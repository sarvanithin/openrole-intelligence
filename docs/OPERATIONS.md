# Public beta operations

This runbook packages OpenRole Intelligence as a safe **single-instance beta**.
The application database is SQLite on a persistent volume. Run exactly one
application replica and one Uvicorn worker until a PostgreSQL repository and
migrations are implemented.

## Deployment contract

| Setting | Required value |
|---|---|
| Container port | `8000`, or the platform-provided `PORT` |
| Persistent mount | `/data` |
| Database | `JOB_INTEL_DB=/data/job_intel.db` |
| Readiness check | `/readyz` |
| Replicas | **1** |
| Uvicorn workers | **1**; enforced by the image entrypoint |
| Restart policy | On failure / unless stopped |
| TLS | Terminate at the hosting platform or a trusted reverse proxy |

The containers run as uid/gid `10001`, drop Linux capabilities in Compose, and
use a read-only root filesystem. Only `/data` and the bounded `/tmp` tmpfs are
writable. The public image contains no browser runtime or legacy scraper dependencies.
Compose runs one web process, one lock-protected approved-source scheduler, and
one separately locked continuous acquisition scheduler against the same WAL
database; do not scale any of these services beyond one instance.

The scheduler polls every 60 seconds and drains every source that is currently due
in bounded batches. Each newly reviewed source defaults to `60` minutes between
successful runs. A failure uses bounded exponential backoff instead of waiting for
the normal cadence; partial or failed manifests cannot close jobs.

Schema v9 safely supports legitimate zero-opening boards. The first complete-empty
candidate probe remains unapproved; the second consecutive complete-empty probe may
register it. For a registered source, the first complete-empty run is anomalous and
cannot affect job lifecycle. The second is accepted as healthy and enters the normal
two-complete-manifest closure grace. Complete non-empty success resets the counter;
failed or incomplete runs preserve it without advancing it. See
[Zero-opening source verification](ZERO_OPENING_VERIFICATION.md).

Approval reuses its successful connector probe as the source's initial ingestion.
Source registration, normalized jobs, the complete sync record, policy review,
candidate approval, and supported company health commit in one transaction. A
mid-ingestion failure rolls the transaction back, and a successful approval schedules
the next fetch at the normal source interval instead of leaving it immediately due.

Each connector persists its complete ATS manifest, including non-U.S. roles, before
lifecycle reconciliation. Schema v6 assigns every job a versioned `us_eligibility`
decision. Public job listings, detail pages, statistics, company active-job counts,
and coverage job counts admit only `eligible`: definite locations in one of the 50
states or Washington, DC. Unknown and conflicting locations fail closed. Never move
this filter into a connector or before manifest reconciliation; doing so can falsely
close a still-open overseas role and corrupt source completeness.

## Local Docker Compose launch

Build and start the service:

```bash
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8000/readyz
```

Compose binds to loopback by default. For an internet-facing self-hosted beta,
keep that binding and place a TLS reverse proxy on the same host in front of it.
Do not expose port 8000 directly without network filtering and TLS.

The database is stored in the named volume `job_intel_data`. Do not use
`docker compose down -v` in normal operations because `-v` deletes that volume.

Operator commands use the same image and volume:

```bash
docker compose run --rm app python -m fortune_intel.cli init-db
docker compose run --rm app python -m fortune_intel.cli import-companies /data/imports/companies.csv \
  --collection "Public beta"
docker compose run --rm app python -m fortune_intel.cli import-dol /data/imports/lca.xlsx \
  --fiscal-year 2026
docker compose run --rm app python -m fortune_intel.cli \
  reschedule-sources --all --interval 60
```

Copy imports into the running container or mount a read-only import directory for
one-off commands. Do not bake licensed lists, disclosure files, credentials, or a
live database into the image.

## Environment configuration

Start from `config/production.env.example` and inject the populated values through
the hosting platform. Do not commit a populated environment file.

