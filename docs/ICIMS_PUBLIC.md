# Authorization-gated iCIMS public portal connector

`icims_public` supports only exact, unfiltered HTTPS `/jobs/search` URLs on an observed
iCIMS customer portal. It does not derive a tenant from a company name, promote a job
detail into a search source, or accept search filters, ports, credentials, fragments,
encoded paths, or lookalike hosts.

The connector first follows the exact same-host sitemap declared by `robots.txt`, then
follows native zero-based `pr` pagination and requires the iCIMS listings
wrapper, one `Search Results Page X of Y` heading, and strict job cards. It fetches every
card's exact same-host detail page. An iCIMS-bound JobPosting object is accepted only
when the page has the iCIMS job wrapper and its numeric ID, title, and canonical URL
match the card. Native `datePosted`, description, category, employment type, and every
structured location and sitemap `lastmod` update timestamp are retained. The paginated
native-ID/URL set must exactly equal the sitemap manifest. Unexpected pages, changed totals, empty pages,
duplicates, detail failures, identity mismatches, or invalid dates make the manifest
incomplete.

This public web transport is not treated as collection permission. Activation requires
official-company provenance, an explicit `robots_status=allowed` review, an applicable
terms decision or written authorization, and a successful complete-manifest probe. No
iCIMS source is enabled by this implementation.

The 2026-08-12 inventory contains 50 missing companies with 221 iCIMS fingerprints.
Only 10 companies have an exact unfiltered search observation. Live contract validation
found at least one strict, sitemap-closed manifest for **8 companies** across 11 portal
hosts (including legitimate zero-job manifests). These figures mean technical
addressability, not activated coverage.
