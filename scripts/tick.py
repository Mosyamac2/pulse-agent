"""CLI entry: `python -m scripts.tick [--date YYYY-MM-DD] [--force]`."""
from __future__ import annotations

import argparse
import sys
from datetime import date

from pulse.config import PATHS, configure_logging
from pulse.data_engine.tick import tick


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Run one daily tick.")
    parser.add_argument("--date", help="Override target date (YYYY-MM-DD).")
    parser.add_argument("--force", action="store_true", help="Re-tick even if date already present.")
    args = parser.parse_args(argv)
    target = date.fromisoformat(args.date) if args.date else None
    summary = tick(PATHS.db, target_date=target, force=args.force)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
