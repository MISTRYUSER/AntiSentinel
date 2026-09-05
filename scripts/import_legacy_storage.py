#!/usr/bin/env python3
"""Import legacy AntiSentinel storage into SQLite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from antisentinel.persistence.legacy_import import LegacyImporter
from antisentinel.persistence.sqlite_database import SQLiteDatabase


def main() -> int:
    parser = argparse.ArgumentParser(description="Import AntiSentinel JSON/JSONL storage into SQLite")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    database = SQLiteDatabase(args.database)
    database.initialize()
    report = LegacyImporter(args.source, database).run()
    print(json.dumps(report.__dict__, ensure_ascii=False, separators=(",", ":")))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
