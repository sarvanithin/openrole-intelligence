#!/bin/sh
# Durable, high-volume verification of passive custom career-page inventory.
# Reviewed ATS policies come from ATS_POLICY_URLS / ATS_POLICY_APPROVED_AT;
# complete-manifest approval remains the only possible source-activation path.
set -eu

project_directory=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
database_path=${JOB_INTEL_DB:-"$project_directory/data/live_index.db"}
batch_size=${REGISTRY_PORTAL_BATCH_SIZE:-200}
concurrency=${REGISTRY_PORTAL_CONCURRENCY:-8}
shard_count=${REGISTRY_PORTAL_SHARD_COUNT:-1}
shard_index=${REGISTRY_PORTAL_SHARD_INDEX:-0}
max_batches=${REGISTRY_PORTAL_MAX_BATCHES:-40}
pace_seconds=${REGISTRY_PORTAL_PACE_SECONDS:-0.5}

exec "$project_directory/.venv/bin/job-intel" --database "$database_path" \
  verify-registry-career-portals \
  --actor registry-career-portal-service \
  --batch-size "$batch_size" \
  --concurrency "$concurrency" \
  --shard-count "$shard_count" \
  --shard-index "$shard_index" \
  --max-batches "$max_batches" \
  --pace-seconds "$pace_seconds"
