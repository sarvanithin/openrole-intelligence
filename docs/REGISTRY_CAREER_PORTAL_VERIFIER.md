# Registry career portal verifier

`verify-registry-career-portals` is a durable, high-volume worker for the
passive `user_supplied_career_url_registry` inventory. It is intentionally
separate from the slower company acquisition scheduler. It processes modern
custom/unrecognized entries, registry-owned `unknown_external` entries, and
older registry rows that do not have a `proposed_kind` classification; it never
processes third-party lead imports.

For each custom or unrecognized registry URL it:

1. accepts only an absolute HTTPS URL whose hostname resolves exclusively to
   public IP addresses;
2. fetches the exact page without following redirects;
3. requires an exact normalized company-name match in the returned public HTML;
4. writes the URL only into an empty `companies.career_url` field as a verified
   career discovery seed.

Each newly verified company ID is immediately passed to the existing bounded
discovery pipeline (at most four concurrent crawls), so its candidate and
passive-fingerprint results do not wait for a daily acquisition plan. If
explicit reviewed policies are configured, only Greenhouse, Workday, Lever,
Ashby, SmartRecruiters, and Oracle Recruiting candidates from that exact
handoff enter the existing complete-manifest approval gate. Official-structured
and unsupported/custom results stay review-only. A failed or incomplete
manifest remains `discovered`; it is never enabled by this worker.

Each terminal result is stored on the fingerprint as `verification_status`.
On restart, the worker processes only rows still marked `unverified`; there is
no in-memory progress state to lose.

Run one bounded drain manually:

```bash
job-intel --database data/live_index.db verify-registry-career-portals \
  --actor registry-career-portal-service \
  --batch-size 200 --concurrency 8 --max-batches 40 --pace-seconds 0.5
```

The workspace wrapper is
`scripts/run_registry_career_portal_verifier.sh`. Its defaults drain up to
8,000 URLs per run in 200-row durable batches, with at most eight concurrent
network checks and a half-second pause between batches. Environment variables
`REGISTRY_PORTAL_BATCH_SIZE`, `REGISTRY_PORTAL_CONCURRENCY`,
`REGISTRY_PORTAL_MAX_BATCHES`, and `REGISTRY_PORTAL_PACE_SECONDS` can tune
those bounds.

To enable the existing manifest gate for policy-approved standard connectors,
set `ATS_POLICY_URLS` to comma-separated `KIND=URL` entries and
`ATS_POLICY_APPROVED_AT` to the policy review timestamp. They are supplied to
the CLI, not stored in source code. Optional `--approval-concurrency` is capped
at four and `--interval` controls the normal source-sync schedule.

The installed launchd service
`com.openrole.intelligence.registry-career-portals` starts at load and checks
for remaining work every hour. It does not restart continuously after a clean
drain, so an empty queue stays quiet until new registry inventory arrives.
