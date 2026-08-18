#!/bin/sh
set -eu

umask 027

: "${JOB_INTEL_DB:=/data/job_intel.db}"
: "${PORT:=8000}"
: "${FORWARDED_ALLOW_IPS:=127.0.0.1}"
export JOB_INTEL_DB PORT FORWARDED_ALLOW_IPS

case "$PORT" in
  ""|*[!0-9]*)
    echo "PORT must be a numeric TCP port" >&2
    exit 64
    ;;
esac

database_directory=$(dirname "$JOB_INTEL_DB")
mkdir -p "$database_directory"
if [ ! -w "$database_directory" ]; then
  echo "Database directory is not writable by uid $(id -u): $database_directory" >&2
  echo "Mount a writable persistent volume and check its ownership." >&2
  exit 73
fi

# Supplying a command makes the image useful for operator tasks such as
# `job-intel import-dol` without maintaining a separate utility image.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

# SQLite is intentionally limited to one application process for this beta.
exec uvicorn fortune_intel.api:create_app \
  --factory \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
