"""Manual configuration smoke test for Driftwood."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config_loader import (
    get_config,
    get_kraken_credentials,
    is_paper_trading,
)


def main() -> None:
    try:
        config = get_config()
        credentials = get_kraken_credentials()
        paper_mode = is_paper_trading()

        print("=== Driftwood Config Test ===")
        print("Config:")
        print(config)
        print()

        print("Kraken credentials:")
        print(credentials)
        print()

        print(f"Paper trading mode: {paper_mode}")

        api_key = credentials.get("api_key")
        credentials_present = bool(api_key and api_key != "your_api_key_here")
        print(f"Kraken credentials present: {credentials_present}")

        print("Config loaded successfully")
    except Exception as exc:
        print(f"Config test failed: {exc}")


if __name__ == "__main__":
    main()
