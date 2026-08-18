# UKG/UltiPro recruiting integration policy

Decision date: 2026-08-12

## Status

Generic UKG/UltiPro sources remain **inventory-only and policy-held**. An explicit
`ukg_recruiting_public` connector is now implemented for exact observed boards, but it
is dormant by default and may run only after primary-company provenance and an
operator-recorded applicable terms decision or written authorization. No UKG source
is enabled by this repository.

A read-only inventory snapshot contained 37 distinct companies and 60 UKG URL
observations. This is an inventory measurement, not a claim of job or company
coverage. The measured hosts were `recruiting.ultipro.com`,
`recruiting2.ultipro.com`, and `recruiting.ultipro.ca`.

## Why ingestion is disabled

- [UKG Terms of Use](https://www.ukg.com/terms-of-use) prohibit robots, spiders,
  scrapers, and other automated access to UKG sites, accounts, systems, or
  networks, and prohibit probing or obtaining information that was not made
  intentionally available.
- [UKG Developer Console Quick Start](https://developer.ukg.com/proplatform/docs/developer-console-quick-start)
  documents role-controlled access and administrator-created machine-to-machine
  credentials (client ID and client secret).
- [UKG Acceptable Use Policy](https://www.ukg.com/acceptable-use-policy) prohibits
  excessive querying, scraping, synchronizing, or extracting beyond ordinary or
  intended use.

No official anonymous, complete recruiting-manifest API was verified. The first-party
anonymous requests made by the public board UI are technically sufficient for a strict
connector, but a public UI URL is not treated as permission to automate it.

## Fail-closed behavior

The local classifier accepts only the exact measured URL families:

- a tenant root;
- a `JobBoard` path with a UUID board identifier; or
- an `OpportunityDetail` path with a UUID `opportunityId`.

It uses an exact host allowlist and rejects credentials, non-HTTPS URLs,
nonstandard ports, fragments, encoded or unsafe paths, duplicate query keys,
unknown query keys, malformed identifiers, and lookalike domains. It preserves
the observed URL for audit and performs zero network requests.

The generic connector factory names (`ukg`, `ultipro`, and variants) still refuse
activation. Exact board URLs can classify as `ukg_recruiting_public`; that connector
implements stable-total pagination, bounded details, board-specific native posting
dates, structured locations, complete-manifest failure semantics, and shared U.S.-only
publication filtering. Approval fails before any network probe when the candidate lacks
primary-company provenance, and normal approval still requires a reviewed terms URL,
timestamp, and actor. See `UKG_RECRUITING_PUBLIC.md`.

## Activation requirements

Activation requires all of the following:

1. Written authorization from UKG and each applicable subscriber/tenant.
2. The exact tenant and board UUID observed from a verified primary-company source.
3. If an official credentialed API is used instead, administrator-issued credentials
   stored in the production secret manager, never source control.
4. A successful complete-manifest probe through the normal approval workflow. A
   complete zero-job board requires a second independent successful observation.
