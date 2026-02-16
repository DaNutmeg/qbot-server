import uuid
import os
import json
from contextlib import asynccontextmanager
from enum import Enum
from dotenv import load_dotenv
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import Integer, Double, String, ForeignKey, select, update, delete
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
import aiosqlite
import valkey.asyncio as valkey

load_dotenv()

DB_USER = os.getenv("DB_USER", "qbot")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "qbot")

DB_CONN_STRING = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"
VALKEY_CONN_STRING = os.getenv("VALKEY_CONN_STRING")

class Currency(str, Enum):
    NAIRA = "NGN"
    DOLLAR = "USD"

class OrderType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"

class Status(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"

class Base(DeclarativeBase):
    pass

class Accounts(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    balance: Mapped[float] = mapped_column(Double, default=50000.0)
    currency: Mapped[Currency] = mapped_column(String, default=Currency.DOLLAR)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow)

class Stocks(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)

class Trades(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    order_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    order_type: Mapped[OrderType] = mapped_column(nullable=False)
    status: Mapped[PositionStatus] = mapped_column(nullable=False)
    amount: Mapped[float] = mapped_column(default=1.0)
    price: Mapped[float] = mapped_column(default=0.0)
    pnl: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow)

class Database():

    def __init__(self):
        self.db_engine = None
        self.stocks_db = None
        self.valkey_conn_str = VALKEY_CONN_STRING

    async def init(self):
       self.db_engine = create_async_engine(DB_CONN_STRING)
       async with self.db_engine.begin() as conn:
           await conn.run_sync(Base.metadata.create_all)
       self.stocks_db = await aiosqlite.connect(os.getenv("STOCKS_DB"))

    async def session(self):
        async_session = async_sessionmaker(self.db_engine, expire_on_commit=False)
        async with async_session() as asession:
            async with asession.begin():
                yield asession

    @asynccontextmanager
    async def raw_session(self):
        async_session = async_sessionmaker(self.db_engine, expire_on_commit=False)
        async with async_session() as asession:
            yield asession

    @asynccontextmanager
    async def valkey_session(self):
        async_session = valkey.Valkey.from_url(
            self.valkey_conn_str,
            decode_responses=True,
            socket_timeout=1800
        )
        try:
            yield async_session
        finally:
            await async_session.close()

    async def close(self):
        await self.db_engine.dispose()
        await self.stocks_db.close()

    async def get_account_session(self, session_id, asession):
        account = None
        if session_id:
            account = await asession.execute(
                select(Accounts)
                .where(Accounts.id == uuid.UUID(session_id))
            )
            account = account.scalars().first()
        return account

    async def add_new_trade(self, data, asession, valkey_session):
        """
        async with asession.begin():
            values = {
                **data,
                "id": uuid.UUID(data["id"]),
                "account_id": uuid.UUID(data["account_id"]),
                "order_id": uuid.UUID(data["order_id"])
            }
            asession.add(Trades(**values))
            trade = await asession.get(Trades, uuid.UUID(data["id"]))
        """
        values = {
            **data,
            "created_at": datetime.utcnow().timestamp(),
            "updated_at": datetime.utcnow().timestamp()
        }

        await valkey_session.set(
            f"QBOT:TRADE:{data["id"]}",
            json.dumps(values),
            ex=60
        )
        #print(f"add_new_trade - Added new trade {values}")
        return {
            "type": "trade",
            "time": values["created_at"],
            "order_type": values["order_type"],
            "info": f"{values["order_type"]} @ {round(values["price"], 2)}"
        }

    async def update_trade(self, data, type, asession, valkey_session):
        async with asession.begin():
            if type == PositionStatus.CLOSED:
                """
                await asession.execute(
                    update(Trades)
                    .values(
                        status=PositionStatus.CLOSED,
                        pnl=data["pnl"],
                        amount=data["equity"]
                    )
                    .where(Trades.id == uuid.UUID(data["id"]))
                )
                trade = await asession.get(Trades, uuid.UUID(data["id"]))
                """
                trade = await valkey_session.get(f"QBOT:TRADE:{data["id"]}")
                if trade is None:
                    return None
                await valkey_session.delete(f"QBOT:TRADE:{data["id"]}")

                trade = json.loads(trade)
                trade["pnl"] = data["pnl"]
                trade["amount"] = data["equity"]

                return {
                    "type": "trade_history",
                    "time": trade["created_at"],
                    "order_type": trade["order_type"],
                    "amount": trade["amount"],
                    "price": trade["price"],
                    "pnl": trade["pnl"]
                }
        return None

    async def get_trades(self, session_id, order_id, asession):
        trades = await asession.execute(
            select(Trades)
            .where(Accounts.id == uuid.UUID(session_id))
            .where(Trades.order_id == uuid.UUID(order_id))
            .where(Trades.status == PositionStatus.CLOSED)
        )
        return trades.scalars()

    async def delete_old_trades(self, session_id, asession):
        await asession.execute(
            delete(Trades)
            .where(Trades.account_id == uuid.UUID(session_id))
        )

