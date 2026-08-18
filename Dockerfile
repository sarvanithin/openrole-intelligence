# syntax=docker/dockerfile:1.7

FROM python:3.11-slim@sha256:94c50be2dc994b873b55bc123e95e6dbade08095b3dfd790f51c34de3f08cbb7

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    JOB_INTEL_DB=/data/job_intel.db \
    PORT=8000

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
        --create-home --home-dir /home/app --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.lock ./
COPY src ./src

RUN python -m pip install --require-hashes -r requirements.lock \
    && install -d -o app -g app /data

COPY --chmod=0755 deploy/entrypoint.sh deploy/healthcheck.py deploy/restore_db.py /app/deploy/
COPY --chmod=0755 scripts/backup_db.sh /app/scripts/backup_db.sh

USER app:app

EXPOSE 8000
VOLUME ["/data"]
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "/app/deploy/healthcheck.py"]

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
