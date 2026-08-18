# Rippling ATS connector policy hold

Status: **exact candidate inventory only; recurring ingestion is disabled**.

The platform recognizes only exact observed URLs on `ats.rippling.com`. It
distinguishes company board roots, localized individual job observations, and
application URLs. It preserves the exact company and job identifiers but does
not derive a board from a job link, guess locale routes, query undocumented
browser endpoints, automate a browser, or register Rippling with the scheduler.

## Official evidence

- Rippling's official [API reference](https://developer.rippling.com/documentation/developer-portal/reference/api-reference)
  requires either an API key or OAuth access token, each tied to one Rippling
  company, for API access.
- The official [developer portal overview](https://developer.rippling.com/documentation/developer-portal)
  requires partners to use OAuth and states that using an API key on behalf of
  another organization violates Rippling's terms.
- Rippling's [OAuth installation guide](https://developer.rippling.com/documentation/developer-portal/v1-guides/installation)
  requires an installed application, registered client credentials, and one
  access token for each authorizing Rippling company. The installing company
  administrator authorizes the configured scopes.
- The official [API usage and rate-limit guide](https://developer.rippling.com/documentation/rest-api/essentials/api-limits)
  documents cursor-based collections, a default page size of 50, maximum page
  size of 100, and a global burst threshold. These guarantees apply to supported
  authenticated APIs, not undocumented ATS browser routes.

The public API catalog does not document a Recruiting job-posting collection
mapped to the observed `ats.rippling.com` URLs. Consequently, the platform cannot
validate a complete job manifest, posting dates, job locations, US-only
compatibility, stable posting identity, bounded request behavior, or closure
reconciliation. A locale in a visible URL is not treated as job-location
evidence. Factory construction therefore fails closed.

Activation requires employer-authorized Rippling API access and a documented ATS
job-posting endpoint or employer-provided feed. A future connector must then pass
complete pagination, record and posting-date validation, US-geography
compatibility, bounded HTTP, empty-manifest safety, and two-run closure tests
before scheduling.