- `JOB_INTEL_DB`: SQLite path. It must be inside the persistent mount in production.
- `PORT`: HTTP port. Hosting platforms commonly inject this value.
- `JOB_INTEL_ENV`: set to `production` for the public service.
- `JOB_INTEL_ALLOWED_HOSTS`: comma-separated public hostnames accepted by the API.
  Production rejects an empty list and the unrestricted `*` value.
- `JOB_INTEL_PUBLIC_URL`: canonical external HTTPS URL. HTTPS is required in production.
- `JOB_INTEL_CONTACT_EMAIL`: monitored operator/correction address; required in production.
- `JOB_INTEL_CORS_ORIGINS`: optional comma-separated browser origins. Leave empty when
  the dashboard and API share one origin.
- `JOB_INTEL_RATE_LIMIT`: per-client requests per minute, from 10 to 10,000; default 120.
- `FORWARDED_ALLOW_IPS`: comma-separated proxy IPs whose forwarding headers Uvicorn
  may trust. The default trusts loopback only. Use `*` only if the application port
  is unreachable except through the hosting platform's trusted ingress.
- `HEALTHCHECK_URL`: optional full URL used by the container health script.
- `HEALTHCHECK_HOST`: Host header for the internal health probe. It must be one of
  `JOB_INTEL_ALLOWED_HOSTS`; by default the probe uses the first allowed host.
- `SCHEDULER_POLL_SECONDS`: due-work polling frequency; default `60` seconds.
- `SCHEDULER_CONCURRENCY`: maximum source fetches within a batch; default `4`.
- `SCHEDULER_BATCH_SIZE`: due sources loaded per batch; default `100`. All due
  batches are drained before the scheduler sleeps.
- `ACQUISITION_CADENCE_SECONDS`: immutable acquisition-plan cadence; default
  `86400` (daily), with a validated range of one hour through seven days.
- `ACQUISITION_POLL_SECONDS`, `ACQUISITION_BATCH_SIZE`,
  `ACQUISITION_LEASE_SECONDS`, and `ACQUISITION_MAX_BATCHES`: bounded checkpoint
  polling and draining; defaults are `60`, `50`, `300`, and `200`.
- `ACQUISITION_SCHEDULER_ACTOR` and `ACQUISITION_LEASE_OWNER`: auditable
  identities for plan creation and task leases. See the
  [continuous acquisition scheduler](ACQUISITION_SCHEDULER.md).
- `ATS_POLICY_URLS` and `ATS_POLICY_APPROVED_AT`: optional reviewed policy
  mappings that permit primary-provenance candidate probes. Empty means queue
  candidates without activating them.
- `SOURCE_SYNC_INTERVAL_MINUTES`: refresh interval assigned to a successfully
  activated source; default `60`.
- `WIKIMEDIA_USER_AGENT`: contactable identity used by exact-CIK website
  acquisition. Follow Wikimedia's format, for example
  `OpenRole-CIKBot/0.1 (https://your-site.example/contact)`. It is not a secret.
- `SEC_USER_AGENT`: contactable application and operator identity for the
  operator-triggered SEC Submissions and filing exact-CIK importers, for example
  `OpenRole-CIKBot/0.1 ops@example.org`. It is not a secret. The importer is
  defaults to one worker and five requests per second. Up to eight workers can
  hide response latency, but one shared limiter still enforces the SEC ceiling of
  ten request starts per second.

Run the SEC fallback only after the SEC company universe exists. It targets only
companies that still have no canonical website and records every accepted field:

```bash
export SEC_USER_AGENT='OpenRole-CIKBot/0.1 ops@example.org'
docker compose run --rm app python -m fortune_intel.cli \
  import-sec-websites --actor ops@example.org --dry-run
docker compose run --rm app python -m fortune_intel.cli \
  import-sec-websites --actor ops@example.org --concurrency 8
```

