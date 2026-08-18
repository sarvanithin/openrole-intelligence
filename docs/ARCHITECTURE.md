# Architecture

## Goal

Turn a six-hour, two-Sheet scraping loop into a replayable intelligence pipeline
where freshness, provenance, and sponsorship reasoning can be inspected.

```text
Company collections + career-source registry
                  │
        source-isolated scheduler
                  │
 approved deterministic public ATS APIs (fixed hosts)
                  │
       raw artifact + crawl manifest
                  │
 normalize → stable identity → lifecycle → classification
                  │
 jobs database ← employer resolution ← DOL / USCIS evidence
                  │
             API + public UI
                  │
       optional Sheets / CSV exports
```

## Current vertical slice

The first implementation uses SQLite so a contributor can run it in seconds. The
repository boundary is designed for a PostgreSQL adapter later. The original
collectors are bridged through `services/ingestion.py`; Google Sheets is retained
only for backward compatibility, not used by the new API.

### Identity and lifecycle

- Exact identity: `(company, source, external_job_id)` so ATS IDs can overlap across tenants.
- URL fallback: canonical URL with common tracking parameters removed.
- Cross-source clustering: normalized company + title + location fingerprint.
- Title edits update an existing record and never change exact identity.
- A complete, non-empty source manifest increments absence counters.
- Two complete omissions close a job. Failures and empty anomalies do not.
- Reappearance resets the counter and reopens the record.

### Trust boundaries

Every source should eventually have a policy registry containing its owner,
terms URL, robots check, rate limit, parser version, and kill switch. Scraped text
is untrusted input. It must not be rendered as raw HTML, executed, used as an
unvalidated outbound URL, or interpreted as instructions by an agent.

## Target beta architecture

- PostgreSQL with full-text search and `pg_trgm`.
- Per-source tasks claimed with `FOR UPDATE SKIP LOCKED`; no Redis initially.
- Content-addressed, compressed raw artifacts in local/S3-compatible storage.
- Versioned parsers that can replay raw captures without re-crawling.
- Greenhouse, Lever, Ashby, SmartRecruiters, Workday, and Oracle Recruiting
  deterministic public endpoints first.
- Workday source identities preserve the exact public host family. Both
  `tenant.wdN.myworkdayjobs.com/Site` and
  `wdN.myworkdaysite.com/recruiting/tenant/Site` map to a fixed
  `host|tenant|site` key and same-host CXS endpoint; discovery and ingestion do
  not synthesize a hostname or tenant alias.
- JSON-LD `JobPosting` second, HTML third, Playwright only as a bounded fallback.
- DOL release manifests with checksums and `data_as_of` metadata; USCIS import is
  a future evidence source and must not be claimed until implemented and reviewed.
- Explicit organization-to-legal-employer links with reviewer evidence.
- A manual queue for ambiguous aliases; never silently fuzzy-match legal entities.

## Operational invariants

1. A failed or partial crawl cannot close jobs.
2. A normally populated source returning zero is an anomaly, not truth.
3. All public assessments carry a method version and reason codes.
4. Employer history cannot override a current explicit “no sponsorship” statement.
5. Licensed company collections are imported privately and not redistributed.
6. Every public job links to its canonical employer posting.

## Scale path

Start with 75–100 companies and strong fixture coverage. Add workers horizontally
only when source-level latency requires it. Kafka, Kubernetes, Elasticsearch, and
LLM-based extraction are deliberately excluded from the MVP: they add operational
surface before accuracy and source health are measurable.
