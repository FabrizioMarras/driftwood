"""Standalone backtesting engine for Driftwood's trend-following strategy."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import ccxt
import numpy as np
import pandas as pd

# Ensure project-root imports work when this file is run directly.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import get_config
from signals.indicators import compute_all_indicators, compute_trend_signal


BACKTEST_CONFIG = {
    "start_date": "2020-01-01",
    "end_date": "2025-12-31",
    "initial_capital": 1000.0,
    "stop_loss_pct": 0.03,
    "take_profit_pct": 0.06,
    "fee_rate": 0.0026,
    "slippage": 0.001,
    "position_size_pct": 0.20,
    "max_open_trades": 2,
    "min_trade_size_usd": 10.0,
}


def _to_utc_ms(date_str: str, end_of_day: bool = False) -> int:
    """Convert YYYY-MM-DD to UTC milliseconds, optionally at end of day."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        dt = dt + timedelta(days=1) - timedelta(milliseconds=1)
    return int(dt.timestamp() * 1000)


def fetch_historical_ohlcv(
    symbol: str, timeframe: str, start_date: str, end_date: str
) -> pd.DataFrame:
    """Fetch Kraken OHLCV in 500-candle pages within the provided UTC date range."""
    exchange = ccxt.binance()
    start_ms = _to_utc_ms(start_date)
    end_ms = _to_utc_ms(end_date, end_of_day=True)

    all_candles: List[List[float]] = []
    since = start_ms

    try:
        while since <= end_ms:
            batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=500)
            if not batch:
                if not all_candles:
                    print(
                        f"Warning: no candles returned for {symbol} {timeframe} "
                        f"between {start_date} and {end_date}."
                    )
                break

            # Keep only candles that are still within the requested range.
            in_range = [row for row in batch if int(row[0]) <= end_ms]
            all_candles.extend(in_range)
            time.sleep(1)

            print(f"Fetching {symbol} {timeframe}... {len(all_candles)} candles fetched")

            # Stop if exchange returned less than a full page or we've passed the end.
            if len(batch) < 500 or int(batch[-1][0]) >= end_ms:
                break

            # Move forward by one candle to avoid duplicates from inclusive 'since'.
            tf_ms = exchange.parse_timeframe(timeframe) * 1000
            since = int(batch[-1][0]) + tf_ms

        if not all_candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(
            all_candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = (
            df.drop_duplicates(subset=["timestamp"])
            .set_index("timestamp")
            .sort_index()[["open", "high", "low", "close", "volume"]]
        )
        return df

    except Exception as exc:
        print(f"Warning: failed to fetch {symbol} {timeframe}: {exc}")
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def generate_signals(
    daily_df: pd.DataFrame, four_hour_df: pd.DataFrame, one_hour_df: pd.DataFrame
) -> List[Dict[str, Any]]:
    """Generate one decision signal per 4h candle using only data available at that time."""
    signals: List[Dict[str, Any]] = []
    total_points = len(four_hour_df.index)

    for idx, ts in enumerate(four_hour_df.index, start=1):
        # Strict no-lookahead: only include candles up to and including the current 4h timestamp.
        daily_slice = daily_df.loc[daily_df.index <= ts]
        four_hour_slice = four_hour_df.loc[four_hour_df.index <= ts]
        one_hour_slice = one_hour_df.loc[one_hour_df.index <= ts]

        # Indicators rely on rolling windows; skip until enough history exists.
        if len(daily_slice) < 50 or len(four_hour_slice) < 50 or len(one_hour_slice) < 50:
            if idx % 100 == 0:
                print(f"Generated {idx}/{total_points} signals...")
            continue

        daily_enriched = compute_all_indicators(daily_slice.copy(), "daily")
        four_hour_enriched = compute_all_indicators(four_hour_slice.copy(), "four_hour")
        one_hour_enriched = compute_all_indicators(one_hour_slice.copy(), "one_hour")

        signal = compute_trend_signal(daily_enriched, four_hour_enriched, one_hour_enriched)

        signals.append(
            {
                "timestamp": ts,
                "signal": signal.get("signal", "hold"),
                "daily_trend": signal.get("daily_trend", "neutral"),
                "four_hour_trend": signal.get("four_hour_trend", "neutral"),
                "one_hour_trend": signal.get("one_hour_trend", "neutral"),
                "volume_confirmed": bool(signal.get("volume_confirmed", False)),
                "reason": signal.get("reason", ""),
                "close_price": float(four_hour_slice["close"].iloc[-1]),
            }
        )

        if idx % 100 == 0:
            print(f"Generated {idx}/{total_points} signals...")

    return signals


def simulate_trades(signals: List[Dict[str, Any]], symbol: str) -> List[Dict[str, Any]]:
    """Simulate entries/exits with slippage, fees, and portfolio-level risk controls."""
    portfolio_value = float(BACKTEST_CONFIG["initial_capital"])
    peak_value = portfolio_value
    daily_start_value = portfolio_value
    daily_loss = 0.0
    daily_halted = False
    trading_halted = False

    open_trades: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []

    current_day: datetime.date | None = None

    for point in signals:
        ts = pd.Timestamp(point["timestamp"]).tz_convert("UTC")
        signal = str(point["signal"]).lower()
        close_price = float(point["close_price"])

        # Reset daily-loss tracking at UTC midnight.
        if current_day != ts.date():
            current_day = ts.date()
            daily_start_value = portfolio_value
            daily_loss = 0.0
            daily_halted = False

        # Entry logic mirrors the live system's buy gate + position sizing constraints.
        has_trade_for_symbol = any(t["symbol"] == symbol for t in open_trades)
        can_open_trade = (
            signal == "buy"
            and len(open_trades) < int(BACKTEST_CONFIG["max_open_trades"])
            and not has_trade_for_symbol
            and not daily_halted
            and not trading_halted
        )

        if can_open_trade:
            entry_price = close_price * (1.0 + float(BACKTEST_CONFIG["slippage"]))
            position_value = portfolio_value * float(BACKTEST_CONFIG["position_size_pct"])

            if position_value >= float(BACKTEST_CONFIG["min_trade_size_usd"]):
                quantity = position_value / entry_price
                entry_fee = entry_price * quantity * float(BACKTEST_CONFIG["fee_rate"])
                open_trades.append(
                    {
                        "symbol": symbol,
                        "entry_time": ts,
                        "entry_price": entry_price,
                        "quantity": quantity,
                        "entry_fee": entry_fee,
                    }
                )

        # Evaluate exits for each open trade on every decision candle.
        remaining_open_trades: List[Dict[str, Any]] = []
        for trade in open_trades:
            stop_loss_price = trade["entry_price"] * (1.0 - float(BACKTEST_CONFIG["stop_loss_pct"]))
            take_profit_price = trade["entry_price"] * (
                1.0 + float(BACKTEST_CONFIG["take_profit_pct"])
            )

            should_exit = False
            exit_reason = ""
            if close_price <= stop_loss_price:
                should_exit = True
                exit_reason = "stop_loss"
            elif close_price >= take_profit_price:
                should_exit = True
                exit_reason = "take_profit"
            elif signal == "sell":
                should_exit = True
                exit_reason = "trend_reversal"

            if should_exit:
                exit_price = close_price * (1.0 - float(BACKTEST_CONFIG["slippage"]))
                exit_fee = exit_price * trade["quantity"] * float(BACKTEST_CONFIG["fee_rate"])
                pnl = (
                    (exit_price - trade["entry_price"]) * trade["quantity"]
                    - trade["entry_fee"]
                    - exit_fee
                )

                portfolio_value += pnl
                peak_value = max(peak_value, portfolio_value)

                if pnl < 0:
                    daily_loss += abs(pnl)

                # Halt new entries for the rest of the UTC day once daily loss limit is breached.
                if daily_start_value > 0 and (daily_loss / daily_start_value) >= 0.03:
                    daily_halted = True

                drawdown = 0.0
                if peak_value > 0:
                    drawdown = (peak_value - portfolio_value) / peak_value

                closed_trades.append(
                    {
                        "symbol": trade["symbol"],
                        "entry_time": trade["entry_time"],
                        "entry_price": trade["entry_price"],
                        "quantity": trade["quantity"],
                        "entry_fee": trade["entry_fee"],
                        "exit_time": ts,
                        "exit_price": exit_price,
                        "exit_fee": exit_fee,
                        "pnl": pnl,
                        "fees": trade["entry_fee"] + exit_fee,
                        "exit_reason": exit_reason,
                        "portfolio_value_after_trade": portfolio_value,
                        "drawdown_pct": drawdown * 100.0,
                    }
                )

                # Hard stop all trading if max drawdown breach occurs.
                if drawdown >= 0.10:
                    trading_halted = True
                    remaining_open_trades = []
                    break
            else:
                remaining_open_trades.append(trade)

        open_trades = remaining_open_trades

        if trading_halted:
            break

    return closed_trades


def calculate_results(
    trades: List[Dict[str, Any]], symbol: str, start_date: str, end_date: str
) -> Dict[str, Any]:
    """Aggregate core performance metrics for one symbol backtest run."""
    total_trades = len(trades)
    wins = [t for t in trades if float(t["pnl"]) > 0]
    losses = [t for t in trades if float(t["pnl"]) < 0]

    total_pnl = float(sum(float(t["pnl"]) for t in trades))
    initial_capital = float(BACKTEST_CONFIG["initial_capital"])
    final_portfolio_value = initial_capital + total_pnl

    win_sum = float(sum(float(t["pnl"]) for t in wins))
    loss_sum = float(sum(float(t["pnl"]) for t in losses))

    profit_factor = 0.0
    if loss_sum < 0:
        profit_factor = win_sum / abs(loss_sum)

    avg_win = float(np.mean([float(t["pnl"]) for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([float(t["pnl"]) for t in losses])) if losses else 0.0
    max_gain = max((float(t["pnl"]) for t in trades), default=0.0)
    max_loss = min((float(t["pnl"]) for t in trades), default=0.0)

    # Build equity curve from closed-trade updates to measure peak-to-trough drawdown.
    equity_curve = [initial_capital] + [
        float(t.get("portfolio_value_after_trade", initial_capital)) for t in trades
    ]
    running_peak = -float("inf")
    max_drawdown_pct = 0.0
    for equity in equity_curve:
        running_peak = max(running_peak, equity)
        if running_peak > 0:
            dd_pct = ((running_peak - equity) / running_peak) * 100.0
            max_drawdown_pct = max(max_drawdown_pct, dd_pct)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    years = max((end_dt - start_dt).days / 365.25, 1e-9)

    return {
        "symbol": symbol,
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": (len(wins) / total_trades * 100.0) if total_trades else 0.0,
        "total_pnl_usd": total_pnl,
        "total_pnl_pct": (total_pnl / initial_capital * 100.0) if initial_capital else 0.0,
        "profit_factor": profit_factor,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "max_single_gain_usd": max_gain,
        "max_single_loss_usd": max_loss,
        "trades_per_year": total_trades / years,
        "max_drawdown_pct": max_drawdown_pct,
        "final_portfolio_value": final_portfolio_value,
        "years": years,
    }


def print_results(results: Dict[str, Any]) -> None:
    """Print a clean, aligned performance summary for one symbol."""
    print("═" * 42)
    print(f" Backtest Results - {results['symbol']}")
    print(
        f" {BACKTEST_CONFIG['start_date']} to {BACKTEST_CONFIG['end_date']} "
        f"({results['years']:.0f} years)"
    )
    print("═" * 42)
    print(f" Total trades:        {results['total_trades']}")
    print(f" Win rate:            {results['win_rate_pct']:.2f}%")
    print(
        f" Total PnL:           {results['total_pnl_usd']:+.2f} "
        f"({results['total_pnl_pct']:+.2f}%)"
    )
    print(f" Profit factor:       {results['profit_factor']:.2f}")
    print(f" Avg win:             {results['avg_win_usd']:+.2f}")
    print(f" Avg loss:            {results['avg_loss_usd']:+.2f}")
    print(f" Max gain (single):   {results['max_single_gain_usd']:+.2f}")
    print(f" Max loss (single):   {results['max_single_loss_usd']:+.2f}")
    print(f" Trades per year:     {results['trades_per_year']:.1f}")
    print(f" Max drawdown:        {results['max_drawdown_pct']:.2f}%")
    print(f" Final portfolio:     {results['final_portfolio_value']:.2f}")
    print("═" * 42)


if __name__ == "__main__":
    print("🌊 Driftwood Backtest Engine")
    print(
        "Config: "
        f"{BACKTEST_CONFIG['start_date']} -> {BACKTEST_CONFIG['end_date']}, "
        f"Initial ${BACKTEST_CONFIG['initial_capital']:.2f}, "
        f"SL {BACKTEST_CONFIG['stop_loss_pct'] * 100:.1f}%, "
        f"TP {BACKTEST_CONFIG['take_profit_pct'] * 100:.1f}%"
    )

    # Load project config so the backtest uses the same indicator parameters as live execution.
    _ = get_config()

    symbols = [("BTC/USDT", "BTC/USD"), ("ETH/USDT", "ETH/USD")]
    all_results: List[Dict[str, Any]] = []

    for fetch_symbol, display_symbol in symbols:
        print(f"\nRunning backtest for {display_symbol}...")
        try:
            daily_df = fetch_historical_ohlcv(
                fetch_symbol, "1d", BACKTEST_CONFIG["start_date"], BACKTEST_CONFIG["end_date"]
            )
            four_hour_df = fetch_historical_ohlcv(
                fetch_symbol, "4h", BACKTEST_CONFIG["start_date"], BACKTEST_CONFIG["end_date"]
            )
            one_hour_df = fetch_historical_ohlcv(
                fetch_symbol, "1h", BACKTEST_CONFIG["start_date"], BACKTEST_CONFIG["end_date"]
            )

            if daily_df.empty or four_hour_df.empty or one_hour_df.empty:
                print(f"Warning: incomplete historical data for {display_symbol}. Skipping.")
                continue

            signals = generate_signals(daily_df, four_hour_df, one_hour_df)
            trades = simulate_trades(signals, display_symbol)
            results = calculate_results(
                trades,
                display_symbol,
                BACKTEST_CONFIG["start_date"],
                BACKTEST_CONFIG["end_date"],
            )
            print_results(results)
            all_results.append(results)

        except Exception as exc:
            print(f"Warning: backtest failed for {display_symbol}: {exc}")

    if all_results:
        total_trades = sum(int(r["total_trades"]) for r in all_results)
        combined_pnl = sum(float(r["total_pnl_usd"]) for r in all_results)
        initial_total = float(BACKTEST_CONFIG["initial_capital"]) * len(all_results)
        combined_pct = (combined_pnl / initial_total * 100.0) if initial_total else 0.0
        best_pair = max(all_results, key=lambda x: float(x["total_pnl_usd"]))

        print("\n" + "═" * 42)
        print(" Combined Summary")
        print("═" * 42)
        print(f" Total trades:        {total_trades}")
        print(f" Combined PnL:        {combined_pnl:+.2f} ({combined_pct:+.2f}%)")
        print(
            f" Best pair:           {best_pair['symbol']} "
            f"({best_pair['total_pnl_usd']:+.2f})"
        )
        print("═" * 42)

        # Save results to JSON for dashboard consumption.
        output_path = PROJECT_ROOT / "data" / "backtest_results.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "config": BACKTEST_CONFIG,
                    "results": all_results,
                },
                f,
                indent=2,
                default=str,
            )
        print(f"\n✅ Results saved to {output_path}")
    else:
        print("\nNo completed backtests to summarize.")
