"""CLI entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config
from .sync import SyncEngine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync Raindrop.io raindrops → Notion data source (incremental by default)"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force a full reconciliation (including delete detection)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        config = load_config()
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2

    engine = SyncEngine(config)
    stats = engine.run(force_full=args.full)
    print(stats)
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
