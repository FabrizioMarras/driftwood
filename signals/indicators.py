"""Technical indicator calculations for Driftwood's trend-following logic."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Dict

import numpy as np
import pandas as pd

# Ensure project-root imports work when this file is run directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import get_config
from data.database import load_ohlcv


def compute_moving_averages(
    df: pd.DataFrame, fast_period: int, slow_period: int
) -> pd.DataFrame:
    """Add moving averages and MA trend direction to the DataFrame."""
    result = df.copy()
    result["fast_ma"] = result["close"].rolling(window=fast_period).mean()
    result["slow_ma"] = result["close"].rolling(window=slow_period).mean()

    # Compare MA lines to classify trend direction.
    result["ma_trend"] = np.select(
        [result["fast_ma"] > result["slow_ma"], result["fast_ma"] < result["slow_ma"]],
        ["up", "down"],
        default="neutral",
    )
    return result


def compute_momentum(df: pd.DataFrame, period: int) -> pd.DataFrame:
    """Add momentum (%) and momentum signal classification."""
    result = df.copy()
    prev_close = result["close"].shift(period)
    result["momentum"] = ((result["close"] - prev_close) / prev_close) * 100

    # Positive momentum is bullish, negative is bearish, zero is neutral.
    result["momentum_signal"] = np.select(
        [result["momentum"] > 0, result["momentum"] < 0],
        ["bullish", "bearish"],
        default="neutral",
    )
    return result


def compute_volume_confirmation(
    df: pd.DataFrame, volume_ma_period: int
) -> pd.DataFrame:
    """Add rolling volume average and above-average volume confirmation."""
    result = df.copy()
    result["volume_ma"] = result["volume"].rolling(window=volume_ma_period).mean()
    result["volume_confirmation"] = result["volume"] > result["volume_ma"]
    return result


def compute_all_indicators(df: pd.DataFrame, timeframe_key: str) -> pd.DataFrame:
    """Compute all configured indicators for one logical timeframe block."""
    config = get_config()
    signals_cfg = config.get("signals", {})
    tf_cfg = signals_cfg.get(timeframe_key)

    if not isinstance(tf_cfg, dict):
        raise ValueError(
            f"Missing signals config for timeframe_key='{timeframe_key}'. "
            "Expected one of: daily, four_hour, one_hour."
        )

    result = compute_moving_averages(
        df,
        fast_period=int(tf_cfg["fast_ma_period"]),
        slow_period=int(tf_cfg["slow_ma_period"]),
    )
    result = compute_momentum(result, period=int(tf_cfg["momentum_period"]))
    result = compute_volume_confirmation(
        result, volume_ma_period=int(tf_cfg["volume_ma_period"])
    )
    result["timeframe"] = timeframe_key
    return result


def compute_trend_signal(
    daily_df: pd.DataFrame, four_hour_df: pd.DataFrame, one_hour_df: pd.DataFrame
) -> Dict[str, Any]:
    """Combine multi-timeframe indicators into one trading signal decision."""
    if daily_df.empty or four_hour_df.empty or one_hour_df.empty:
        return {
            "daily_trend": "neutral",
            "four_hour_trend": "neutral",
            "one_hour_trend": "neutral",
            "daily_momentum": "neutral",
            "four_hour_momentum": "neutral",
            "one_hour_momentum": "neutral",
            "volume_confirmed": False,
            "signal": "hold",
            "reason": "Insufficient data: one or more timeframe datasets are empty.",
        }

    daily_latest = daily_df.iloc[-1]
    four_hour_latest = four_hour_df.iloc[-1]
    one_hour_latest = one_hour_df.iloc[-1]

    trends = [
        str(daily_latest.get("ma_trend", "neutral")),
        str(four_hour_latest.get("ma_trend", "neutral")),
        str(one_hour_latest.get("ma_trend", "neutral")),
    ]
    momentums = [
        str(daily_latest.get("momentum_signal", "neutral")),
        str(four_hour_latest.get("momentum_signal", "neutral")),
        str(one_hour_latest.get("momentum_signal", "neutral")),
    ]
    volume_checks = [
        bool(daily_latest.get("volume_confirmation", False)),
        bool(four_hour_latest.get("volume_confirmation", False)),
        bool(one_hour_latest.get("volume_confirmation", False)),
    ]

    bullish_count = sum(m == "bullish" for m in momentums)
    bearish_count = sum(m == "bearish" for m in momentums)
    volume_confirmed = sum(volume_checks) >= 2

    if trends == ["up", "up", "up"] and bullish_count >= 2:
        signal = "buy"
        reason = (
            "All three timeframes trend up and at least two show bullish momentum."
        )
    elif trends == ["down", "down", "down"] and bearish_count >= 2:
        signal = "sell"
        reason = (
            "All three timeframes trend down and at least two show bearish momentum."
        )
    else:
        signal = "hold"
        reason = (
            "Timeframes are not fully aligned on trend/momentum, so no trade signal."
        )

    return {
        "daily_trend": trends[0],
        "four_hour_trend": trends[1],
        "one_hour_trend": trends[2],
        "daily_momentum": momentums[0],
        "four_hour_momentum": momentums[1],
        "one_hour_momentum": momentums[2],
        "volume_confirmed": volume_confirmed,
        "signal": signal,
        "reason": reason,
    }


if __name__ == "__main__":
    # Load historical candles for BTC/USD from local DB by raw exchange timeframe.
    daily = load_ohlcv("BTC/USD", "1d")
    four_hour = load_ohlcv("BTC/USD", "4h")
    one_hour = load_ohlcv("BTC/USD", "1h")

    # Enrich each timeframe using its own configured indicator periods.
    daily_enriched = compute_all_indicators(daily, "daily")
    four_hour_enriched = compute_all_indicators(four_hour, "four_hour")
    one_hour_enriched = compute_all_indicators(one_hour, "one_hour")

    # Build and print a clear signal summary.
    trend_signal = compute_trend_signal(
        daily_enriched, four_hour_enriched, one_hour_enriched
    )

    print("Multi-timeframe trend signal:")
    for key, value in trend_signal.items():
        print(f"- {key}: {value}")
