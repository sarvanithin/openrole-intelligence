# Official structured-jobs fallback

`official_structured` is a policy-gated fallback for an exact sitemap or
syndication URL observed on a company-controlled page. It does not guess common
sitemap paths and does not turn passive `unknown_external` fingerprints into
sources.

## Safety and completeness contract

- The source key and base URL are the exact observed HTTPS manifest URL.
- Discovery accepts only an explicit same-company HTML sitemap/feed link.
- Approval always requires primary-company provenance, an explicit allowed
  robots review, and an operator-reviewed policy URL.
- Network traversal is pinned to the manifest's exact public host, rejects
  credentials, non-default ports, IP literals, redirects, private DNS results,
  path traversal, oversized responses, invalid UTF-8, and non-XML manifests.
- XML DTDs and entity declarations are rejected.
- Sitemap indexes and URL sets are traversed exhaustively within fixed manifest
  and job ceilings. Cycles, duplicates, off-host URLs, ceiling hits, malformed
  XML, or any failed child/detail request make the manifest incomplete.
- Every enumerated detail URL must contain exactly one valid Schema.org
  `JobPosting` JSON-LD object. Duplicate native identifiers fail closed.
- RSS and Atom entries can be parsed, but their result is always incomplete
  because a recent-items feed does not prove a complete active-job inventory.
- A complete empty sitemap still needs the platform's existing second
  independent complete-empty confirmation.

The connector preserves native `datePosted`, `dateModified`, `validThrough`,
locations, description, canonical URL, employment type, remote status, hiring
organization, and all parsed locations. U.S. eligibility remains a downstream
classification; the connector does not discard non-U.S. rows from a complete
source manifest.

## Bounded addressability audit

At implementation time, the live queue contained 1,773 `unknown_external`
observations across 888 companies. Of those companies, 815 had a stored official
website or careers seed.

A deterministic evenly spaced 100-company read-only sample checked 304 official
pages. Four companies exposed explicit sitemap/feed links, but all five observed
manifests failed the production completeness probe:

- Corteva: generic page sitemap; enumerated pages lacked JobPosting JSON-LD.
- EverQuote: RSS was non-complete and its general sitemap exceeded the bounded
  job ceiling.
- Karyopharm: generic sitemap; enumerated pages lacked JobPosting JSON-LD.
- LivePerson: general sitemap exceeded the bounded job ceiling.

Therefore the proven addressable count in the audited sample is **0 of 100**;
four companies are manifest-exposed but not safely activatable. The current
1,773 stored observations themselves contain zero explicit `.xml`, `.rss`, or
`.atom` URLs. This is a measured lower bound, not a claim that the unscanned 715
seeded companies have no structured manifest.

No live candidate or source was activated by this audit.
