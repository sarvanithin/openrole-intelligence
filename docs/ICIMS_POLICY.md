# iCIMS connector policy decision

Decision date: 2026-08-10

Status: **generic/API integration policy-held; public portal authorization-gated**

## Verified official contract

The official iCIMS Job Portal API documents the complete portal search endpoint
as `https://api.icims.com/customers/{customerId}/search/portals/{portalIdOrName}`.
Every official request example includes HTTP Basic authorization. The associated
Search API states that the caller must belong to the Integration User group and
that the API is designed for background synchronization rather than real-time
search.

The current iCIMS Developer Terms prohibit scraping, crawling, harvesting, or
other automated extraction except through documented and authorized API use.
They also prohibit probing iCIMS endpoints without express authorization.

Primary sources:

- https://developer-community.icims.com/applications/applicant-tracking/job-portal
- https://developer-community.icims.com/applications/applicant-tracking/search-api
- https://developer-community.icims.com/terms-use
- https://www.icims.com/legal/terms-of-use/

## Fail-closed implementation

The platform may inventory an exact HTTPS customer-portal URL whose path is
`/jobs/search`. It does not derive that path from a job-detail URL and does not
invent a customer ID, portal ID, API URL, or credential.

The iCIMS factory returns a policy-held connector whose probe performs no HTTP,
returns no jobs, marks the manifest incomplete, and emits the non-retryable error
`policy_review_required`. Consequently normal source approval cannot activate it.

A separate `icims_public` connector implements the strictly observed public portal
contract without changing this decision for generic iCIMS or the authenticated API.
It remains dormant unless official-company provenance, an explicit allowed robots
review, and applicable written authorization/terms review are recorded. See
`ICIMS_PUBLIC.md`. No iCIMS source is enabled by the repository.

Recurring ingestion may be implemented only after the operator has both:

1. written authorization from iCIMS and the applicable subscriber for this use;
2. Integration User credentials and exact customer/portal identifiers, stored in
   a production secret manager rather than source keys, CSV files, or SQLite.

At that point the authenticated connector must implement the official ID-based
pagination contract, retrieve complete job profiles and portal-post start dates,
and retain the present fail-closed manifest rules.
