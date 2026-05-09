"""CLI entry: `python -m scripts.seed [--force]`.

Generates `data/sber_hr.db` plus snapshots in `data/synthetic/`.
"""
from __future__ import annotations

import argparse
import sys

from pulse.config import PATHS, configure_logging
from pulse.data_engine.seed import seed


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Seed Pulse synthetic HR sandbox.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing DB.")
    args = parser.parse_args(argv)
    PATHS.ensure()
    summary = seed(PATHS.db, force=args.force)
    for t, n in sorted(summary.items()):
        print(f"  {t}: {n}")
    print(f"\nDB: {PATHS.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
