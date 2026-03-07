"""SQLite database layer for the Driftwood trading system."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Dict

import pandas as pd
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Ensure project-root imports work when this file is run directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import get_config
from data.kraken_client import fetch_all_pairs

Base = declarative_base()


class OHLCV(Base):
    """Stores historical candles for each symbol/timeframe."""

    __tablename__ = "ohlcv"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_ohlcv_key"),
    )


class Trade(Base):
    """Stores each trade and its lifecycle for audit and analytics."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=False)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    exit_time = Column(DateTime(timezone=True), nullable=True)
    pnl = Column(Float, nullable=True)
    fees = Column(Float, nullable=False)
    status = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    mode = Column(String, nullable=False)


class SystemEvent(Base):
    """Stores system-level events (errors, pauses, signals, etc.)."""

    __tablename__ = "system_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, nullable=False)
    message = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    details = Column(String, nullable=True)


def get_engine() -> Engine:
    """Return a SQLAlchemy engine for the configured SQLite database."""
    config = get_config()
    db_path = config.get("database", {}).get("path", "data/driftwood.db")
    db_file = PROJECT_ROOT / db_path

    # Create the parent data directory if it does not yet exist.
    db_file.parent.mkdir(parents=True, exist_ok=True)

    return create_engine(f"sqlite:///{db_file}", future=True)


def init_db() -> None:
    """Create all database tables if they do not exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)


def save_ohlcv(df: pd.DataFrame, symbol: str, timeframe: str) -> int:
    """Insert OHLCV candles into the database, skipping duplicates silently."""
    if df.empty:
        return 0

    engine = get_engine()
    rows = []

    # Accept either timestamp as index or as a column.
    if "timestamp" in df.columns:
        working_df = df.copy()
        working_df["timestamp"] = pd.to_datetime(working_df["timestamp"], utc=True)
    else:
        working_df = df.reset_index().rename(columns={df.index.name or "index": "timestamp"})
        working_df["timestamp"] = pd.to_datetime(working_df["timestamp"], utc=True)

    for _, row in working_df.iterrows():
        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp": row["timestamp"].to_pydatetime(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )

    stmt = sqlite_insert(OHLCV.__table__).values(rows)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["symbol", "timeframe", "timestamp"]
    )

    with engine.begin() as conn:
        result = conn.execute(stmt)

    return int(result.rowcount or 0)


def load_ohlcv(symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
    """Load OHLCV candles from SQLite and return them as a DataFrame."""
    engine = get_engine()
    Session = sessionmaker(bind=engine, future=True)

    query = (
        select(
            OHLCV.timestamp,
            OHLCV.open,
            OHLCV.high,
            OHLCV.low,
            OHLCV.close,
            OHLCV.volume,
        )
        .where(OHLCV.symbol == symbol, OHLCV.timeframe == timeframe)
        .order_by(OHLCV.timestamp.asc())
        .limit(limit)
    )

    with Session() as session:
        rows = session.execute(query).all()

    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df.set_index("timestamp", inplace=True)
    return df


if __name__ == "__main__":
    # 1) Initialize schema.
    init_db()
    print("Database initialized.")

    # 2) Pull current market candles for all configured pairs/timeframes.
    all_data: Dict[str, Dict[str, pd.DataFrame]] = fetch_all_pairs()
    print("Fetched OHLCV data from Kraken.")

    # 3) Persist and reload to confirm writes succeeded.
    print("Save/load summary:")
    for symbol, timeframe_map in all_data.items():
        for timeframe, df in timeframe_map.items():
            inserted = save_ohlcv(df, symbol, timeframe)
            reloaded = load_ohlcv(symbol, timeframe, limit=len(df) or 500)
            print(
                f"- {symbol} {timeframe}: inserted={inserted}, "
                f"rows_in_db={len(reloaded)}"
            )
