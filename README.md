# OpenRole Intelligence

[![Platform CI](https://github.com/sarvanithin/openrole-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/sarvanithin/openrole-intelligence/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)

**Open-source job intelligence for verified U.S. openings and explainable H-1B
sponsorship evidence.**

OpenRole replaces spreadsheet-based job tracking with a transparent, local-first
pipeline: verified employer sources, complete-manifest ingestion, U.S.-only public
results, job freshness tracking, and evidence users can inspect. It is the next
iteration of Fortune Job Scraper, designed to be useful to job seekers and safe to
extend in public.

> **No black-box sponsorship claims.** A Tier A result means the current job posting
> contains an explicit, job-specific immigration sponsorship offer. A current posting
> that denies sponsorship is Tier E and overrides employer history. Ambiguous or
> conditional wording is not promoted to Tier A.

## Why OpenRole?

- **Verified sources, not guessed URLs.** Sources are registered, policy-reviewed,
  probed for complete manifests, and attributable to an employer or supported ATS.
- **U.S. roles only.** Public endpoints fail closed for ambiguous, worldwide, and
  non-U.S. locations.
- **Evidence you can challenge.** Every tier has a reason, excerpt, and rule version;
  employer history is never represented as a sponsorship guarantee.
- **Built for contributions.** Run the synthetic demo locally, improve a connector,
  report a bad source, or help make a company’s coverage more reliable.

## Get involved

Contributions are welcome—especially source corrections, connector work, tests,
documentation, and accessibility improvements.

- Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.
- Report a product bug or bad source through [GitHub Issues](https://github.com/sarvanithin/openrole-intelligence/issues/new/choose).
- Read the [methodology](docs/METHODOLOGY.md), [architecture](docs/ARCHITECTURE.md),
  and [security policy](SECURITY.md) before proposing changes to collection or evidence logic.

> Sponsorship history is not a promise that an employer will sponsor a specific
> role. The platform reports evidence tiers and the reason for each assessment;
> it does not provide legal advice.

## What works today

- SQLite-backed company, job, sync-run, and sponsorship-evidence records.
- Company/board-scoped source identities, canonical URLs, content hashes, and cross-source
  cluster fingerprints.
- Safe closure primitive: a job can close only after two trusted complete manifests
  from a collector that explicitly guarantees completeness. An empty board must first
  pass independent two-observation verification; partial collectors cannot close jobs.
- Evidence tiers A–E, with current-job text taking priority over employer history.
- User-supplied company collection importer (the project does not redistribute a
  licensed Fortune list).
- Strict DOL LCA CSV/XLSX importer with provisional normalized-name candidates.
- Searchable official DOL H-1B employer directory, with exact release provenance.
- Public SEC EDGAR ticker-company importer for a legally reusable 5,000+ company universe.
- Source-supplied opening dates kept separate from first-observed timestamps.
- A versioned, fail-closed U.S. location classifier: only definite roles in the 50 states
  or Washington, DC reach public job, company-count, statistics, and coverage APIs.
- FastAPI search endpoints and a responsive evidence-first job explorer.
- Bridge to the original Workday, Eightfold, Greenhouse, Lever, iCIMS, Taleo,
  SmartRecruiters, Plaid, and generic collectors.
- A completely synthetic demo dataset for contributors and screenshots.

## Quick start

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

job-intel init-db
job-intel seed-demo
job-intel serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Interactive API
documentation is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

Run tests with:

```bash
pytest
```

## What to contribute

| Area | Useful contribution |
|---|---|
| Employer coverage | Report an official company career page or correct an existing source. |
| ATS connectors | Add a documented public endpoint with complete-manifest pagination tests. |
| Data quality | Improve location, freshness, or sponsorship-evidence regression coverage. |
| Product | Improve search, accessibility, docs, observability, or deployment tooling. |

Please do not submit credentials, private applicant information, circumvented pages,
or guessed company URLs. See [CONTRIBUTING.md](CONTRIBUTING.md) for the review path.

## Bring your own company universe

Lists are versioned inputs rather than hard-coded product identity. This avoids
redistributing a commercial ranking and lets users work with a licensed Fortune
export, an SEC-based list, or their own targets.

```bash
job-intel import-companies companies.csv \
  --collection "My licensed company list" \
  --year 2026
```

Recognized columns include `Company Name`, `Career Search URL`, `Platform Type`,
`Website URL`, and `Rank`. See `data/sample/companies.csv` for the format.

There is no official “Fortune 5000” list. Fortune's broadest U.S. revenue list is
the licensed Fortune 1000; Inc. publishes a separate Inc. 5000. Neither commercial
compilation is redistributed by this project. For an open public universe, import
the SEC company-ticker file instead:

```bash
curl -H 'User-Agent: Your Product contact@example.org' \
  -o company_tickers.json https://www.sec.gov/files/company_tickers.json
job-intel import-sec company_tickers.json --year 2026
```

## Import official DOL history

Download an H-1B disclosure file from the
[DOL OFLC performance-data page](https://www.dol.gov/agencies/eta/foreign-labor/performance),
then import one cumulative/annual release per fiscal period:

```bash
job-intel import-dol path/to/LCA_Disclosure_Data_FY2025_Q4.xlsx \
  --fiscal-year 2025
```

Do not append Q1 + Q2 + Q3 + Q4 from the same fiscal year: DOL quarterly files
may be cumulative. LCA files contain H-1B, H-1B1, and E-3 records; the importer
requires `VISA_CLASS == H-1B`, excludes certified-withdrawn cases, validates worker
counts, and deduplicates case numbers. Name-only joins are stored as provisional
candidate matches below the scoring threshold. A reviewed alias/address/domain
link is required before they influence a public sponsorship tier.
Every qualified employer is also indexed in `/h1b-employers`, including employers
that do not yet have a mapped career source.

## Register and sync approved deterministic sources

Production ingestion is allowlisted source-by-source, including a terms review,
an accountable owner, and a minimum refresh interval:

```bash
job-intel add-source \
  --company "Example Company" \
  --kind greenhouse \
  --board-token example \
  --url https://boards.greenhouse.io/example \
  --terms-url https://example.com/terms \
  --policy-approved-at 2026-08-06T12:00:00Z \
  --owner-contact data-owner@example.org
job-intel sync-sources
job-intel run-scheduler
```

New registrations default to a 60-minute refresh cadence. The scheduler polls for
due work every minute and drains all due sources before sleeping; it does not
redownload every source every minute. To migrate already registered reviewed
sources to hourly refresh, make the change explicit and auditable:

```bash
job-intel reschedule-sources --all --interval 60
# Or limit the change to one exact imported company name:
job-intel reschedule-sources --company "Example Company" --interval 60
```

Rescheduled enabled sources become due immediately so an old six-hour
`next_sync_at` value cannot delay the first hourly run. Re-importing a reviewed
source registry also updates changed intervals and makes those changed sources due.

For hundreds or thousands of reviewed boards, use the bulk registry format in
`data/sample/source_registry.csv`:

```bash
job-intel import-sources path/to/reviewed_source_registry.csv
```

## Discover career sources without claiming false coverage

The SEC ticker file does not provide a reliable canonical website for every filer.
Generate a deterministic work batch before acquiring websites or running discovery:

```bash
job-intel discovery-priority --batch-size 100 --batch-number 1 \
  > discovery-batch-001.json
```

The report ranks exact, manually reviewed H-1B/SEC identities first, followed by
reviewed H-1B identities, SEC identities, and the remaining company universe. It
includes the source documents and checksums behind H-1B priority, the current
coverage state, and one explicit next action. Provisional name matches never earn
H-1B priority, and this command neither guesses URLs nor creates identity links.
Its overview counts make the acquisition backlog measurable across batches.

Import verified website seeds with provenance, then run bounded discovery:

```bash
job-intel import-websites path/to/reviewed_company_websites.csv
# Freeze only the reviewed CSV batch into an immutable discovery plan.
job-intel acquisition-plan-create --name reviewed-batch-001 --scope discovery \
  --actor discovery-operator@example.org \
  --companies-csv path/to/reviewed_company_websites.csv
export WIKIMEDIA_USER_AGENT='OpenRole-CIKBot/0.1 (https://your-site.example/contact)'
job-intel import-wikidata-websites --actor discovery-operator@example.org \
  --limit 100 --after-company-id 0 --dry-run
# After reviewing the batch, apply it and resume from the reported
# safe_resume_after_company_id. The cursor is exclusive and company-ID ordered.
job-intel import-wikidata-websites --actor discovery-operator@example.org \
  --limit 100 --after-company-id 0
export SEC_USER_AGENT='OpenRole-CIKBot/0.1 discovery-operator@example.org'
job-intel import-sec-websites --actor discovery-operator@example.org --dry-run
job-intel import-sec-websites --actor discovery-operator@example.org
job-intel import-sec-filing-websites --actor discovery-operator@example.org --dry-run \
  --limit 100
job-intel import-sec-filing-websites --actor discovery-operator@example.org \
  --limit 100 --concurrency 4 --discover-new --discovery-concurrency 4
job-intel discover-sources --all --limit 100 --concurrency 4 \
  --actor discovery-operator@example.org
```

When a reviewer has primary first-party evidence for an exact ATS handoff, import
it directly as a review-required candidate instead of depending on an HTML crawl:

```bash
job-intel import-source-candidates path/to/reviewed_ats_candidates.csv
job-intel approve-discovered-sources \
  --policy greenhouse=https://developers.greenhouse.io/job-board \
  --policy workday=https://www.workday.com/en-us/legal.html \
  --policy-approved-at 2026-08-11T17:00:00Z --actor reviewer@example.org \
  --candidate-id 123 --candidate-id 124
```

The candidate registry requires `company_name`, `candidate_url`, `source_url`,
`verified_at`, and `actor`; an optional `kind` must match the strict local URL
classifier. The full file is validated before any row is written. Repeated
`--candidate-id` options keep approval scoped to the exact reviewed batch while
preserving the normal complete-manifest activation gate.

Third-party career or ATS lists are discovery leads, not verified source evidence.
Import only a license-reviewed list with exact stored company identity and immutable
source provenance:

```bash
job-intel import-discovery-leads path/to/licensed_discovery_leads.csv
```

The CSV requires `company_id`, the exact stored `company_name`, `lead_url`, optional
`kind`, `source_dataset`, `source_record_id`, `source_url`, SHA-256 `source_checksum`,
`license_id`, `license_url`, `license_status=permitted`, timezone-aware
`license_reviewed_at` and `retrieved_at`, and `actor`. These rows are stored only as
passive fingerprints with `verification_status=unverified`; they do not change company
coverage, create candidates, probe URLs, or activate sources. An operator must later
verify the URL from the company's primary website and use `import-source-candidates`
before normal policy review and complete-manifest approval can occur.

The Jobseek board registry is another attributed third-party lead source. It
never imports Jobseek job postings, creates companies, creates source candidates,
or activates recurring sync. It accepts only exact existing-company name matches
and a small allow-list of canonical supported ATS URLs; unknown formats are
reported as unsupported. Record the immutable upstream revision, attribution,
license notice, and the authorization context for each import:

```bash
job-intel import-jobseek-board-registry \
  /path/to/jobseek/apps/crawler/data/boards.csv \
  /path/to/jobseek/apps/crawler/data/companies.csv \
  --source-revision 3d4ae9baff7ec615f19c898eb098079e56838fc6 \
  --retrieved-at 2026-08-16T17:00:00+00:00 \
  --actor operator@example.org \
  --permission-basis "Direct permission from the Jobseek owner, recorded 2026-08-16"
```

The resulting fingerprints remain `verification_status=unverified` and
`activation_allowed=false`. First-party company evidence, policy review, and a
complete-manifest probe remain mandatory before a source can be activated.

`--companies-csv` requires exact, unambiguous `company_name` values. It prevents
an urgent reviewed batch from being mixed with the wider acquisition backlog;
the resulting plan still freezes the same verified URL evidence and uses the
normal lease, retry, and audit machinery.

The Wikidata acquisition path never searches by company name. It joins the exact
10-digit SEC CIK to Wikidata P5531, prefers a single P10311 official jobs URL as
the career seed, and stores a single P856 official website separately. Requests
are sequential, batched, contact-identified, and retry 429/temporary failures.
No value is selected when a CIK maps to multiple Wikidata items, when a property
has multiple URLs, or when a discovered URL conflicts with an existing value.
Every accepted value produces an audit event identifying the Wikidata item and
P5531→P10311/P856 path. The `--dry-run` output measures exact-match coverage
without writing, and live import output reports all ambiguity/conflict classes.

The SEC acquisition path is an independent authoritative fallback for companies
still missing a website. It requests only
`data.sec.gov/submissions/CIK##########.json`, verifies that the returned CIK is
the exact requested CIK, and accepts only an absolute public top-level `website`
value. If that field is empty, an explicit top-level `investorWebsite` may be
stored as a clearly labeled discovery fallback. It never searches by name or
constructs a domain. Requests are contact-identified, globally capped at ten per
second across at most eight workers (five per second and one worker by default),
retry temporary failures, and refuse redirects.
Each accepted URL records the exact SEC JSON endpoint and field in the company
audit history. SEC submissions commonly leave both URL fields empty; those
companies remain in the measured acquisition backlog instead of receiving a
guessed URL.

For exact-CIK companies still missing a website, the filing importer inspects
recent company-filed SEC documents and accepts a URL only when the filing text
explicitly identifies it as the company website. It records the accession number,
filing form/date, primary-document URL, and bounded evidence text behind every
accepted value. A declared deep page is reduced to its verified HTTPS origin;
query/fragment URLs and investor-, storage-, CDN-, ATS-, social-, or SEC-hosted
URLs are rejected. It does not derive a domain from a company name, ticker,
email, or filing link, and it does not replace an existing canonical website.
Ambiguous or conflicting evidence remains unresolved.

Use `--after-cik CIK` with a bounded `--limit` to resume large operator batches in
deterministic CIK order. `--discover-new` is an explicit convenience step: after
the filing import commits, it sends only website seeds changed by that invocation
through the existing bounded ATS discovery. This creates review-required source
candidates only. It never approves a candidate, enables a source, or starts job
ingestion; reviewed vendor policy plus a successful complete-manifest probe are
still separate requirements. `--discover-new` is intentionally incompatible with
`--dry-run`.

Filing fetch concurrency defaults to one and is bounded at eight. All workers
share the configured `--rate-per-second` ceiling, which cannot exceed the SEC's
ten-request-per-second fair-access limit. `--discovery-concurrency` controls the
later corporate-site crawl independently.

Use the returned `safe_resume_after_cik`, not merely the last attempted CIK, as
the next cursor. Only exhausted 429/5xx/network failures hold that safe cursor;
permanent 4xx archive gaps are reported but are not retried indefinitely.

Wikidata is open, community-maintained data, not an authoritative company
registry, and P10311/P5531 coverage is incomplete. A P10311 URL is a high-quality
discovery seed, not proof that its ATS manifest is supported or collection is
permitted. Source discovery and reviewed connector approval remain mandatory.

Discovery checks robots rules, blocks private/reserved network targets and unsafe
redirects, reads at most four bounded HTML pages per company, and recognizes URL
variants for Greenhouse, Lever, Ashby, SmartRecruiters, Workday, and Oracle
Recruiting, plus exact ADP Workforce Now, authorization-gated iCIMS, and UKG
Recruiting public-board URLs. A verified seed
stored as HTTP is attempted only as HTTPS on the exact
same hostname and path; discovery never fetches the HTTP URL or invents an alternate
domain. A result is stored only as a candidate. It does not become a scheduled
source until an operator reviews the terms and a deterministic connector probe returns
a complete manifest. A zero-opening board requires two consecutive complete probes:

Workday discovery supports both public host families without translating or guessing
tenants: `https://company.wd5.myworkdayjobs.com/Site` and
`https://wd5.myworkdaysite.com/recruiting/company/Site`. The reviewed source key always
stores the exact host, tenant, and site (`host|tenant|site`), and the connector constructs
the CXS endpoint on that same host. Recruiting-path URLs are accepted only with an exact
HTTPS host and path; credentials, explicit ports, query strings, fragments, traversal,
and host/tenant/site mismatches are rejected.

ADP Workforce Now discovery accepts only the observed public career-center shape
with an exact `cid`, `ccId`, and `lang`/`locale`; legacy `client=` pages, missing
identifiers, malformed locales, credentials, ports, and fragments remain passive
evidence. The connector closes pagination against ADP's stable total and fetches
every public requisition detail, but discovery does not activate it: a reviewer must
still supply an explicit terms decision through the normal approval gate.

UKG Recruiting discovery accepts only an observed `recruiting.ultipro.com`,
`recruiting2.ultipro.com`, or `recruiting.ultipro.ca` URL containing both the exact
tenant and job-board UUID. It never guesses tenant roots or board identifiers. The
connector closes pagination against the board's stable total, loads each public
opportunity detail, and requires the board-specific native posting date. UKG's
published terms prohibit unauthorized automation, so an exact board remains dormant
unless it has primary-company provenance and an operator records the applicable
written authorization/terms decision. See `docs/UKG_RECRUITING_PUBLIC.md`.

iCIMS public discovery accepts only an exact unfiltered HTTPS `/jobs/search` portal.
The connector reconciles the robots-declared same-host sitemap against native search
pagination before a manifest can be complete. Activation also requires an explicit
allowed robots review imported with the primary-source candidate, plus the normal
official-provenance and reviewed terms/written-authorization gates. Generic iCIMS and
its credentialed API remain policy-held. See `docs/ICIMS_PUBLIC.md`.

```bash
job-intel approve-source-candidate 123 \
  --terms-url https://example.org/terms \
  --policy-approved-at 2026-08-06T12:00:00Z \
  --actor reviewer@example.org
```

The successful approval probe is the source's initial ingestion: jobs, ATS opening
dates, U.S. geography evidence, policy review, source health, and coverage state are
committed together. If persistence fails, none of those activation records remain.
The scheduler waits for the configured interval before fetching that source again.

After reviewing vendor policies, operators can probe a bounded batch. Complete
non-empty manifests activate immediately; a complete-empty manifest remains pending
until a second independent observation, and probe failures remain candidates:

```bash
job-intel approve-discovered-sources \
  --policy greenhouse=https://developers.greenhouse.io/job-board \
  --policy workday=https://www.workday.com/en-us/legal.html \
  --policy-approved-at 2026-08-07T00:00:00Z \
  --actor reviewer@example.org --interval 60
```

Oracle Recruiting candidates require a separate policy decision. Oracle's public
reference currently labels the Candidate Experience resources used by public career
sites as internal-use endpoints, so this repository discovers and can probe those
candidates but does not treat the Oracle website terms page as collection permission
or approve them by default.

The public `/api/coverage` denominator distinguishes unreviewed, candidate,
approved, supported, unsupported, blocked, no-source, and stale companies. Being
listed in `/companies` never means that the company has been checked.

The implemented connector types are Greenhouse, Lever, Ashby, SmartRecruiters,
Workday, Oracle Recruiting, ADP Workforce Now, and authorization-gated iCIMS and UKG
Recruiting; Oracle and unreviewed ADP/iCIMS/UKG sources remain policy-held as described
above.
Complete manifests alone can
advance the two-pass
closure rule.
If an ATS publishes an opening date, jobs are ordered and labeled by that value.
Otherwise the API and UI explicitly use `first_seen_at`/“First observed”; an ATS
update timestamp is never misrepresented as the application opening date.

Collectors retain complete global ATS manifests internally so lifecycle reconciliation
remains accurate. Public reads include only `us_eligibility=eligible`; ambiguous remote,
worldwide, conflicting, non-U.S., and U.S.-territory-only locations are not published.

## Legacy collector bridge

The older broad collectors are an optional, local compatibility tool. They are
not installed in the public image and never run on a production schedule.

```bash
python -m pip install -e '.[legacy]'
playwright install chromium
job-intel scrape path/to/companies.csv --limit 3 --concurrency 2
```

This bridge makes the old scraper data useful immediately, but marks every legacy
result partial: those collectors swallow some pagination failures and cannot prove
manifest completeness. They also do not capture descriptions, so current-posting
language remains unevaluated. The next milestone is typed collector results,
first-party ATS APIs, full-role ingestion, and description capture.

## Evidence tiers

| Tier | Meaning |
|---|---|
| A | Explicit, job-specific immigration sponsorship offer in the current posting |
| B | Strong, recent official employer history |
| C | Some high-confidence official employer history |
| D | Insufficient, conditional, ambiguous, or unresolved evidence |
| E | Explicit current-posting sponsorship denial |

The numeric `evidence_score` is an ordering index, not a probability. Current
explicit negative language overrides all historical positives. Screening questions,
generic sponsorship statements, and non-immigration sponsorship (for example,
security-clearance sponsorship) are not treated as a job policy decision.

## Project map

```text
src/fortune_intel/       New domain, storage, import, API, and dashboard
src/scraper/             Original ATS-specific collectors
src/sheets_client.py     Legacy Google Sheets output (kept during migration)
tests/                   Unit, lifecycle, scoring, and API tests
docs/                    Architecture and product methodology
```

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the migration design and
[docs/METHODOLOGY.md](docs/METHODOLOGY.md) for evidence semantics and limitations.
Public maintainers must also follow [docs/PUBLIC_RELEASE.md](docs/PUBLIC_RELEASE.md)
because the private development repository's old Git history is not publishable.
The latest local acceptance evidence and remaining external blockers are recorded
in [docs/VERIFICATION.md](docs/VERIFICATION.md). The migration required before a
multi-thousand-source coverage claim is documented in
[docs/SCALE_PLAN.md](docs/SCALE_PLAN.md).

## Data and compliance principles

- Prefer canonical employer feeds and documented public ATS APIs.
- Require source terms/robots/rate-limit review, opt-outs, and kill switches before
  production operation; the legacy bridge does not centrally enforce this policy.
- Never bypass authentication, CAPTCHAs, or technical controls.
- Publish factual fields, short evidence excerpts, provenance, and canonical links
  instead of mirroring full copyrighted postings.
- Keep licensed company rankings, credentials, `.env` files, raw disclosures, and
  local databases out of Git.

## Status

This branch now supports a large open company directory and real official H-1B
employer history, while job coverage remains explicitly tied to approved career
sources. It does not claim complete Fortune/Inc. coverage, current USCIS petition
data, or guaranteed sponsorship. Public release requires the launch checklist,
TLS ingress, backup restore drill, and a clean-history source repository; see
`docs/LAUNCH_CHECKLIST.md`.

## License

MIT applies to code and the synthetic fixture only; imported data retains its own
terms. See [data/PROVENANCE.md](data/PROVENANCE.md). “Fortune” is not affiliated
with or endorsing this project.
