# Paylocity Recruiting connector policy hold

Status: **exact candidate inventory only; recurring ingestion is disabled**.

The platform recognizes only exact observed URLs on
`recruiting.paylocity.com`. It distinguishes UUID-based company boards, legacy
list boards, job-detail URLs, and application URLs. It preserves each exact URL
and identifier but does not derive a company board from a job, invent a browser
route, query undocumented endpoints, automate a browser, or register Paylocity
with the scheduler.

## Official evidence

- Paylocity's official [authentication documentation](https://developer.paylocity.com/integrations/v2025-08-12/reference/authentication)
  requires every API call to carry a short-lived bearer token obtained from the
  Paylocity identity provider.
- The official [Paylocity API End User License Agreement and Developer Tools Terms](https://www.paylocity.com/terms-and-conditions/)
  limit API access to registered clients or partners, require registration/API
  keys where applicable, and require client-specific authorization and an
  integration agreement before production endpoints are provisioned. Production
  use may also require Paylocity certification.
- Paylocity's [integration requirements](https://developer.paylocity.com/integrations/docs/integration-requirements)
  classify integrations as customer-specific unless the owner has completed
  Paylocity's review and technology-partner approval.
- The official [Work Locations endpoint](https://developer.paylocity.com/integrations/reference/get_apihub-corehr-v1-companies-companyid-worklocations)
  requires bearer authorization and a Paylocity company identifier. Its
  documentation says configured work locations feed Recruiting job postings,
  but it is not a job-posting collection API.

The official public API catalog does not document an anonymous Recruiting job
manifest mapped to the observed career URLs. Consequently, the platform cannot
validate complete pagination, posting start dates, structured job locations,
US-only compatibility, stable posting identity, bounded request behavior, or
closure reconciliation. Visible browser routes are not treated as an API
contract. Factory construction therefore fails closed.

Activation requires Paylocity registration, the applicable client authorization
and production agreement, plus a documented Recruiting job-posting API or
employer-provided feed. A future connector must then pass complete pagination,
record and posting-date validation, US-geography compatibility, bounded HTTP,
empty-manifest safety, and two-run closure tests before scheduling.
