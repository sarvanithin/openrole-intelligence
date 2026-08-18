# UKG Recruiting public-board connector

## Scope and activation gate

`ukg_recruiting_public` is a dormant, authorization-gated connector for exact UKG/
UltiPro Recruiting boards already observed through a company's verified website or
an explicit primary-source review. Discovery does not activate collection.

Activation requires both:

1. primary-company provenance for the exact board; and
2. an operator-recorded applicable terms decision or written authorization, including
   reviewer identity and review timestamp.

Generic tenant roots, guessed board UUIDs, third-party discovery leads, and generic
`ukg`/`ultipro` connector names remain policy-held. This repository does not ship any
enabled UKG source.

## Exact source identity

The source key is `host|tenant|board_uuid`. The allowlist contains only:

- `recruiting.ultipro.com`
- `recruiting2.ultipro.com`
- `recruiting.ultipro.ca`

URLs must be HTTPS and contain the exact tenant plus `JobBoard/{uuid}`. Credentials,
explicit ports, fragments, unsafe or encoded paths, unknown query parameters, malformed
UUIDs, lookalike hosts, and tenant-only URLs fail closed. Canonicalization removes only
the recognized UI search state; it never changes the host, tenant, or board UUID.

## Complete-manifest contract

After authorization, the connector uses the same anonymous, first-party transport as
the public board UI:

- `POST .../JobBoardView/LoadSearchResults` with an empty query, deterministic native
  `PostedDate` descending order, and explicit `Top`/`Skip` pagination;
- `GET .../OpportunityDetail?opportunityId={native_uuid}` for every returned summary.

This is an observed public web transport, not an official anonymous UKG API contract.
The policy gate therefore remains essential.

A run is complete only when all of these checks pass:

- the total count stays unchanged for every page;
- offsets close exactly against that total;
- no page ends early and the configured maximum page count is not exceeded;
- every summary has one valid native UUID and a corresponding bounded detail response;
- detail UUID and title match the summary;
- exactly one published membership matches the configured board UUID;
- the membership's native `ExternalPostedDate` is valid and matches the listing date;
- no native job UUID is duplicated.

Any listing, pagination, detail, parsing, identity, or date failure marks the entire
manifest incomplete, which prevents job closure and source activation.

## Fields and U.S. publication

The connector preserves the native opening date separately from `UpdatedDate`, extracts
the full description, requisition number, job category, employment/location type, pay
range fields, and all structured locations/country codes. Complete global manifests are
kept internally for correct lifecycle reconciliation. The shared fail-closed geography
classifier publishes only definite roles in the 50 states or Washington, DC; non-U.S.,
territory-only, conflicting, and ambiguous locations remain hidden from public reads.

## Inventory impact

The implementation-start snapshot contained 46 missing companies with UKG-family
fingerprints; strict parsing accepted 53 exact boards for 42 of them. During the same
official-domain acquisition wave, two further companies gained exact boards. The
current frozen audit therefore contains 48 missing UKG-family companies and accepts
55 exact boards belonging to **44 distinct missing companies**. Four companies have
only tenant-root or malformed observations and remain unaddressable without a new exact
primary-source board URL. These figures describe technical addressability, not activated
coverage or successful live ingestion.
