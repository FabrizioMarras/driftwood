"""Paper trade execution engine for the Driftwood trading system."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

# Ensure project-root imports work when this file is run directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import get_config, is_paper_trading
from data.database import SystemEvent, Trade, get_engine, init_db
from data.kraken_client import fetch_ohlcv
from risk.risk_engine import RiskState, evaluate_trade, update_risk_state
from signals.indicators import compute_all_indicators, compute_trend_signal


def get_current_price(symbol: str) -> float:
    """Fetch the latest 1h candle for a symbol and return its close price."""
    try:
        df = fetch_ohlcv(symbol, "1h", limit=2)
        if df.empty:
            raise ValueError(f"No OHLCV data returned for {symbol} on 1h timeframe.")
        return float(df["close"].iloc[-1])
    except Exception as exc:
        raise RuntimeError(f"Failed to get current price for {symbol}: {exc}") from exc


def open_paper_trade(
    symbol: str, quantity: float, entry_price: float, reason: str, mode: str
) -> int:
    """Create an open trade record and return the new trade ID."""
    config = get_config()
    fee_rate = float(config.get("exchange", {}).get("fee_rate", 0.0))
    fees = entry_price * quantity * fee_rate

    engine = get_engine()
    Session = sessionmaker(bind=engine, future=True)

    with Session() as session:
        trade = Trade(
            symbol=symbol,
            side="buy",
            entry_price=entry_price,
            exit_price=None,
            quantity=quantity,
            entry_time=datetime.now(timezone.utc),
            exit_time=None,
            pnl=None,
            fees=fees,
            status="open",
            reason=reason,
            mode=mode,
        )
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return int(trade.id)


def close_paper_trade(trade_id: int, exit_price: float) -> Dict[str, Any]:
    """Close an open trade, compute realized PnL, and return a summary."""
    config = get_config()
    fee_rate = float(config.get("exchange", {}).get("fee_rate", 0.0))

    engine = get_engine()
    Session = sessionmaker(bind=engine, future=True)

    with Session() as session:
        trade = session.get(Trade, trade_id)
        if trade is None or trade.status != "open":
            raise ValueError(f"Open trade with id={trade_id} not found.")

        # Include both entry and exit fees for realized PnL.
        exit_fee = exit_price * float(trade.quantity) * fee_rate
        total_fees = float(trade.fees) + exit_fee
        pnl = (exit_price - float(trade.entry_price)) * float(trade.quantity) - total_fees

        trade.exit_price = float(exit_price)
        trade.exit_time = datetime.now(timezone.utc)
        trade.pnl = float(pnl)
        trade.fees = float(total_fees)
        trade.status = "closed"
        session.commit()

        return {
            "trade_id": int(trade.id),
            "symbol": trade.symbol,
            "entry_price": float(trade.entry_price),
            "exit_price": float(trade.exit_price),
            "quantity": float(trade.quantity),
            "pnl": float(trade.pnl),
            "fees": float(trade.fees),
            "status": trade.status,
        }


def check_exit_conditions(
    trade: Trade, current_price: float, signal: str | None = None
) -> Dict[str, Any]:
    """Evaluate stop loss, take profit, or trend-reversal exit rules."""
    entry_price = float(trade.entry_price)
    stop_loss_price = entry_price * 0.97
    take_profit_price = entry_price * 1.06

    if current_price <= stop_loss_price:
        return {
            "should_exit": True,
            "reason": "Stop loss triggered (price fell more than 3% below entry).",
        }

    if current_price >= take_profit_price:
        return {
            "should_exit": True,
            "reason": "Take profit triggered (price rose more than 6% above entry).",
        }

    if (signal or "").lower() == "sell":
        return {
            "should_exit": True,
            "reason": "Trend reversal triggered (signal is sell).",
        }

    return {"should_exit": False, "reason": "No exit condition met."}


def log_system_event(event_type: str, message: str, details: Dict[str, Any] | None) -> None:
    """Persist a system event row with JSON-serialized details."""
    engine = get_engine()
    Session = sessionmaker(bind=engine, future=True)

    with Session() as session:
        event = SystemEvent(
            event_type=event_type,
            message=message,
            timestamp=datetime.now(timezone.utc),
            details=json.dumps(details or {}),
        )
        session.add(event)
        session.commit()


def _fetch_enriched_timeframes(symbol: str) -> Dict[str, Any]:
    """Fetch raw candles and compute indicators for daily/4h/1h views."""
    config = get_config()
    tf_cfg = config.get("trading", {}).get("timeframes", {})
    tf_daily = str(tf_cfg.get("trend_filter", "1d"))
    tf_four_hour = str(tf_cfg.get("signal", "4h"))
    tf_one_hour = str(tf_cfg.get("entry", "1h"))

    daily_df = compute_all_indicators(fetch_ohlcv(symbol, tf_daily), "daily")
    four_hour_df = compute_all_indicators(fetch_ohlcv(symbol, tf_four_hour), "four_hour")
    one_hour_df = compute_all_indicators(fetch_ohlcv(symbol, tf_one_hour), "one_hour")

    return {
        "daily": daily_df,
        "four_hour": four_hour_df,
        "one_hour": one_hour_df,
    }


def run_trading_cycle(risk_state: RiskState) -> RiskState:
    """Run one full trading cycle: signals, entries, exits, and event logging."""
    config = get_config()
    pairs: List[str] = list(config.get("trading", {}).get("pairs", []))
    mode = "paper" if is_paper_trading() else "live"

    cycle_signals: Dict[str, Dict[str, Any]] = {}

    # 1) Fetch OHLCV + compute indicators and signals for each configured pair.
    for symbol in pairs:
        try:
            enriched = _fetch_enriched_timeframes(symbol)
            signal = compute_trend_signal(
                enriched["daily"], enriched["four_hour"], enriched["one_hour"]
            )
            cycle_signals[symbol] = signal
        except Exception as exc:
            log_system_event(
                "error",
                f"Failed signal computation for {symbol}",
                {"symbol": symbol, "error": str(exc)},
            )

    # 2) Evaluate entries from signals.
    for symbol, signal_data in cycle_signals.items():
        try:
            current_price = get_current_price(symbol)
            decision = evaluate_trade(symbol, current_price, signal_data["signal"], risk_state)

            if decision["approved"]:
                trade_id = open_paper_trade(
                    symbol=symbol,
                    quantity=float(decision["quantity"]),
                    entry_price=current_price,
                    reason=str(decision["reason"]),
                    mode=mode,
                )
                risk_state = update_risk_state(
                    risk_state, trade_pnl=0.0, new_trade_opened=True, trade_closed=False
                )
                log_system_event(
                    "trade_opened",
                    f"Opened {mode} trade for {symbol}",
                    {
                        "trade_id": trade_id,
                        "symbol": symbol,
                        "quantity": decision["quantity"],
                        "entry_price": current_price,
                        "signal": signal_data,
                    },
                )
            else:
                log_system_event(
                    "trade_skipped",
                    f"Trade not approved for {symbol}",
                    {"symbol": symbol, "reason": decision["reason"], "signal": signal_data},
                )
        except Exception as exc:
            log_system_event(
                "error",
                f"Entry evaluation failed for {symbol}",
                {"symbol": symbol, "error": str(exc)},
            )

    # 3) Evaluate exits for all currently open trades.
    engine = get_engine()
    Session = sessionmaker(bind=engine, future=True)
    with Session() as session:
        open_trades = session.execute(
            select(Trade).where(Trade.status == "open")
        ).scalars().all()

    for trade in open_trades:
        try:
            current_price = get_current_price(trade.symbol)
            latest_signal = cycle_signals.get(trade.symbol, {}).get("signal", "hold")
            exit_check = check_exit_conditions(trade, current_price, signal=latest_signal)

            if exit_check["should_exit"]:
                summary = close_paper_trade(int(trade.id), current_price)
                risk_state = update_risk_state(
                    risk_state,
                    trade_pnl=float(summary["pnl"]),
                    new_trade_opened=False,
                    trade_closed=True,
                )
                log_system_event(
                    "trade_closed",
                    f"Closed trade {trade.id} for {trade.symbol}",
                    {"trade": summary, "exit_reason": exit_check["reason"]},
                )
        except Exception as exc:
            log_system_event(
                "error",
                f"Exit evaluation failed for trade {trade.id}",
                {"trade_id": int(trade.id), "error": str(exc)},
            )

    return risk_state


if __name__ == "__main__":
    init_db()
    cfg = get_config()
    initial_capital = float(cfg.get("portfolio", {}).get("initial_capital_usd", 1000.0))

    state = RiskState(
        portfolio_value=initial_capital,
        daily_start_value=initial_capital,
        peak_value=initial_capital,
        open_trades=0,
        daily_loss=0.0,
        trading_halted=False,
        halt_reason="",
    )

    updated_state = run_trading_cycle(state)

    print("Trading cycle completed.")
    print(f"- Portfolio value: {updated_state.portfolio_value:.2f}")
    print(f"- Daily loss: {updated_state.daily_loss:.2f}")
    print(f"- Open trades: {updated_state.open_trades}")
    print(f"- Trading halted: {updated_state.trading_halted}")
    if updated_state.halt_reason:
        print(f"- Halt reason: {updated_state.halt_reason}")
