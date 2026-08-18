# Release-candidate verification

Verification date: 2026-08-10 (America/New_York)

## Passed

- 272 automated tests covering API boundaries, source connectors, DOL/SEC/website
  import, ATS URL variants, bounded network discovery, source approval, sponsorship
  rules, lifecycle closure, schema migration, production isolation, and scheduling.
- Ruff lint and formatting checks for the production platform and its tests.
- Python bytecode compilation for source and tests.
- Dependency audit of the hash-locked runtime: no known vulnerabilities found.
- Static security scan: zero critical, high, medium, or low findings after the
  DOM renderer was changed to avoid `innerHTML`.
- Browser acceptance of dashboard loading, real-data counts, provenance-aware
  opening dates, the 8,000-company directory, and the 26,100-employer H-1B directory.
- Schema v6 adds versioned, fail-closed U.S. location decisions. Public job/detail,
  statistics, company-count, and coverage surfaces expose only definite 50-state/DC
  roles while complete global manifests remain private for lifecycle correctness.
- Schema v9 persists separate candidate and registered-source complete-empty streaks.
  One empty result cannot approve a source or close jobs; two consecutive complete
  empty observations establish a legitimate zero-opening board, after which the
  existing two-manifest closure grace still applies.
- Candidate approval reuses its successful complete probe as an atomic initial
  ingestion. Source, jobs, sync success, policy audit, candidate review, and supported
  coverage roll back together on failure; the scheduler starts at the normal cadence
  without an immediate duplicate fetch.
- Local concurrency smoke: 100/100 successful job-search responses at 20-way
  concurrency; observed 733.7 requests/second and 53.1 ms p95 on this development
  machine. This is a smoke result, not a production capacity commitment.
- Compose configuration rendering, clean release snapshot creation, checksum
  verification, readiness, OpenAPI, sitemap, and self-hosted API guide checks.

## Environment limitation

The Docker configuration was validated and the pinned upstream image manifest was
resolved, but the release image could not be built locally because the Docker
daemon was not running. CI or the deployment host must build and smoke-test the
image before launch.

## Real-data coverage snapshot

On 2026-08-10 the local non-public index contained:

- 8,000 distinct canonical companies after eight reviewed pilot-to-SEC identity merges;
  Gopuff remains a non-SEC private-company source.
- 26,100 DOL H-1B legal employers and 461,730 certified LCA worker positions.
- 4,507 exact-CIK verified company websites. Of these, 3,032 are current
  exact-filing matches added by the annual-filing evidence importer; 101 declared
  deep links were canonicalized to their verified HTTPS origins and 27 unsafe or
  ambiguous matches were invalidated after adversarial revalidation.
- All 7,999 exact-CIK companies traversed through the SEC submissions/annual-filing
  path. Permanent historical archive gaps are separated from retryable 429/5xx or
  network failures, and no current filing-derived website retains a deep path.
- 385 enabled Greenhouse, Lever, Ashby, SmartRecruiters, and Workday sources across
  352 companies; every enabled source has at least one prior successful manifest.
- 66,660 internally active postings retained for complete-manifest reconciliation.
  The public U.S.-only view contains 38,856 jobs: 36,832 with ATS-supplied opening
  dates and 2,024 explicitly labeled by first observation because the ATS did not
  publish an opening date. Another 12,544 are definite non-U.S. and 15,260 are
  ambiguous or conflicting, so both groups fail closed.
- 434 companies with evidenced ATS candidates and 99 companies passing every
  strict identity, portal, manifest, ingestion, opening-date, and freshness gate.

The raw government files, source registry, and database are ignored local artifacts
and are not distributed in Git.

## Per-company completion audit

Coverage is verified company by company through seven ordered gates: exact identity,
official portal provenance, evidenced ATS discovery, approved complete manifest,
successful ingestion, source-supplied opening dates for every active role, and
freshness within the configured collection cadence. A company is `covered` only when
all seven gates pass. In particular, a populated directory row, a known website, or
the operational `supported` disposition cannot independently produce that label.

Generate the complete machine-readable checklist with:

```bash
job-intel --database /data/job_intel.db coverage-audit --format json > coverage-audit.json
job-intel --database /data/job_intel.db coverage-audit --format csv > coverage-audit.csv
job-intel --database /data/job_intel.db coverage-audit \
  --status incomplete --format csv > coverage-remediation.csv
```

The read-only public equivalent is `GET /api/coverage/companies`. Public records omit
portal and connector URLs; the operator CLI retains the verified portal seed so the
underlying evidence can be reviewed. Each row includes the first failed gate as
`next_action`, which makes the 8,000-company workflow resumable without treating
unchecked companies as complete.

## External launch blockers

- Publish a new clean-history repository snapshot. The private development Git
  history still contains deleted company-list CSVs.
- Provide a domain/TLS ingress, persistent volume, monitoring destination,
  off-volume backup destination, and monitored correction email.
- Continue exact-source acquisition for the 7,615 companies whose strict audit
  still requires verified official-job-portal provenance.
- Complete the production backup/restore drill before changing launch status to go.

The deployable scope is a small allowlisted public ATS beta. It is not an index of
all Fortune companies, all employers, or all H-1B sponsors, and it does not include
USCIS petition ingestion yet.
