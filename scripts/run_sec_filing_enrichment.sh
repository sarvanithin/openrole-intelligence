#!/bin/sh
# One durable official-SEC enrichment pass followed by a passive registry refresh.
# The SEC importer writes only exact-CIK filing evidence. ATS discovery creates
# review-only candidates; the export records those URLs as passive inventory and
# never enables a source.
set -eu

project_directory=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
run_directory=${1:?run directory is required}
database_path=${JOB_INTEL_DB:-"$project_directory/data/live_index.db"}
contact_email=$(git -C "$project_directory" config --get user.email || true)
if [ -z "$contact_email" ]; then
  echo 'SEC enrichment requires the configured repository contact email' >&2
  exit 2
fi
user_agent="OpenRole Intelligence $contact_email"

mkdir -p "$run_directory"
"$project_directory/.venv/bin/job-intel" --database "$database_path" import-sec-filing-websites \
  --actor overnight-sec-filing-enrichment \
  --user-agent "$user_agent" \
  --rate-per-second 5 --concurrency 4 --discover-new --discovery-concurrency 4 \
  >"$run_directory/sec-filing-result.json"

"$project_directory/.venv/bin/python" "$project_directory/scripts/export_verified_career_registry.py" \
  --database "$database_path" \
  --input "$project_directory/data/exports/companies_career_urls_2026-08-14.csv" \
  --output "$project_directory/data/exports/companies_career_urls_verified_enriched_2026-08-15.csv" \
  --report "$run_directory/verified-registry-export-report.json" \
  >"$run_directory/verified-registry-export.log"

observed_at=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
PYTHONPATH="$project_directory/src" "$project_directory/.venv/bin/python" -m fortune_intel.cli \
  --database "$database_path" import-career-url-registry \
  "$project_directory/data/exports/companies_career_urls_verified_enriched_2026-08-15.csv" \
  --actor overnight-sec-filing-enrichment --observed-at "$observed_at" \
  >>"$run_directory/verified-registry-export.log"
