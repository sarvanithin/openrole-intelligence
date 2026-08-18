# Avature connector policy

## Status

Avature ingestion is **policy-held** and is not available to the scheduler. The
classifier records only exact measured career-board/search observations and the
probe performs zero network requests.

The measured source inventory reviewed for this implementation contained 15
`avature.net` URLs across four companies and four exact hosts. Only Ally had an
observed career-board root and search routes. The Jack Henry, Ross, and Synopsys
observations were login, internship landing, or talent-community pages; those are
not evidence of a complete public job manifest and are rejected.

## Official evidence

- [Avature Applicant Tracking System](https://www.avature.net/applicant-tracking-system/)
  describes an open integration framework and configurable career sites, but does
  not document a uniform anonymous job-listing API.
- [Avature Technical Services](https://www.avature.net/avature-technical-services/)
  says DIY integrations use Custom Endpoints REST APIs and that Avature's
  integration team scopes, develops, and supports integrations.
- [Avature Integration Framework](https://www.avature.net/fr/le-framework-dintegration-avature/)
  says administrators create custom API endpoints, control their activation, and
  generate vendor credentials and API keys. This is customer-specific authorized
  access, not an anonymous contract inferred from a career-site URL.
- [Avature Terms of Use](https://www.avature.net/terms-of-use/) grants limited
  personal, non-commercial display access and restricts copying, transmitting,
  distributing, and mirroring site content without prior written consent. Customer
  career portals may also impose their own terms.
- [Avature Career Sites](https://www.avature.net/career-sites/) shows that the
  visible site experience is configurable. A visible route is therefore not proof
  of a stable or complete machine-readable feed.

## Why activation is blocked

No official public contract was found that establishes all of the following for
the measured URLs:

1. authorized anonymous collection and public redistribution;
2. a complete job manifest and deterministic pagination;
3. authoritative original posting dates and stable job identifiers;
4. structured job locations suitable for strict United States-only filtering;
5. bounded request behavior and reliable closure detection.

Filtered search URLs are retained only as observations. They are never treated as
complete boards, and their filter field IDs are not guessed or generalized to
other tenants.

## Activation requirements

Activation requires written authorization from the employer/Avature and a
customer-configured endpoint or feed with documented credentials, pagination,
dates, locations, rate limits, and redistribution rights. A future connector must
then pass fixture-based completeness, US-location, retry/bounds, idempotency, and
job-closure tests before it can be added to the scheduler.
