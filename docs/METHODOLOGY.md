# Sponsorship evidence methodology

## What the label means

The product answers: “What verifiable evidence is available for this role and
employer?” It does **not** answer: “Will this applicant receive sponsorship?”

Three kinds of evidence stay separate:

1. Current posting policy: explicit positive, explicit negative, or unknown.
2. Employer history: DOL labor-condition records now; USCIS petition import is planned.
3. Match quality: confidence that a job's organization is the same legal employer
   represented in an official dataset.

## Decision order

1. A rule-detected negative phrase in the current posting assigns Tier E and
   overrides historical evidence, while preserving an excerpt for review.
2. A rule-detected positive phrase assigns Tier A and preserves a short excerpt.
3. With no current policy, high-confidence recent employer history can assign B/C.
4. Weak legal-entity matches and missing evidence assign D.

Application screening questions are not policy. “Will you now or in the future
require sponsorship?” alone does not mean the company refuses sponsorship.
Tier A/E are automated language signals, not manually verified legal-policy labels;
ambiguous or conflicting language is sent to Tier D for review.

The evidence score uses logarithmic historical volume and freshness decay so very
large employers do not dominate solely by size. It is capped below the explicit
positive tier. It is an index for ordering evidence, not a calibrated probability.

## Source interpretation

- A certified DOL LCA is an employer attestation/prerequisite. It does not prove a
  petition was filed, approved, or tied to a current vacancy.
- USCIS employer history is stronger petition evidence but remains historical and
  employer-level.
- Employer-submitted records can contain errors, aliases, blanks, and later changes.
- A DOL `TOTAL_WORKER_POSITIONS` value means positions requested on an employer
  filing—not unique people, hires, petition approvals, or current openings.
- Unique normalized-name candidates are provisional and remain below the scoring
  threshold until reviewed against aliases/address/domain evidence.
- The public H-1B employer directory lists historical legal-employer filings even
  when no career site is mapped. It does not convert that history into a current
  sponsorship claim.

## Opening dates

`source_opened_at` is populated only when the ATS publishes a creation, release, or
publication timestamp for the role. `first_seen_at` is the crawler observation time.
The UI labels these “Opened” and “First observed” respectively. Greenhouse's public
board feed publishes an update timestamp but no opening timestamp, so the platform
does not relabel `updated_at` as an application date.
Oracle Recruiting's anonymous Candidate Experience detail response publishes
`ExternalPostedStartDate`. The connector stores that exact value and records the
field name in metadata; it uses the listing's `PostedDate` only when the detail
omits the higher-resolution timestamp.

Official starting points:

- [DOL OFLC performance data](https://www.dol.gov/agencies/eta/foreign-labor/performance)
- [DOL H-1B program overview](https://www.dol.gov/agencies/eta/foreign-labor/programs/h-1b)
- [USCIS H-1B Employer Data Hub files](https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub/h-1b-employer-data-hub-files)

## Accuracy plan

Before making predictive claims, create a reviewed gold set covering explicit yes,
explicit no, ambiguous screening questions, and employer aliases. Measure precision
and recall separately for policy extraction and company resolution. Version every
rule/model and retain the exact evidence excerpt so corrections are auditable.
