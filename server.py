from fastapi import FastAPI, Body, Cookie, WebSocket
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="QTradingBot - Final Mock Implementation")

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
async def get_account(session_id: Optional[str] = Cookie(None)):
    """Matches the 'This is called on application open' section."""
    return {"balance": 0.0, "currency": "USD"}


@app.post("/api/account/currency")
async def set_account_currency(
    data: CurrencyUpdate,
    session_id: str = Cookie(...)
):
    """Captures {"currency": "NGN"} tied to a session ID."""
    print(f"💰 [KITCHEN]: Session {session_id} updated currency to: {data.currency}")
    return {"balance": 0.0, "currency": data.currency}

# Screenshot 2: GET List of Stocks
@app.get("/api/trade/stocks", response_model=List[Stock])
async def get_stocks(session_id: str = Cookie(None)):
    """Returns the mock stock list (Google, Apple, etc.)"""
    return [
        {"id": 1, "name": "Google", "symbol": "GOOGL"},
        {"id": 2, "name": "Apple", "symbol": "AAPL"}
    ]

# POST Start Trading
@app.post("/api/trade")
async def start_trading(
    trade_data: TradeRequest,
    session_id: str = Cookie(...)
):
    """Executes trade for a specific session."""
    print(f"🚀 [TRADE]: Session {session_id} trading {trade_data.amount} {trade_data.currency}")
    return {"qbot_session_id": "f112dbac091441ad92ce9a8d0251aadf"}

# WebSocket Mocks (Returns 101 Switching Protocols)
@app.get("/api/trade/history")
async def trade_history_mock(session_id: str = Cookie(...)):
    return {"message": f"WebSocket Ready - 101 Switching Protocols {session_id}"}

@app.get("/api/trade/chart")
async def trade_chart_mock(session_id: str = Cookie(...)):
    return {"message": f"Chart Data WebSocket Ready{session_id}"}
