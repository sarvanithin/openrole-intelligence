#!/usr/bin/env python3
"""Validate and atomically restore a standalone SQLite backup.

The application must be stopped before this command is run.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from pathlib import Path


def restore(source_path: Path, destination_path: Path, *, force: bool) -> None:
    source_path = source_path.resolve(strict=True)
    destination_path = destination_path.resolve(strict=False)
    if source_path == destination_path:
        raise ValueError("source and destination must be different files")
    if destination_path.exists() and not force:
        raise FileExistsError("destination exists; pass --force after stopping and backing up the app")

    source_uri = f"{source_path.as_uri()}?mode=ro&immutable=1"
    with sqlite3.connect(source_uri, uri=True) as source:
        integrity = source.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError(f"source backup failed integrity_check: {integrity}")

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination_path.name}.", suffix=".restore", dir=destination_path.parent
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            with sqlite3.connect(temporary_path) as destination:
                source.backup(destination)
                restored_integrity = destination.execute("PRAGMA integrity_check").fetchone()
                if not restored_integrity or restored_integrity[0] != "ok":
                    raise ValueError(
                        f"restored database failed integrity_check: {restored_integrity}"
                    )
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, destination_path)
            for suffix in ("-wal", "-shm"):
                Path(f"{destination_path}{suffix}").unlink(missing_ok=True)
        finally:
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="validated standalone backup file")
    parser.add_argument("destination", type=Path, help="application SQLite database path")
    parser.add_argument(
        "--force", action="store_true", help="replace an existing destination database"
    )
    args = parser.parse_args()
    restore(args.source, args.destination, force=args.force)
    print(f"Restored {args.source} to {args.destination}")


if __name__ == "__main__":
    main()
