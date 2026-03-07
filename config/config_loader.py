"""Configuration loader for the Driftwood trading system."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv


# Resolve important paths from this file location so imports work from anywhere.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
CONFIG_FILE = PROJECT_ROOT / "config" / "config.yaml"

# Load environment variables from the project root .env file once at import time.
load_dotenv(dotenv_path=ENV_FILE)


@lru_cache(maxsize=1)
def _load_config() -> Dict[str, Any]:
    """Load and cache the YAML config, applying environment overrides."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            "config/config.yaml not found — have you set up the project correctly?"
        )

    with CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    # Ensure the general section exists before applying mode overrides.
    general = config.setdefault("general", {})

    # APP_ENV overrides general.mode when present (e.g., paper/live).
    app_env = os.getenv("APP_ENV")
    if app_env:
        general["mode"] = app_env

    return config


def get_config() -> Dict[str, Any]:
    """Return the full application configuration dictionary."""
    return _load_config()


def get_kraken_credentials() -> Dict[str, str | None]:
    """Return Kraken API credentials from environment variables."""
    return {
        "api_key": os.getenv("KRAKEN_API_KEY"),
        "api_secret": os.getenv("KRAKEN_API_SECRET"),
    }


def is_paper_trading() -> bool:
    """Return True when configured mode is paper, otherwise False."""
    mode = str(get_config().get("general", {}).get("mode", "")).strip().lower()
    return mode == "paper"
