#!/bin/sh
# Start exactly one bounded, read-only verification run. Safe to resume by
# rerunning with the same output directory after an interruption.
set -eu

project_directory=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
database_path=${JOB_INTEL_DB:-"$project_directory/data/live_index.db"}
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  exec "$project_directory/.venv/bin/python" "$project_directory/scripts/overnight_verified_audit.py" --help
fi
run_directory=${1:-"$project_directory/data/overnight_runs/$(date -u +%Y%m%dT%H%M%SZ)"}

exec "$project_directory/.venv/bin/python" "$project_directory/scripts/overnight_verified_audit.py" \
  --database "$database_path" \
  --run-directory "$run_directory" \
  --max-duration-seconds 36000 \
  --workers 6 \
  --timeout-seconds 15 \
  --host-interval-seconds 1
