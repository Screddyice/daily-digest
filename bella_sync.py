#!/usr/bin/env python3
"""Collect Bella's Fi health snapshot and persist it in Corpus."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from typing import Callable

import bella
import bella_corpus


def _stderr(text: str) -> None:
    print(text, file=sys.stderr)


def main(
    argv: list[str] | None = None,
    *,
    collect: Callable | None = None,
    client_factory: Callable | None = None,
    output: Callable[[str], None] = print,
    error: Callable[[str], None] = _stderr,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Fi report date (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="collect and validate; write nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    try:
        target = date.fromisoformat(args.date) if args.date else date.today()
    except ValueError:
        error("Bella Fi sync failed: --date must be YYYY-MM-DD")
        return 1

    collector = collect or bella.collect_snapshot
    try:
        snapshot = collector(target)
        rows = bella_corpus.snapshot_rows(snapshot)
    except bella.FiSyncError:
        error("Bella Fi sync failed: connector did not return a usable snapshot")
        return 1
    except Exception as exc:
        logging.getLogger(__name__).exception("Bella Fi sync failed")
        error(f"Bella Fi sync failed: {type(exc).__name__}")
        return 1

    if not rows:
        error("Bella Fi sync failed: snapshot contained no supported metrics")
        return 1

    metric_names = ", ".join(sorted({row.metric for row in rows}))
    noun = "row" if len(rows) == 1 else "rows"
    summary = f"{snapshot.pet_name} {target.isoformat()}: {len(rows)} {noun} ({metric_names})"

    if args.dry_run:
        output(f"DRY RUN {summary}")
        return 0

    try:
        if client_factory is None:
            from shawn_corpus import KnowledgeClient
            client_factory = KnowledgeClient.from_env
        count = bella_corpus.write_snapshot(snapshot, client_factory())
    except Exception as exc:
        logging.getLogger(__name__).exception("Bella Corpus write failed")
        error(f"Bella Corpus write failed: {type(exc).__name__}")
        return 1

    output(f"WROTE {count} {noun}: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
