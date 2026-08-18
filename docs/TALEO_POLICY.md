# Oracle Taleo recruiting integration policy

Decision date: 2026-08-10

## Status

Oracle Taleo Enterprise and Taleo Business Edition are **inventory-only and
policy-held**. The deterministic platform may classify and retain exact public
career URLs already observed in the source inventory, but it must not activate
job ingestion, infer service endpoints, or send probe requests.

The current read-only inventory snapshot contains 16 distinct companies and 36
Taleo URL observations. The earlier measured count was 15 companies; this
number increased as the shared inventory changed. Neither number is a claim of
complete job or company coverage.

Eight Enterprise zone hosts and four Business Edition zone hosts are currently
observed. The inventory includes candidate-facing job search and detail URLs as
well as non-job login and submission URLs; the strict classifier accepts only
the job-board shapes.

## Primary-source findings

- Oracle's [Supported Product URLs](https://docs.oracle.com/en/cloud/saas/taleo-enterprise/24d/otrdc/c-taleo10supportedurls.html)
  document the Enterprise `careersection/<code>/jobsearch.ftl` and
  `jobdetail.ftl` candidate-facing URL shapes.
- Oracle's [Taleo Web Services API](https://docs.oracle.com/en/cloud/saas/taleo-enterprise/otwsu/c-taleoapi.html)
  describes a secure API for an organization's information, not an anonymous
  public jobs feed.
- Oracle's [Taleo API quick start](https://docs.oracle.com/en/cloud/saas/taleo-enterprise/24c/otwsu/c-quickstart.html)
  requires tenant-generated WSDL definitions, account access, privileges, or
  WSDL files obtained from a customer representative.
- Oracle's [Taleo Business Edition adapter connection guide](https://docs.oracle.com/en/cloud/paas/integration-cloud/talent-cloud-midsize-adapter-user/create-connection.html)
  requires company code, username, password, and an Administrator-role user.
- Oracle's [Web Sites Terms of Use](https://www.oracle.com/legal/terms/)
  prohibit robots, spiders, scrapers, and other automated access without
  Oracle's express written permission.

No official anonymous complete recruiting-manifest contract was verified for
either Taleo edition. Candidate-facing HTML routes do not establish permission
to reverse-engineer private requests or service endpoints.

## Fail-closed behavior

The local classifier accepts only these measured families:

- Enterprise `careersection/<section>/jobsearch.ftl` URLs with measured query
  fields;
- Enterprise `careersection/<section>/jobdetail.ftl` URLs with a validated job
  identifier; and
- Business Edition `jobSearch`, `searchResults`, and documented redirect-style
  observations containing exact organization and career-site identifiers.

It rejects login and submission pages, unsafe or encoded paths, unrecognized
query keys and duplicate identifiers, malformed values, non-HTTPS URLs,
credentials, nonstandard ports, fragments, and lookalike domains. It preserves
the observed URL for audit and performs zero network requests.

Endpoint, complete-manifest, pagination, posting-date, location, USA-only
filtering, and bounded-HTTP validations remain false. The connector factory
refuses Taleo activation. The older Playwright-based scraper is not used by the
deterministic connector pipeline and is not an acceptable production fallback.

## Activation requirements

Activation requires all of the following:

1. Express written Oracle authorization and authorization from each applicable
   Taleo subscriber.
2. Tenant-issued service credentials stored in the production secret manager.
3. The exact tenant WSDL or official API contract covering complete external
   requisition enumeration, pagination, posting dates, locations, and closure
   semantics.
4. Proven USA-only filtering and bounded HTTP behavior before scheduler
   registration.
