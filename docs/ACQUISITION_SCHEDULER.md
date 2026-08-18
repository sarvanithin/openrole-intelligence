# Continuous acquisition scheduler

The `acquisition-scheduler` service continuously resumes bounded company-level
checkpoints. It is separate from the hourly approved-job-source scheduler and
processes three stages in order:

1. acquire an official website/career seed only from a unique exact SEC CIK;
2. crawl only verified company-domain seeds and persist supported ATS candidates
   or passive unsupported-family fingerprints;
3. probe and activate only candidates with primary-company provenance and an
   operator-configured vendor policy URL.

## Safety and durability contract

- URLs are observed from exact public provenance; no ATS host, tenant, or path is
  guessed. A company without a unique exact CIK receives an auditable terminal
  `identity_unavailable` checkpoint.
- Third-party discovery leads remain passive fingerprints and are never eligible
  for activation.
- Unsupported portals remain queued by fingerprint family for future connector
  and policy work.
- Task snapshots are immutable. SQLite leases allow another worker to reclaim an
  expired checkpoint after a crash without processing it twice concurrently.
- Retryable failures use bounded exponential backoff from one minute to six
  hours. Exhausted and permanent failures remain queryable as dead letters.
- Exact-reviewed H-1B companies are processed first, followed by stale sources
  that previously completed successfully, exact-CIK companies, then the general
  missing-coverage inventory.
- Candidate activation requires an explicit `KIND=URL` vendor-policy mapping and
  a timezone-aware policy review timestamp. Complete-manifest approval rules,
  including two independent complete-empty observations, still apply.
- The continuous and legacy discovery schedulers share the same singleton lock;
  run only one of them.

## Deployment

Populate `config/production.env.example` through the deployment secret/config
store. `WIKIMEDIA_USER_AGENT` must contain a contactable operator identity.
Leave `ATS_POLICY_URLS` empty until the applicable vendor policies have been
reviewed. When configured, use comma-separated entries such as
`greenhouse=https://.../policy,lever=https://.../policy`, and set
`ATS_POLICY_APPROVED_AT` to the exact review timestamp.

Start the public app, approved-source scheduler, and acquisition scheduler:

```bash
docker compose --env-file /secure/path/production.env \
  up -d app scheduler acquisition-scheduler
docker compose logs -f acquisition-scheduler
```

Run directly when not using Compose:

```bash
job-intel --database /data/job_intel.db run-acquisition-scheduler \
  --poll-seconds 60 \
  --cadence-seconds 86400 \
  --batch-size 50 \
  --lease-seconds 300 \
  --max-batches 200 \
  --actor continuous-acquisition-scheduler \
  --lease-owner continuous-acquisition-1 \
  --user-agent "FortuneJobs/1.0 operations@example.org" \
  --policy greenhouse=https://reviewed.example/greenhouse-policy \
  --policy-approved-at 2026-08-12T00:00:00+00:00 \
  --interval 60
```

Omit every `--policy` and `--policy-approved-at` argument to run website and
discovery acquisition while leaving all candidates queued for manual review.

Inspect auditable progress without starting a worker:

```bash
job-intel --database /data/job_intel.db acquisition-metrics
```

The metrics include coverage dispositions, stage/status checkpoints,
dead-letter outcome counts, supported-candidate queues by kind, and passive
unsupported queues by family. Do not scale this scheduler above one instance
while SQLite is the production persistence layer.
