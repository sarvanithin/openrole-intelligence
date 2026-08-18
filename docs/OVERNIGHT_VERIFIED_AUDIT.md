# Overnight verified portal audit

`scripts/run_overnight_verified_audit.sh` is a ten-hour, bounded, **read-only**
audit for the observed company career URLs already in the live registry. It
checks URLs directly observed from the supplied career URL registry and every
currently enabled source, then emits a durable reachability report. It does not
import URLs, create candidates, approve portals, change source health, or fetch
jobs. The existing launchd acquisition worker and hourly source scheduler remain
the only processes that modify those records.

The script holds an exclusive run-directory lock and opens SQLite with
`mode=ro` plus `PRAGMA query_only`, so it can safely operate alongside the live
services. It accepts only public HTTP(S) targets, does not follow redirects, has
a 15-second per-request timeout, a six-worker limit, and starts no more than one
request per hostname per second. An interrupted run resumes from its JSONL
results when restarted with the same run directory.

## Start

From the repository root, run:

```bash
mkdir -p data/overnight_runs/2026-08-15-overnight
nohup scripts/run_overnight_verified_audit.sh \
  data/overnight_runs/2026-08-15-overnight \
  > data/overnight_runs/2026-08-15-overnight/runner.log 2>&1 &
```

The maximum duration is hard-capped at ten hours. To resume after a machine or
network interruption, run the same command again with the same directory.

For a durable Mac launchd run (rather than a terminal background process), copy
`deploy/com.openrole.intelligence.overnight-verified-audit.plist.template` to
`~/Library/LaunchAgents/`, replace `__PROJECT_DIRECTORY__` and
`__RUN_DIRECTORY__`, create the run directory, then run
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.openrole.intelligence.overnight-verified-audit.plist`.
The process exits normally after at most ten hours; it is not configured to
restart after it exits. Inspect it with
`launchctl print gui/$(id -u)/com.openrole.intelligence.overnight-verified-audit`.

## Output

- `portal-results.jsonl` — one exact observed URL and its reachability result.
- `source-health-before.json` / `source-health-after.json` — snapshots for
  identifying sources the normal lock-protected scheduler must recover.
- `summary.json` — counts of reachable URLs, redirects, HTTP errors, and network
  failures. `deadline_reached: false` means all selected targets were audited.

For active source recovery, leave the existing `com.openrole.intelligence.scheduler`
launch agent running. It already owns `live_index.scheduler.lock`; never start a
second `run-scheduler` process. The audit is deliberately separate so it cannot
race, restart, or rewrite source state.
