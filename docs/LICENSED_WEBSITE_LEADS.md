# Licensed website leads

`job-intel import-website-leads` accepts an approved/licensed CSV of exact
company IDs and proposed HTTPS company websites. It is a passive inventory
operation: it cannot write `companies.website_url`, create a career-source
candidate, or activate a source.

Required CSV columns are:

```text
company_id,company_name,website_url,source_dataset,source_record_id,source_url,
source_checksum,license_id,license_url,license_status,license_reviewed_at,
retrieved_at,actor
```

The importer verifies the stored company ID and legal name exactly, requires a
SHA-256 dataset checksum, an HTTPS source/license URL, timezone-bearing review
timestamps, and `license_status=permitted`. The original dataset provenance is
stored with the passive fingerprint.

## First-party promotion

Run the bounded verifier with:

```bash
job-intel --db data/live_index.db verify-website-leads --actor acquisition-scheduler
```

For each still-unseeded company it makes one capped HTTPS request without
following redirects. A suggestion becomes a website seed only when the response
is successful HTML and the site declares the exact normalized company identity
in one of these self-published surfaces:

- `Organization`, `Corporation`, `Company`, or `GovernmentOrganization` JSON-LD
  `legalName`, `name`, or `alternateName`; or
- `application-name` or `og:site_name` metadata.

A title, arbitrary page text, redirect, non-HTML response, malformed JSON-LD,
or near-name match is not enough. Each result records the decision, direct-page
identity surface, content type, HTTP status, and body SHA-256. Failed leads are
retained as rejected evidence; they are never guessed, retried as live sources,
or published as jobs.

Promotion only sets a verified company website. Career discovery and source
activation remain separate complete-manifest and policy-gated steps.
