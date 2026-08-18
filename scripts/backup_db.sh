#!/bin/sh
set -eu

database_path=${1:-${JOB_INTEL_DB:-data/job_intel.db}}
backup_directory=${2:-backups}

if [ ! -f "$database_path" ]; then
  echo "Database does not exist: $database_path" >&2
  exit 66
fi

mkdir -p "$backup_directory"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
destination="$backup_directory/job_intel-$timestamp.db"

python - "$database_path" "$destination" <<'PY'
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

source_path = Path(sys.argv[1]).resolve(strict=True)
destination_path = Path(sys.argv[2]).resolve(strict=False)
destination_path.parent.mkdir(parents=True, exist_ok=True)

descriptor, temporary_name = tempfile.mkstemp(
    prefix=f".{destination_path.name}.", suffix=".backup", dir=destination_path.parent
)
os.close(descriptor)
temporary_path = Path(temporary_name)

try:
    # Opening the live database normally ensures SQLite includes committed WAL
    # content in the online backup snapshot.
    with sqlite3.connect(source_path) as source, sqlite3.connect(temporary_path) as target:
        source.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"backup failed integrity_check: {integrity}")

    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, destination_path)
finally:
    temporary_path.unlink(missing_ok=True)

digest = hashlib.sha256(destination_path.read_bytes()).hexdigest()
print(destination_path)
print(f"sha256:{digest}")
PY
