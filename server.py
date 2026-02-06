import uuid
from asyncio import create_task, to_thread
from contextlib import asynccontextmanager
from fastapi import (
    FastAPI,
    Body,
    Cookie,
    WebSocket,
    Depends,
    Response,
    WebSocket,
    HTTPException
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from database import (
    Accounts,
    Orders,
    Stocks,
    Trades,
    Database,
    ENUM_PENDING,
    ENUM_RUNNING
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from qbot import (
    send_chart_data,
    run_trade_worker,
    run_qbot,
    QLearningConfig
)
import valkey.asyncio as async_valkey

db = Database()

origins = [
    "http://127.0.0.1",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "https://qbot.mooo.com"
]

@asynccontextmanager
async def handle_lifespan(server: FastAPI):
    global db
    await db.init()
    create_task(run_trade_worker(db))
    yield

app = FastAPI(
    lifespan=handle_lifespan,
    title="QTradingBot - Final Mock Implementation"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
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
        session_id = str(uuid.uuid4())
        asession.add(Accounts(id=uuid.UUID(session_id)))
        account = await asession.get(Accounts, uuid.UUID(session_id))
        response.set_cookie(
            key="session_id", 
            value=str(account.id),
            secure=True,
            samesite="none"
        )
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
    if account.currency == "USD" and data.currency == "NGN":
        account.balance *= 1500
        account.currency = data.currency
    elif account.currency == "NGN" and data.currency == "USD":
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
    if account.currency == 'NGN':
        data.amount *= 1500
    if data.amount > account.balance:
        raise HTTPException(status_code=400, detail="Amount is greater than balance")
    account.balance -= data.amount
    stock = await asession.execute(select(Stocks).where(Stocks.symbol == data.stock_symbol))
    stock = stock.scalars().first()
    if not stock:
        raise HTTPException(status_code=400, detail="Invalid stock symbol")
    asession.add(Orders(
        account_id=account.id,
        stock_id=stock.id,
        stop_loss=data.stop_loss,
        take_profit=data.take_profit,
        amount=data.amount,
        duration=data.duration,
        status=ENUM_PENDING
    ))

    return {"balance": account.balance, "currency": account.currency}

@app.websocket("/api/trade/chart")
async def live_data_handler(
    websocket: WebSocket,
    session_id: str = Cookie(),
    asession: AsyncSession = Depends(db.session)
):
    consumer_session, producer_session = None, None
    try:
        await websocket.accept()
        account = await db.get_account_session(session_id, asession)
        if not account:
            raise HTTPException(status_code=400, detail="Invalid account")
        order = await asession.execute(
            select(Orders)
            .where(Accounts.id == account.id)
            .where(Orders.status == ENUM_PENDING)
        )
        order = order.scalars().first()
    
        stock = await asession.execute(
            select(Stocks).where(Stocks.id == order.stock_id)
        )
        stock = stock.scalars().first()

        consumer_session = await db.valkey_session()
        producer_session = await db.valkey_session()
    
        input_queue = f"QBOT:INPUT:{session_id}"
        output_queue = f"QBOT:OUTPUT:{session_id}"

        create_task(to_thread(
            run_qbot,
            session_id,
            db.valkey_conn_str,
            f"QBOT:INPUT:{session_id}",
            f"QBOT:OUTPUT:{session_id}",
            "QBOT:TRADE",
            order.amount,
            QLearningConfig()
        ))
    
        create_task(send_chart_data(
            stock.symbol, 
            db.stocks_db, 
            producer_session,
            input_queue
        ))
    
        print(f"live_data_handler - Running consumer loop")
        while True:
            _, data = await consumer_session.brpop([output_queue], timeout=600)
            if data:
                await websocket.send_text(data)
    except Exception:
        if consumer_session:
            await consumer_session.aclose()
        if producer_session:
            await producer_session.aclose()

