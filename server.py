import uuid
import json
import os
from dotenv import load_dotenv
from asyncio import create_task, to_thread
from contextlib import asynccontextmanager
from fastapi import (
    FastAPI,
    Body,
    Cookie,
    WebSocket,
    Depends,
    Response,
    HTTPException
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from database import (
    Currency,
    Status,
    Accounts,
    Stocks,
    Trades,
    Database
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from qbot import run_qbot_worker
from workers import run_trade_worker, run_chart_data_producer, run_trade_history_producer

load_dotenv()

db = Database()

CHART_FREQUENCY = 1

@asynccontextmanager
async def handle_lifespan(server: FastAPI):
    await db.init()
    async with db.valkey_session() as valkey_session:
        async for key in valkey_session.scan_iter("QBOT:*"):
            await valkey_session.delete(key)
    create_task(run_trade_worker(db))
    yield

app = FastAPI(
    lifespan=handle_lifespan,
    title="QTradingBot"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ORIGINS", "https://qbot.mooo.com").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# --- 1. DATA MODELS (The "Order Forms") ---

class AccountResponse(BaseModel):
    balance: float
    currency: str

class CurrencyUpdate(BaseModel):
    currency: str

class Stock(BaseModel):
    id: int
    name: str
    symbol: str

class TradeRequest(BaseModel):
    stock_symbol: str
    amount: float
    stop_loss: float
    take_profit: float
    duration: str

# --- 2. ENDPOINTS (The "Service Windows") ---

# Screenshot 1: GET Account Details
@app.get("/api/account", response_model=AccountResponse)
async def get_account(
    response: Response,
    session_id: Optional[str] = Cookie(None),
    asession: AsyncSession = Depends(db.session)
):
    """Matches the 'This is called on application open' section."""
    account = await db.get_account_session(session_id, asession)
    if not account:
        total_accounts = await asession.execute(
            select(func.count())
            .select_from(Accounts)
        )
        total_accounts = total_accounts.scalar()

        session_id = str(uuid.uuid4())
        if total_accounts >= 200: # Create no more than 200 accounts
            account = await asession.execute(
                select(Accounts)
                .order_by(Accounts.updated_at)
                .limit(1)
            )
            account = account.scalar()
            await asession.execute(
                update(Accounts)
                .values(
                    id=uuid.UUID(session_id),
                    balance=Accounts.__table__.c.balance.default.arg,
                    currency=Accounts.__table__.c.currency.default.arg
                )
                .where(Accounts.id == account.id)
            )
        else:
            asession.add(Accounts(id=uuid.UUID(session_id)))
            
        account = await asession.get(Accounts, uuid.UUID(session_id))

        response.set_cookie(
            key="session_id", 
            value=str(account.id),
            secure=True,
            samesite="none"
        )
    # Reset the balance if it goes below 1000 dollars
    if account.currency == Currency.NAIRA:
        if account.balance <= (1000*1500):
            account.balance = 50000*1500
    else:
        if account.balance <= 1000:
            account.balance = 50000
    return {"balance": account.balance, "currency": account.currency}

# Screenshot 1 & 5: POST Set Account Currency
@app.post("/api/account/currency")
async def set_account_currency(
    data: CurrencyUpdate,
    session_id: str = Cookie(),
    asession: AsyncSession = Depends(db.session)
):
    """
    Captures the {"currency": "NGN"} example.
    Check your terminal to see the capture!
    """
    account = await db.get_account_session(session_id, asession)
    if account.currency == Currency.DOLLAR and data.currency == Currency.NAIRA:
        account.balance *= 1500
        account.currency = data.currency
    elif account.currency == Currency.NAIRA and data.currency == Currency.DOLLAR:
        account.balance /= 1500
        account.currency = data.currency
    else:
        account.currency = data.currency
    return {"balance": account.balance, "currency": account.currency}

# Screenshot 2: GET List of Stocks
@app.get("/api/trade/stocks", response_model=List[Stock])
async def get_stocks(
    session_id: Optional[str] = Cookie(None),
    asession: AsyncSession = Depends(db.session)
):
    """Returns the mock stock list (Google, Apple, etc.)"""
    query = select(Stocks).order_by(Stocks.id).limit(5)
    return [{
        "id": stock.id,
        "name": stock.name,
        "symbol": stock.symbol
    } for stock in (await asession.execute(query)).scalars()]

# POST Start Trading
@app.post("/api/trade")
async def start_trading(
    data: TradeRequest,
    session_id: str = Cookie(),
    asession: AsyncSession = Depends(db.session)
):
    """Executes trade for a specific session."""
    account = await db.get_account_session(session_id, asession)
    if account.currency == Currency.NAIRA:
        data.amount *= 1500
    if data.amount > account.balance:
        raise HTTPException(status_code=400, detail="Amount is greater than balance")
    if data.stop_loss >= data.amount:
        raise HTTPException(status_code=400, detail="Amount is equal or less than stop loss")
    if data.take_profit <= data.amount:
        raise HTTPException(status_code=400, detail="Amount is equal or greater than take profit")
    stock = await asession.execute(
        select(Stocks)
        .where(Stocks.symbol == data.stock_symbol)
    )
    stock = stock.scalars().first()
    if not stock:
        raise HTTPException(status_code=400, detail="Invalid stock symbol")

    async with db.valkey_session() as valkey_session:
        if await valkey_session.get(f"QBOT:ORDER:{session_id}") is not None:
            return {
                "balance": account.balance,
                "currency": account.currency,
                "detail": "Bot Already Running"
            }
    
        await valkey_session.set(
            f"QBOT:ORDER:{session_id}",
            json.dumps({
                "order_id": str(uuid.uuid4()),
                "account_id": str(account.id),
                "stock_id": stock.id,
                "stock_name": stock.name,
                "stock_symbol": stock.symbol,
                "stop_loss": data.stop_loss,
                "take_profit": data.take_profit,
                "amount": data.amount,
                "duration": data.duration,
                "status": Status.PENDING
            })
        )

        await db.delete_old_trades(session_id, asession)

    account.balance -= data.amount
    return {"balance": account.balance, "currency": account.currency}

@app.websocket("/api/trade/chart")
async def live_data_handler(
    websocket: WebSocket,
    session_id: str = Cookie(),
    asession: AsyncSession = Depends(db.session)
):
    await websocket.accept()

    account = await db.get_account_session(session_id, asession)
    if not account:
        raise HTTPException(status_code=400, detail="Invalid account")

    output_queue = f"QBOT:OUTPUT:{session_id}"
    input_queue = f"QBOT:INPUT:{session_id}"

    tasks = []

    async with db.valkey_session() as valkey_session:
        order = await valkey_session.get(f"QBOT:ORDER:{session_id}")
        if order is None:
            raise HTTPException(status_code=403)
        order = json.loads(order)

        await websocket.send_json({
            "type": "trade_info",
            "stock_name": f"{order["stock_name"]} - {order["stock_symbol"]}",
            "stock_symbol": order["stock_symbol"],
            "amount": order["amount"],
            "stop_loss": order["stop_loss"],
            "take_profit": order["take_profit"],
            "duration": order["duration"],
            "currency": account.currency
        })

        if order["status"] == Status.PENDING:
            await valkey_session.set(
                f"QBOT:ORDER:{session_id}",
                json.dumps({**order, "status": Status.RUNNING})
            )
            create_task(to_thread(
                run_qbot_worker,
                session_id,
                order["order_id"],
                order["stock_id"],
                db.valkey_conn_str,
                input_queue,
                output_queue,
                "QBOT:TRADE",
                order["amount"],
                order["stop_loss"],
                order["take_profit"],
            ))
    
            create_task(run_chart_data_producer(
                db,
                order["stock_symbol"], 
                input_queue,
                f"QBOT:ORDER:{session_id}",
                CHART_FREQUENCY
            ))
        elif order["status"] == Status.RUNNING:
            create_task(run_trade_history_producer(
                db,
                session_id,
                order["order_id"],
                output_queue
            ))
    
        print(f"live_data_handler - Running consumer loop")
        try:
            while True:
                data = await valkey_session.brpop([output_queue], 5)
                if data is not None:
                    _, data = data
                    if len(data) < 20:
                        break
                    print(f"data:{data}")
                    await websocket.send_text(data)
        except Exception:
            return
    
        for task in tasks:
            task.cancel()