Preserve the reported `request_failures`, `invalid_payloads`, and `invalid_urls`
counts in the acquisition log. Rerun failed CIKs before claiming the batch was
checked. Empty SEC URL fields are a valid no-result and must not be replaced with
a generated domain.

### SEC filing evidence fallback

When the Submissions JSON website fields are empty, run the filing importer for
the remaining exact-CIK companies. Start with a dry run and a bounded batch. The
importer reads recent issuer-filed documents, accepts only an explicitly labeled
company website, and stores filing-level provenance. It does not overwrite an
existing website or use a search result/generated domain as evidence.

```bash
export SEC_USER_AGENT='OpenRole-CIKBot/0.1 ops@example.org'
docker compose run --rm app python -m fortune_intel.cli \
  import-sec-filing-websites --actor ops@example.org --limit 100 --dry-run
docker compose run --rm app python -m fortune_intel.cli \
  import-sec-filing-websites --actor ops@example.org --limit 100 --concurrency 4
```

Preserve the returned accession/form/date evidence and every failure/conflict
counter in the batch log. Use the returned `safe_resume_after_cik` as the
exclusive resume cursor. Exhausted 429/5xx/network failures hold that cursor for
a later retry; permanent 4xx archive gaps are reported without blocking progress:

```bash
docker compose run --rm app python -m fortune_intel.cli \
  import-sec-filing-websites --actor ops@example.org \
  --after-cik 0000123456 --limit 100
```

To run bounded ATS discovery immediately for only the verified seeds written by
that invocation, opt in explicitly:

```bash
docker compose run --rm app python -m fortune_intel.cli \
  import-sec-filing-websites --actor ops@example.org --limit 100 \
  --discover-new --discovery-concurrency 4
```

The discovery phase records candidates with `terms_status=review_required`; it
does not approve sources or fetch jobs. Review applicable collection terms and
run the normal candidate approval/probe workflow separately. Never schedule the
filing acquisition command hourly: SEC filings are evidence acquisition inputs,
while only reviewed ATS sources belong in the hourly job scheduler.

SEC fetch concurrency is bounded at eight workers. The workers share one global
request-start limiter; keep `--rate-per-second` at or below ten. The independent
`--discovery-concurrency` value applies only to the later verified-domain crawl.

After every acquisition, discovery, approval, or ingestion batch, export the strict
company checklist and retain it with the batch logs:

```bash
docker compose run --rm app python -m fortune_intel.cli coverage-audit \
  --status incomplete --format csv > coverage-remediation.csv
```

Work each row's `next_action` in order. Do not use `coverage_disposition=supported`
as a release-completion signal; only the separate `covered` field means identity,
portal, complete ingestion, opening-date provenance, and freshness all passed.

There are currently no application secrets required for the read-only public API.
Keep any future source credentials in the platform secret store, never in an image,
environment file committed to Git, or SQLite backup.

## Render, Fly.io, Railway, and similar platforms

Use the repository `Dockerfile` rather than a platform-specific buildpack:

1. Create one web service and one continuously running scheduler/worker service
   from the repository root. The worker command is `python -m fortune_intel.cli
   run-scheduler --poll-seconds 60 --concurrency 4 --batch-size 100`.
2. Attach the same persistent disk/volume at `/data` to both services before the
   first production start. If a provider cannot safely mount one volume to both,
   this SQLite beta cannot run there; use a single-host Compose deployment or move
   to PostgreSQL.
3. Set `JOB_INTEL_DB=/data/job_intel.db`.
4. Set `JOB_INTEL_ENV=production`, the exact `JOB_INTEL_ALLOWED_HOSTS`, an HTTPS
   `JOB_INTEL_PUBLIC_URL`, and a monitored `JOB_INTEL_CONTACT_EMAIL`.
5. Let the platform provide `PORT`, or set it to `8000`.
6. Set `HEALTHCHECK_HOST` to an allowed public hostname and configure the platform
   health path as `/readyz`.
