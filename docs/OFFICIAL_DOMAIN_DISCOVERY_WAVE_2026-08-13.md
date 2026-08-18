# Official-domain discovery and activation wave — 2026-08-13

This report covers only fresh network crawls performed by
`codex-official-domain-wave-h1b@local` and
`codex-official-domain-wave-general@local`. It excludes acquisition-plan tasks
that returned a cached disposition under the 24-hour recent-discovery guard.

## Fresh discovery

- Companies freshly crawled: 322
  - Exact H-1B priority: 243
  - General candidate-bearing cohort: 79
- Official seeds checked: 373
- Company-controlled pages checked: 1,472
- Fresh dispositions: 104 candidate, 133 unsupported, 85 blocked
- Exact supported ATS observations persisted/reconfirmed: 119

Every candidate was observed on a stored official company website/careers seed
or an official redirect. No ATS URL was guessed and passive fingerprints were
not counted as verified candidates.

## Safety gates and activation

- Approved-family candidates probed: 88
- Complete manifests activated: 60 sources across 55 companies
- Probe failures retained as discovered: 28
- Complete-empty first observations: 1
- Complete-empty final pending: 0 (the source activated only after a second
  independent complete-empty observation)
- Jobs transactionally ingested by the 60 sources: 14,523
- Candidates deliberately not probed: 31
  - iCIMS observations without an implemented connector: 25
  - ADP Workforce Now observations retained under the documented policy hold: 6

Exact H-1B phase: 12 sources across 10 companies, 3,279 jobs. General phase:
48 sources across 45 companies, 11,244 jobs.

## Checkpoint and validation

- Pre-wave database checkpoint:
  `data/backups/live_index-before-official-domain-wave-20260813.db`
- SQLite `PRAGMA integrity_check`: `ok`
- Discovery, bulk-approval, and source-approval tests: 23 passed
- Post-wave database snapshot at validation: 8,000 companies, 603 sources,
  135,354 jobs (133,308 active)

No connector implementation file was changed. The live database was mutated by
the existing transactional discovery and source-approval services.
