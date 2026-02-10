import json
from asyncio import sleep
from datetime import datetime

from database import PositionStatus

async def run_trade_worker(db):
    print("run_trade_worker - Running trade worker")
    async with db.raw_session() as asession, db.valkey_session() as valkey_session:
        ret = None
        while True:
            data = await valkey_session.brpop(["QBOT:TRADE"], 50)
            if data is not None:
                data = json.loads(data[1])

                if data["type"] == PositionStatus.OPEN:
                    ret = await db.add_new_trade(data["data"], asession)
                    continue
                else:
                    ret = await db.update_trade(data["data"], data["type"], asession)

                if ret is not None:
                    await valkey_session.lpush(
                        data["output_queue"], json.dumps(ret))

async def run_chart_data_producer(db, stock_symbol, input_queue, order_key, chart_frequency):
    print(f"run_chart_data_producer - stock_symbol:{stock_symbol} input_queue:{input_queue}")
    query = f"SELECT * FROM {stock_symbol}"
    async with db.valkey_session() as valkey_session, db.stocks_db.execute(query) as cursor:
        async for row in cursor:
            await valkey_session.lpush(input_queue, json.dumps({
                "type": "chart",
                "time": datetime.strptime(row[0], "%Y-%m-%d").timestamp(),
                "open": round(float(row[1]), 6),
                "high": round(float(row[2]), 6),
                "low": round(float(row[3]), 6),
                "close": round(float(row[4]), 6)
            }))
            await sleep(chart_frequency)
            await valkey_session.ltrim(input_queue, 0, 99)
        await valkey_session.lpush(input_queue, json.dumps({"exit": True}))
        await valkey_session.delete(order_key)

async def run_trade_history_producer(db, session_id, order_id, output_queue):
    print(f"run_trade_history_producer - session_id:{session_id} order_id:{order_id}")
    trades = None
    async with db.session() as asession:
        trades = await db.get_trades(asession, session_id, order_id)
    if not trades:
        return

    def normalize(data):
        return {
            "type": "trade_history",
            "time": data.created_at.timestamp(),
            "order_type": data.order_type,
            "amount": data.amount,
            "price": data.price,
            "pnl": data.pnl
        }

    async with db.valkey_session() as valkey_session:
        for trade in map(normalize, trades):
            await valkey_session.lpush(output_queue, json.dumps(trade))