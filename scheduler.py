"""Main scheduler entrypoint for the Driftwood trading system."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Tuple

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config.config_loader import get_config, is_paper_trading
from data.database import init_db, save_ohlcv
from data.kraken_client import fetch_all_pairs
from execution.execution_engine import log_system_event, run_trading_cycle
from risk.risk_engine import RiskState


def _parse_size_to_bytes(size_value: str) -> int:
    """Convert human-readable size strings (e.g. 10MB) into bytes."""
    value = size_value.strip().upper()
    if value.endswith("MB"):
        return int(float(value[:-2].strip()) * 1024 * 1024)
    if value.endswith("KB"):
        return int(float(value[:-2].strip()) * 1024)
    if value.endswith("B"):
        return int(float(value[:-1].strip()))
    return int(float(value))


def _setup_logging() -> logging.Logger:
    """Configure root logging to console and rotating log file."""
    config = get_config()
    general_cfg = config.get("general", {})
    logging_cfg = config.get("logging", {})

    log_level_name = str(general_cfg.get("log_level", "INFO")).upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    log_path = Path(logging_cfg.get("path", "logs/driftwood.log"))
    max_file_size = _parse_size_to_bytes(str(logging_cfg.get("max_file_size", "10MB")))
    backup_count = int(logging_cfg.get("backup_count", 5))

    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        filename=log_path,
        maxBytes=max_file_size,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    return logging.getLogger(__name__)


def _initial_risk_state_from_config() -> RiskState:
    """Build the initial RiskState object using configured capital."""
    config = get_config()
    initial_capital = float(config.get("portfolio", {}).get("initial_capital_usd", 1000.0))
    return RiskState(
        portfolio_value=initial_capital,
        daily_start_value=initial_capital,
        peak_value=initial_capital,
        open_trades=0,
        daily_loss=0.0,
        trading_halted=False,
        halt_reason="",
    )


logger = _setup_logging()
PAUSED = False
current_risk_state = _initial_risk_state_from_config()


def job_fetch_and_store() -> None:
    """Fetch market candles and store only new OHLCV rows in the database."""
    try:
        all_data = fetch_all_pairs()
        total_inserted = 0

        for symbol, timeframe_map in all_data.items():
            for timeframe, df in timeframe_map.items():
                inserted = save_ohlcv(df, symbol, timeframe)
                total_inserted += inserted
                logger.info(
                    "Saved candles for %s %s: %s new rows", symbol, timeframe, inserted
                )

        logger.info("Fetch/store job completed. Total new candles: %s", total_inserted)
    except Exception as exc:
        logger.exception("Fetch/store job failed: %s", exc)
        try:
            log_system_event(
                "error",
                "Fetch/store job failed",
                {"error": str(exc), "timestamp": datetime.now(timezone.utc).isoformat()},
            )
        except Exception:
            logger.exception("Failed to log system event for fetch/store error.")


def job_trading_cycle() -> None:
    """Run one trading cycle unless the scheduler is paused."""
    global current_risk_state

    if PAUSED:
        logger.info("Trading paused, skipping cycle")
        return

    try:
        current_risk_state = run_trading_cycle(current_risk_state)
        logger.info("Trading cycle completed successfully.")
    except Exception as exc:
        logger.exception("Trading cycle failed: %s", exc)
        try:
            log_system_event(
                "error",
                "Trading cycle failed",
                {"error": str(exc), "timestamp": datetime.now(timezone.utc).isoformat()},
            )
        except Exception:
            logger.exception("Failed to log system event for trading cycle error.")


def job_reset_daily_state() -> None:
    """Reset daily risk counters at UTC midnight."""
    global current_risk_state

    current_risk_state.daily_loss = 0.0
    current_risk_state.daily_start_value = current_risk_state.portfolio_value
    current_risk_state.trading_halted = False
    current_risk_state.halt_reason = ""
    logger.info("Daily risk state reset")


def _startup_context() -> Tuple[str, list[str]]:
    """Return startup mode label and configured trading pairs."""
    config = get_config()
    mode = "paper" if is_paper_trading() else "live"
    pairs = list(config.get("trading", {}).get("pairs", []))
    return mode, pairs


def main() -> None:
    """Initialize services and start scheduled jobs."""
    init_db()
    mode, pairs = _startup_context()
    logger.info("Starting Driftwood scheduler in %s mode for pairs: %s", mode, pairs)

    # Run once on startup so data/trading loop starts immediately.
    job_fetch_and_store()
    job_trading_cycle()

    # UTC-based schedule:
    # - Fetch/store at hh:01
    # - Trading cycle at hh:02
    # - Daily risk reset at 00:00 UTC
    scheduler = BlockingScheduler(timezone=timezone.utc)
    scheduler.add_job(
        job_fetch_and_store,
        CronTrigger(minute=1, timezone=timezone.utc),
        id="fetch_and_store",
        replace_existing=True,
    )
    scheduler.add_job(
        job_trading_cycle,
        CronTrigger(minute=2, timezone=timezone.utc),
        id="trading_cycle",
        replace_existing=True,
    )
    scheduler.add_job(
        job_reset_daily_state,
        CronTrigger(hour=0, minute=0, timezone=timezone.utc),
        id="daily_reset",
        replace_existing=True,
    )

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
