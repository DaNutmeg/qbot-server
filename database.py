import uuid
import enum
import os
from dotenv import load_dotenv
from datetime import datetime
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession
)
from sqlalchemy import Integer, Double, String, ForeignKey, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column
)
import aiosqlite
import valkey.asyncio as valkey

ENUM_NAIRA = "NGN"
ENUM_DOLLAR = "USD"

ENUM_SELL = "SELL"
ENUM_BUY = "BUY"

ENUM_OPEN = "OPEN"
ENUM_CLOSED = "CLOSED"

ENUM_PENDING = "PENDING"
ENUM_RUNNING = "RUNNING"

class Base(DeclarativeBase):
    pass

class Accounts(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    balance: Mapped[float] = mapped_column(Double, default=50000.0)
    currency: Mapped[str] = mapped_column(String, default=ENUM_DOLLAR)

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
    order_type: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False)
    amount: Mapped[float] = mapped_column(default=1.0)
    price: Mapped[float] = mapped_column(default=0.0)
    pnl: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

class Orders(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    stop_loss: Mapped[float] = mapped_column(default=200.0)
    take_profit: Mapped[float] = mapped_column(default=800.0)
    amount: Mapped[float] = mapped_column(default=0.0)
    duration: Mapped[str] = mapped_column(default="15m")
    status: Mapped[str] = mapped_column(nullable=False)

load_dotenv()

DB_USER = os.getenv("DB_USER", "qbot")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "qbot")

DB_CONN_STRING = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"
VALKEY_DB_CONN_STRING = f"valkey://{os.getenv("VALKEY_HOST")}"
VALKEY_DB_CONN_STRING = f"{os.getenv("VALKEY_HOST")}"

class Database():

    def __init__(self):
        self.db_engine = None
        self.stocks_db = None
        self.valkey_conn_str = VALKEY_DB_CONN_STRING

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

    async def valkey_session(self):
        return valkey.Valkey.from_url(self.valkey_conn_str, socket_timeout=1800, decode_responses=True)

    async def close(self):
        await self.db_engine.dispose()
        await self.stocks_db.close()

    async def get_account_session(self, session_id: str | None, asession: AsyncSession):
        account = None
        if session_id:
            account = await asession.execute(select(Accounts).where(Accounts.id == uuid.UUID(session_id)))
            account = account.scalars().first()
        return account

    async def add_new_trade(self, data, session):
        session.add(Trades(
            id=uuid.uuid4(),
            **data
        ))
        print(f"add_new_trade - Added new trade {data}")
