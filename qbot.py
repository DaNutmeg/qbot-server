from __future__ import annotations

import json
import uuid
import valkey
import signal
import math
import threading
from dataclasses import dataclass, field
from random import random, choice
from typing import Dict, Hashable, List, Optional, Tuple

from database import OrderType, PositionStatus

@dataclass
class QLearningConfig:
    alpha: float = 0.1  # learning rate
    gamma: float = 0.95  # discount factor
    epsilon: float = 0.1  # exploration rate
    price_bin_size: float = 0.25  # percentage move bin for state discretisation
    stop_loss_pct: float = 0.01  # 1% stop loss
    take_profit_pct: float = 0.02  # 2% take profit

@dataclass
class Position:
    id: str
    direction: int  # +1 for long, -1 for short
    entry_price: float
    stop_loss: float
    take_profit: float

@dataclass
class BotState:
    last_close: Optional[float] = None
    position: Optional[Position] = None
    equity: float = 0.0
    step: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

class QLearningAgent:
    def __init__(self, config = None):
        self.config = config or QLearningConfig()
        self.q_table: Dict[Hashable, Dict[OrderType, float]] = {}

    def _discretise_state(self, last_close: float, current_close: float, position: int):
        if last_close <= 0 or current_close <= 0:
            change_pct = 0.0
        else:
            change_pct = (current_close - last_close) / last_close
        bin_size = self.config.price_bin_size / 100.0
        if bin_size <= 0:
            bin_index = 0
        else:
            bin_index = int(math.floor(change_pct / bin_size))
        # Position is -1 (short), 0 (flat), 1 (long)
        pos_clamped = max(-1, min(1, position))
        return bin_index, pos_clamped

    def _ensure_state(self, state: Hashable):
        if state not in self.q_table:
            self.q_table[state] = {a: 0.0 for a in OrderType}

    def select_action(self, state: Hashable):
        self._ensure_state(state)
        if random() < self.config.epsilon:
            return choice(list(OrderType))
        # greedy action
        action_values = self.q_table[state]
        return max(action_values, key=action_values.get)

    def update(
        self,
        state: Hashable,
        action: OrderType,
        reward: float,
        next_state: Hashable = None,
    ):
        self._ensure_state(state)
        alpha = self.config.alpha
        gamma = self.config.gamma
        current_q = self.q_table[state][action]

        if next_state is None:
            target = reward
        else:
            self._ensure_state(next_state)
            max_next_q = max(self.q_table[next_state].values())
            target = reward + gamma * max_next_q

        self.q_table[state][action] = current_q + alpha * (target - current_q)

def _compute_reward(
    prev_equity: float,
    new_equity: float,
    closed_position: Position = None,
):
    # Reward is PnL delta
    pnl = new_equity - prev_equity

    if closed_position is not None:
        return pnl * 2.0
    return pnl

