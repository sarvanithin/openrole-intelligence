# Jobvite recruiting integration policy

Decision date: 2026-08-10

## Status

Jobvite is **inventory-only and policy-held**. The deterministic platform can
classify exact public company-board and job-detail URLs for audit, but it must
not activate ingestion, derive private routes, or send probe requests.

The current immutable, read-only inventory snapshot contains eight distinct
companies and eight Jobvite URL observations, all on `jobs.jobvite.com`. Six are
company board roots. The remaining `/apply` and `/jobAlerts` observations are
action pages rather than complete job manifests and are rejected by the strict
board classifier. This measurement is not a claim of complete company or job
coverage.

## Primary-source findings

- Jobvite's [Terms of Use](https://www.jobvite.com/terms-of-use/) state that
  Jobvite materials may not be scraped and that Job Postings may not be
  provided to third parties except as those terms permit.
- Jobvite's official [Integrations & API](https://help.jobvite.com/hc/en-us/sections/24681239197981-Integrations-API)
  documentation treats API access and career-site integration as customer
  product capabilities; it does not document an anonymous complete jobs feed.
- An official [Jobvite integration FAQ](https://help.jobvite.com/hc/en-us/articles/14026618210333)
  shows that API-based integrations require Jobvite-enabled keys and secrets.
- Jobvite's [standard roles and privileges](https://help.jobvite.com/hc/en-us/articles/7884452851229-Standard-User-Roles-and-Privileges)
  describe customer privileges for publishing requisitions and creating job
  board links, rather than anonymous API authorization.

No first-party anonymous contract was verified for complete enumeration,
pagination, posting dates, locations, closures, or USA-only filtering. A public
career page is not authorization to scrape or reverse-engineer its backing
requests.

## Fail-closed behavior

The local classifier recognizes only:

- `https://jobs.jobvite.com/<company>` board roots; and
- first-party-evidenced `https://jobs.jobvite.com/<company>/job/<job-id>` job
  details, with only the observed `fr` and `nl` query markers.

It rejects `/apply`, `/jobAlerts`, missing identifiers, unsafe or encoded paths,
unknown or duplicate query keys, non-HTTPS URLs, credentials, nonstandard ports,
fragments, subdomains, alternate hosts, and lookalike domains. It preserves the
exact observed URL and performs zero network requests.

Endpoint, complete-manifest, pagination, posting-date, location, USA-only
filtering, and bounded-HTTP validations remain false. The connector factory
refuses Jobvite activation.

## Activation requirements

Activation requires all of the following:

1. Written Jobvite authorization and authorization from each applicable
   subscriber permitting this use and redistribution.
2. Customer-enabled API credentials stored in the production secret manager.
3. An official jobs API contract proving complete external requisition
   enumeration, pagination, posting dates, locations, and closure semantics.
4. Proven USA-only filtering and bounded HTTP behavior before scheduler
   registration.
