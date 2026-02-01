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

# Screenshot 1 & 5: POST Set Account Currency
@app.post("/api/account/currency")
async def set_account_currency(data: CurrencyUpdate):
    """
    Captures the {"currency": "NGN"} example.
    Check your terminal to see the capture!
    """
    print(f"💰 [KITCHEN LOG]: Setting currency to: {data.currency}")
    return {"balance": 0.0, "currency": data.currency}

# Screenshot 2: GET List of Stocks
@app.get("/api/trade/stocks", response_model=List[Stock])
async def get_stocks(session_id: str = Cookie(None)):
    """Returns the mock stock list (Google, Apple, etc.)"""
    return [
        {"id": 1, "name": "Google", "symbol": "GOOGL"},
        {"id": 2, "name": "Apple", "symbol": "AAPL"}
    ]

# Screenshot 3: POST Start Trading
@app.post("/api/trade")
async def start_trading(trade_data: TradeRequest):
    """
    Captures all trade parameters (amount, stop loss, etc.)
    Returns the exact mock session ID from your screenshot.
    """
    print(f"🚀 [KITCHEN LOG]: Trading {trade_data.amount} on {trade_data.currency}")
    return {"qbot_session_id": "f112dbac091441ad92ce9a8d0251aadf"}

# Screenshot 3 & 4: WebSocket Mocks (Returns 101 Switching Protocols)
@app.get("/api/trade/history")
async def trade_history_mock():
    return {"message": "WebSocket Ready - 101 Switching Protocols"}

@app.get("/api/trade/chart")
async def trade_chart_mock():
    return {"message": "Chart Data WebSocket Ready"}