def run_qbot_worker(
    session_id,
    order_id,
    stock_id,
    valkey_conn_str,
    input_queue,
    output_queue,
    trade_queue,
    initial_equity,
    min_equity,
    max_equity,
    config = None,
    stop_event = None
):
    valkey_session = valkey.Valkey.from_url(
        valkey_conn_str,
        socket_timeout=1800,
        decode_responses=True
    )

    active_equity = initial_equity
    agent = QLearningAgent(config)
    state = BotState(equity=initial_equity)

    if stop_event is None:
        stop_event = threading.Event()

    def _handle_signal(signum, frame) -> None:
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except ValueError:
        pass

    while not stop_event.is_set():
        result = valkey_session.brpop([input_queue], 1)
        if result is None:
            continue

        if valkey_session.get(f"QBOT:ORDER:{session_id}") is None:
            valkey_session.lpush(output_queue,
                json.dumps({"type": "stop"}))
            valkey_session.ltrim(output_queue, 0, 99)
            return

        _, raw_item = result
        try:
            candle = json.loads(raw_item)
        except json.JSONDecodeError:
            continue

        if candle.get("exit") is not None:
            return

        close_price = float(candle.get("close", 0.0))

        with state.lock:
            prev_equity = state.equity
            # Compute current equity given open position.
            if state.position is not None:
                dir_sign = state.position.direction
                position_pnl = (close_price - state.position.entry_price) * dir_sign
                state.equity = initial_equity + position_pnl

            # Derive current and next states.
            if state.last_close is None:
                state.last_close = close_price
                # Not enough history yet; forward candle and continue.
                valkey_session.lpush(output_queue, json.dumps(candle))
                valkey_session.ltrim(output_queue, 0, 99)
                continue

            current_state = agent._discretise_state(
                state.last_close,
                close_price,
                0 if state.position is None else state.position.direction,
            )

            # Select action.
            action = agent.select_action(current_state)

            closed_position: Optional[Position] = None

            # Apply action & risk rules.
            if state.position is None:
                if action == OrderType.BUY:
                    # Open long position.
                    entry = close_price
                    sl = entry * (1.0 - agent.config.stop_loss_pct)
                    tp = entry * (1.0 + agent.config.take_profit_pct)
                    state.position = Position(id=str(uuid.uuid4()), direction=1, entry_price=entry, stop_loss=sl, take_profit=tp)
                    # Emit trade signal.
                    valkey_session.lpush(trade_queue, json.dumps({
                        "type": PositionStatus.OPEN,
                        "output_queue": output_queue,
                        "data": {
                            "id": state.position.id,
                            "account_id": session_id,
                            "stock_id": stock_id,
                            "order_id": order_id,
                            "order_type": OrderType.BUY,
                            "status": PositionStatus.OPEN,
                            "price": entry,
                            "amount": 1.0,
                            "pnl": 0.0
                        }
                    }))
                    valkey_session.ltrim(trade_queue, 0, 99)
                elif action == OrderType.SELL:
                    # Open short position (if your execution layer supports it).
                    entry = close_price
                    sl = entry * (1.0 + agent.config.stop_loss_pct)
                    tp = entry * (1.0 - agent.config.take_profit_pct)
                    state.position = Position(id=str(uuid.uuid4()), direction=-1, entry_price=entry, stop_loss=sl, take_profit=tp)
                    valkey_session.lpush(trade_queue, json.dumps({
                        "type": PositionStatus.OPEN,
                        "output_queue": output_queue,
                        "data": {
                            "id": state.position.id,
                            "account_id": session_id,
                            "stock_id": stock_id,
                            "order_id": order_id,
                            "order_type": OrderType.SELL,
                            "status": PositionStatus.OPEN,
                            "price": entry,
                            "amount": 1.0,
                            "pnl": 0.0
                        }
                    }))
                    valkey_session.ltrim(trade_queue, 0, 99)
                # HOLD means do nothing.
            else:
                # Manage existing position based on price hitting SL/TP or explicit SELL/BUY opposite action.
                pos = state.position
                hit_sl = (pos.direction == 1 and close_price <= pos.stop_loss) or (
                    pos.direction == -1 and close_price >= pos.stop_loss
                )
                hit_tp = (pos.direction == 1 and close_price >= pos.take_profit) or (
                    pos.direction == -1 and close_price <= pos.take_profit
                )
                close_on_signal = (pos.direction == 1 and action == OrderType.SELL) or (
                    pos.direction == -1 and action == OrderType.BUY
                )

                if hit_sl or hit_tp or close_on_signal:
                    position_pnl = (close_price - pos.entry_price) * pos.direction
                    active_equity += position_pnl
                    valkey_session.lpush(trade_queue, json.dumps({
                        "type": PositionStatus.CLOSED,
                        "output_queue": output_queue,
                        "data": {
                            "id": pos.id,
                            "pnl": position_pnl,
                            "equity": active_equity
                        }
                    }))
                    valkey_session.ltrim(trade_queue, 0, 99)
                    closed_position = pos
                    state.position = None

            # Recompute equity after any position change.
            if state.position is not None:
                dir_sign = state.position.direction
                position_pnl = (close_price - state.position.entry_price) * dir_sign
                state.equity = initial_equity + position_pnl
                active_equity += position_pnl
            else:
                hit_min_equity = active_equity <= min_equity
                hit_max_equity = active_equity >= max_equity
                if hit_min_equity or hit_max_equity:
                    valkey_session.lpush(output_queue,
                        json.dumps({"type": "stop"}))
                    valkey_session.ltrim(output_queue, 0, 99)
                    return
                state.equity = state.equity  # unchanged if flat

            reward = _compute_reward(prev_equity, state.equity, closed_position)

            # Next state uses updated last_close.
            next_state = agent._discretise_state(
                state.last_close,
                close_price,
                0 if state.position is None else state.position.direction,
            )

            agent.update(current_state, action, reward, next_state)

            state.last_close = close_price
            state.step += 1

        valkey_session.lpush(output_queue, json.dumps(candle))
        valkey_session.ltrim(output_queue, 0, 99)