7. Configure `FORWARDED_ALLOW_IPS` for the actual trusted ingress path; do not
   blindly trust forwarding headers on a directly reachable container.
8. Set both web and scheduler minimum and maximum replicas to one. Disable rolling
   deployment modes that briefly run old and new containers against the same
   SQLite file; use a recreate or stop-then-start deployment.
9. Terminate HTTPS at the platform ingress and restrict direct container access.
10. Configure a 30-second termination grace period and automatic restart on failure.
11. Enable persistent-volume snapshots if offered, in addition to database backups.

The Python base image and Python dependency set are digest/hash pinned. Update
them in a reviewed dependency-refresh change and rerun tests, audit, image build,
and smoke checks; do not silently retag a production release.

Provider filesystems outside the mounted disk are ephemeral. A deployment is not
durable if `/data` is not a persistent mount. Some providers attach a volume to one
region or machine; document that placement and recovery procedure before launch.

## Backups

Never copy a live SQLite file with plain `cp`: committed data may still be in its
WAL file. The supplied script uses SQLite's online backup API and verifies the
result:

```bash
docker compose exec app /app/scripts/backup_db.sh /data/job_intel.db /data/backups
docker compose exec app ls -lh /data/backups
```

Copy the resulting `.db` file and recorded SHA-256 digest off the application
volume. Keep at least seven daily and four weekly copies in separately controlled
storage. A backup that lives only on the same volume is not a recovery copy.

For a provider deployment, run the same script through its secure shell or one-off
job facility. Ensure the one-off process mounts the same `/data` volume and does
not start another API server.

## Restore drill

Restoration replaces the active database. Take a final backup and stop the web
service first. For Compose, place the selected backup under `./backups`, then run:

```bash
docker compose stop scheduler app
docker compose run --rm --no-deps \
  -v "$PWD/backups:/restore:ro" \
  --entrypoint python app \
  /app/deploy/restore_db.py \
  /restore/job_intel-YYYYMMDDTHHMMSSZ.db /data/job_intel.db --force
docker compose up -d app scheduler
docker compose exec app python /app/deploy/healthcheck.py
```

The restore helper verifies the source, writes a complete temporary database,
atomically replaces the destination, and removes obsolete WAL/SHM sidecars. Do not
run it while the API or a collector is using the database. Perform and document a
restore drill before launch and at least quarterly.

## Updates and rollback

1. Create and export a verified backup.
2. Record the current image digest or Git revision.
3. Build the replacement image and run its tests in CI.
4. Stop the old instance, start the new image against the existing volume, and
   verify `/readyz` plus representative searches.
5. If validation fails, stop the new instance, restore the previous image, and
   restore the database backup if the release changed data.

The schema initializer applies transactional, versioned migrations and fails
readiness when required tables, columns, integrity, or the schema version disagree.
Still back up first and test every migration against a restored production copy.

## Monitoring and incident response

Monitor container health, restart count, HTTP 5xx responses, disk usage, database
growth, collector failures, freshness, and backup age. Logs go to stdout/stderr and
should be retained by the hosting platform.

If the service becomes unhealthy:

1. Stop collectors and other operator jobs.
2. Preserve logs and take a volume snapshot if possible.
3. Check free disk space and volume writability.
4. Run an online backup; if that fails, stop the app before further database work.
5. Restore the last verified backup or roll back the image.
6. Record affected freshness windows and publish a correction if displayed evidence
   was wrong.

## Explicit SQLite limitation

SQLite is appropriate for this low-write, single-instance beta, not for horizontal
scale. Multiple replicas, multiple Uvicorn workers, rolling deployments with overlap,
or concurrent scheduler fleets can cause lock contention and unsafe lifecycle
decisions. PostgreSQL migration requires a PostgreSQL repository implementation,
schema migrations, transaction/locking tests, backup automation, and a cutover plan;
changing only `JOB_INTEL_DB` is **not** a PostgreSQL migration.
