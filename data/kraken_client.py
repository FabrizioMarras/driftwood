"""Kraken market data client for Driftwood."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Dict

import ccxt
import pandas as pd

# Ensure project-root imports work when running this file directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import get_config, get_kraken_credentials


def get_exchange() -> ccxt.kraken:
    """Initialize and return a Kraken exchange client.

    Uses authenticated mode only when non-placeholder credentials are present.
    """
    creds = get_kraken_credentials()
    api_key = creds.get("api_key")
    api_secret = creds.get("api_secret")

    options = {"enableRateLimit": True}

    # If credentials are missing/placeholders, use public API mode.
    if (
        api_key
        and api_secret
        and api_key != "your_api_key_here"
        and api_secret != "your_api_secret_here"
    ):
        options["apiKey"] = api_key
        options["secret"] = api_secret

    return ccxt.kraken(options)


def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
    """Fetch OHLCV candles from Kraken and return a normalized DataFrame."""
    exchange = get_exchange()

    try:
        raw_ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

        df = pd.DataFrame(
            raw_ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )

        # Convert from milliseconds to UTC datetime and make it the index.
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)

        return df
    except Exception as exc:
        raise RuntimeError(
            f"Failed to fetch OHLCV for {symbol} on {timeframe}: {exc}"
        ) from exc


def _extract_timeframes(config: dict) -> list[str]:
    """Extract unique timeframe values from trading.timeframes in config."""
    timeframes_cfg = config.get("trading", {}).get("timeframes", {})

    if isinstance(timeframes_cfg, dict):
        values = [str(v) for v in timeframes_cfg.values() if v]
    elif isinstance(timeframes_cfg, list):
        values = [str(v) for v in timeframes_cfg if v]
    else:
        values = []

    # Preserve order while removing duplicates.
    unique: list[str] = []
    for tf in values:
        if tf not in unique:
            unique.append(tf)

    return unique


def fetch_all_pairs() -> Dict[str, Dict[str, pd.DataFrame]]:
    """Fetch OHLCV data for every configured pair/timeframe combination."""
    config = get_config()
    pairs = config.get("trading", {}).get("pairs", [])
    timeframes = _extract_timeframes(config)

    all_data: Dict[str, Dict[str, pd.DataFrame]] = {}

    for symbol in pairs:
        all_data[symbol] = {}
        for timeframe in timeframes:
            try:
                all_data[symbol][timeframe] = fetch_ohlcv(symbol, timeframe)
            except Exception as exc:
                print(f"Error fetching {symbol} {timeframe}: {exc}")
                all_data[symbol][timeframe] = pd.DataFrame(
                    columns=["open", "high", "low", "close", "volume"]
                )

    return all_data


if __name__ == "__main__":
    dataset = fetch_all_pairs()
    print("OHLCV fetch summary:")
    for symbol, tf_map in dataset.items():
        print(f"- {symbol}")
        for timeframe, df in tf_map.items():
            print(f"  {timeframe}: {len(df)} candles")
