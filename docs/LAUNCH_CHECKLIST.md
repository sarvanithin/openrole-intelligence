# Public beta launch checklist

Treat any unchecked go/no-go item as a launch blocker or document the person who
accepted the risk and its remediation date.

## Product and data

- [ ] Production contains no synthetic demo companies or synthetic sponsorship facts.
- [ ] Every displayed job links to the canonical employer or public ATS posting.
- [ ] Sponsorship tiers show their method version, data-as-of date, source, and limits.
- [ ] Every enabled evidence release (currently DOL; USCIS is planned), fiscal
      period, checksum, and import result is recorded.
- [ ] Ambiguous legal-employer matches are unresolved or manually reviewed, not
      silently fuzzy-matched.
- [ ] Current-posting policy is not claimed for jobs whose descriptions were not fetched.
- [ ] Licensed company lists and job-description redistribution have been reviewed.
- [ ] Methodology, correction, opt-out, and takedown contacts are public.

## Collector safety

- [ ] Only approved career sources can be scheduled.
- [ ] Source terms, robots policy, rate limit, owner, and kill switch are recorded.
- [ ] DNS, redirects, and browser requests cannot reach private, loopback, link-local,
      or cloud metadata networks.
- [ ] Partial, failed, blocked, and anomalously empty crawls cannot close jobs.
- [ ] Closure requires independently complete manifests and the configured time grace.
- [ ] Canary sources and count-drift alerts are enabled.

## Tests and release artifact

- [ ] Unit, API, importer, identity, lifecycle, and fixture tests pass in CI.
- [ ] Dependency and secret scans pass.
- [ ] The release image is built from a reviewed commit and tagged immutably.
- [ ] The container runs as non-root with a read-only root filesystem.
- [ ] The image contains no `.env`, credentials, database, raw disclosure, licensed
      collection, test cache, or local backup.
- [ ] The API and dashboard were smoke-tested from the release image.

## Deployment

- [ ] Exactly one replica and one Uvicorn worker are configured.
- [ ] `/data` is a persistent volume and `JOB_INTEL_DB=/data/job_intel.db`.
- [ ] Deployment is recreate/stop-then-start, with no overlapping SQLite writers.
- [ ] HTTPS is enforced at trusted ingress; the application port is not directly public.
- [ ] `JOB_INTEL_ENV=production`; allowed hosts, public HTTPS URL, and contact email
      pass startup validation.
- [ ] `FORWARDED_ALLOW_IPS` trusts only the actual ingress path.
- [ ] `/readyz` is the readiness endpoint and restart-on-failure is enabled.
- [ ] Exactly one lock-protected scheduler instance is configured.
- [ ] Reviewed production sources use the intended cadence (hourly by default),
      and a full due-source drain was observed in staging.
- [ ] Shutdown grace is at least 30 seconds.
- [ ] Disk capacity and regional/host attachment of the volume are documented.

## Backup and recovery

- [ ] A SQLite online backup completed and passed `PRAGMA integrity_check`.
- [ ] The backup and SHA-256 digest were copied off the application volume.
- [ ] Retention is at least seven daily and four weekly backups.
- [ ] A restore drill succeeded against the release image.
- [ ] Recovery time, recovery point, responsible operator, and escalation contact are
      documented.
- [ ] Operators know never to use `docker compose down -v` for routine maintenance.

## Monitoring and support

- [ ] Uptime, 5xx rate, restart count, disk usage, freshness, collector success, and
      backup age have alerts.
- [ ] Logs are retained outside the container and include a release revision.
- [ ] A public status/correction process exists for stale or inaccurate evidence.
- [ ] An operator can disable collection without disabling the read-only job explorer.
- [ ] The first 24 hours and first scheduled collection window have named coverage.

## Go / no-go record

- Release revision or image digest:
- Database backup identifier:
- Data release/as-of dates:
- Launch operator:
- Reviewer:
- Launch time (UTC):
- Accepted risks and remediation dates:
