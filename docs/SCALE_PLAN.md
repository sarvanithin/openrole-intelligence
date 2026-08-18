# Path from public beta to broad company coverage

The company universe and job-source coverage are separate denominators. The SEC
import can enumerate thousands of public companies, while a company is job-covered
only after a reviewed, deterministic career source completes a manifest.

## Current capacity

The local SQLite scheduler is appropriate for a reviewed beta fleet. It now drains
all sources that are due before sleeping and isolates each source failure. SQLite
remains intentionally single-node and should not be presented as a 5,000-source
production scheduler.

## Required scale architecture

Before claiming broad multi-thousand-company coverage:

1. Store companies, sources, jobs, manifests, and evidence in managed PostgreSQL.
2. Dispatch source work through a durable queue; claim work with transactional
   leases such as `FOR UPDATE SKIP LOCKED`, with bounded retries and dead letters.
3. Run horizontally scalable workers with per-host rate limits, circuit breakers,
   and source-specific concurrency budgets.
4. Expand beyond Greenhouse, Lever, Ashby, SmartRecruiters, Workday, and the
   policy-held Oracle Recruiting implementation to iCIMS, Taleo, Eightfold, and
   standards-based JSON-LD feeds after terms review. Do not schedule Oracle
   Candidate Experience sources while Oracle's reference labels them internal-use.
5. Maintain an auditable discovery registry with supported, unsupported, blocked,
   opted-out, and stale states. Never count an unverified careers URL as coverage.
6. Publish denominator metrics: companies in scope, sources reviewed, sources
   healthy, jobs observed, opening-date provenance, and freshness SLO attainment.
7. Complete a 30-day soak with backup/restore drills before calling the fleet
   production-complete.

The SQLite beta now implements this discovery-state vocabulary and a bounded,
robots-aware candidate finder for six deterministic ATS families. This inventories
coverage honestly; it does not remove the PostgreSQL/durable-queue requirement for
multi-thousand-source production operation.

## Suggested service objectives

- 99% of healthy sources refreshed within their declared interval.
- 95% of complete source manifests finish within 15 minutes of dispatch.
- Zero closures caused by failed, partial, blocked, or anomalously empty runs.
- Every public date labeled as source-opened or first-observed provenance.
- Every sponsorship tier reproducible from versioned facts and rules.

Commercial Fortune and Inc. collections must remain bring-your-own licensed
filters. They are not crawling targets or redistributable product data.
