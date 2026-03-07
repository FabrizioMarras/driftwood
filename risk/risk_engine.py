"""Risk management engine for the Driftwood trading system."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Dict

# Ensure project-root imports work when this file is run directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import get_config, is_paper_trading


@dataclass
class RiskState:
    """Tracks current portfolio risk state used for trade decisions."""

    portfolio_value: float
    daily_start_value: float
    peak_value: float
    open_trades: int
    daily_loss: float
    trading_halted: bool
    halt_reason: str


def compute_position_size(symbol: str, price: float, risk_state: RiskState) -> Dict[str, object]:
    """Compute position size from configured risk limits for a symbol/price."""
    _ = symbol  # Kept for API completeness and future per-symbol sizing logic.
    config = get_config()
    risk_cfg = config.get("risk", {})

    max_position_size_pct = float(risk_cfg.get("max_position_size_pct", 0.0))
    min_trade_size_usd = float(risk_cfg.get("min_trade_size_usd", 0.0))

    max_position_value = risk_state.portfolio_value * max_position_size_pct
    quantity = max_position_value / price if price > 0 else 0.0

    if max_position_value < min_trade_size_usd:
        return {
            "allowed": False,
            "quantity": quantity,
            "position_value_usd": max_position_value,
            "reason": (
                f"Position value ${max_position_value:.2f} is below minimum trade size "
                f"${min_trade_size_usd:.2f}."
            ),
        }

    return {
        "allowed": True,
        "quantity": quantity,
        "position_value_usd": max_position_value,
        "reason": (
            f"Position sized at {max_position_size_pct:.2%} of portfolio "
            f"(${max_position_value:.2f})."
        ),
    }


def check_risk_gates(risk_state: RiskState) -> Dict[str, object]:
    """Evaluate risk gates and decide whether new trades are permitted."""
    config = get_config()
    risk_cfg = config.get("risk", {})
    trading_cfg = config.get("trading", {})

    max_daily_loss_pct = float(risk_cfg.get("max_daily_loss_pct", 1.0))
    max_drawdown_pct = float(risk_cfg.get("max_drawdown_pct", 1.0))
    max_open_trades = int(trading_cfg.get("max_open_trades", 0))

    # If already halted, preserve halt state and block trading.
    if risk_state.trading_halted:
        return {
            "allowed": False,
            "halt": True,
            "reason": f"Trading already halted: {risk_state.halt_reason or 'no reason provided'}.",
        }

    daily_loss_ratio = (
        risk_state.daily_loss / risk_state.daily_start_value
        if risk_state.daily_start_value > 0
        else 0.0
    )
    if daily_loss_ratio >= max_daily_loss_pct:
        reason = (
            f"Daily loss limit breached ({daily_loss_ratio:.2%} >= {max_daily_loss_pct:.2%})."
        )
        return {"allowed": False, "halt": True, "reason": reason}

    drawdown_ratio = (
        (risk_state.peak_value - risk_state.portfolio_value) / risk_state.peak_value
        if risk_state.peak_value > 0
        else 0.0
    )
    if drawdown_ratio >= max_drawdown_pct:
        reason = (
            f"Max drawdown limit breached ({drawdown_ratio:.2%} >= {max_drawdown_pct:.2%})."
        )
        return {"allowed": False, "halt": True, "reason": reason}

    if risk_state.open_trades >= max_open_trades:
        return {
            "allowed": False,
            "halt": False,
            "reason": (
                f"Max open trades reached ({risk_state.open_trades}/{max_open_trades}); "
                "new trade blocked."
            ),
        }

    return {"allowed": True, "halt": False, "reason": "All risk gates passed."}


def evaluate_trade(
    symbol: str, price: float, signal: str, risk_state: RiskState
) -> Dict[str, object]:
    """Evaluate whether a new trade should be approved under current risk state."""
    gate_result = check_risk_gates(risk_state)
    if not gate_result["allowed"]:
        if gate_result["halt"]:
            risk_state.trading_halted = True
            risk_state.halt_reason = str(gate_result["reason"])
        return {
            "approved": False,
            "symbol": symbol,
            "quantity": 0.0,
            "position_value_usd": 0.0,
            "reason": str(gate_result["reason"]),
        }

    if signal.lower() != "buy":
        return {
            "approved": False,
            "symbol": symbol,
            "quantity": 0.0,
            "position_value_usd": 0.0,
            "reason": f"Signal is '{signal}', so no long entry is approved.",
        }

    sizing = compute_position_size(symbol, price, risk_state)
    if not sizing["allowed"]:
        return {
            "approved": False,
            "symbol": symbol,
            "quantity": float(sizing["quantity"]),
            "position_value_usd": float(sizing["position_value_usd"]),
            "reason": str(sizing["reason"]),
        }

    mode = "paper" if is_paper_trading() else "live"
    return {
        "approved": True,
        "symbol": symbol,
        "quantity": float(sizing["quantity"]),
        "position_value_usd": float(sizing["position_value_usd"]),
        "reason": f"{gate_result['reason']} {sizing['reason']} Mode: {mode}.",
    }


def update_risk_state(
    risk_state: RiskState, trade_pnl: float, new_trade_opened: bool, trade_closed: bool
) -> RiskState:
    """Update and return risk state after trade lifecycle and PnL changes."""
    # Realized PnL impacts current portfolio value.
    risk_state.portfolio_value += float(trade_pnl)

    # Track daily losses only when PnL is negative.
    if trade_pnl < 0:
        risk_state.daily_loss += abs(float(trade_pnl))

    # Keep the all-time peak portfolio value updated.
    if risk_state.portfolio_value > risk_state.peak_value:
        risk_state.peak_value = risk_state.portfolio_value

    # Maintain currently open trade count.
    if new_trade_opened:
        risk_state.open_trades += 1
    if trade_closed:
        risk_state.open_trades = max(0, risk_state.open_trades - 1)

    return risk_state


if __name__ == "__main__":
    cfg = get_config()
    initial_capital = float(cfg.get("portfolio", {}).get("initial_capital_usd", 1000.0))

    sample_state = RiskState(
        portfolio_value=initial_capital,
        daily_start_value=initial_capital,
        peak_value=initial_capital,
        open_trades=0,
        daily_loss=0.0,
        trading_halted=False,
        halt_reason="",
    )

    print("=== Risk Engine Manual Test ===")
    print()

    buy_result = evaluate_trade("BTC/USD", 50000.0, "buy", sample_state)
    print("Buy signal evaluation (BTC/USD @ $50,000):")
    print(buy_result)
    print()

    hold_result = evaluate_trade("BTC/USD", 50000.0, "hold", sample_state)
    print("Hold signal evaluation:")
    print(hold_result)
    print()

    loss_limit_state = RiskState(
        portfolio_value=960.0,
        daily_start_value=1000.0,
        peak_value=1000.0,
        open_trades=0,
        daily_loss=40.0,  # 4% daily loss on a 3% limit from config.
        trading_halted=False,
        halt_reason="",
    )
    gate_result = check_risk_gates(loss_limit_state)
    print("Risk gate evaluation after exceeding daily loss limit:")
    print(gate_result)
