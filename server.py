from contextlib import asynccontextmanager
from fastapi import (
    FastAPI,
    Body,
    Cookie,
    WebSocket,
    Depends,
    Response
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

import uuid
from database import (
    Accounts,
    Stocks,
    Trades,
    Database,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

db = Database()

origins = [
    "http://127.0.0.1",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "https://qbot.mooo.com",
]

@asynccontextmanager
async def handle_lifespan(server: FastAPI):
    global db
    await db.init()
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
    currency: str
    amount: int
    stop_loss: int
    take_profit: int
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
    trade_data: TradeRequest,
    session_id: str = Cookie(),
    asession: AsyncSession = Depends(db.session)
):
    """Executes trade for a specific session."""
    print(f"🚀 [TRADE]: Session {session_id} trading {trade_data.amount} {trade_data.currency}")
    return {"qbot_session_id": "f112dbac091441ad92ce9a8d0251aadf"}

# WebSocket Mocks (Returns 101 Switching Protocols)
@app.get("/api/trade/history")
async def trade_history_mock(
    session_id: str = Cookie(),
    asession: AsyncSession = Depends(db.session)
):
    return {"message": f"WebSocket Ready - 101 Switching Protocols {session_id}"}

@app.get("/api/trade/chart")
async def trade_chart_mock(
    session_id: str = Cookie(),
    asession: AsyncSession = Depends(db.session)
):
    return {"message": f"Chart Data WebSocket Ready{session_id}"}
