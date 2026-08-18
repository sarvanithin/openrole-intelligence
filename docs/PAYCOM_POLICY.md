# Paycom recruiting integration policy

Decision date: 2026-08-10

## Status

Paycom is **inventory-only and policy-held**. The platform may locally classify
exact career URLs already found in the unknown-external inventory, but it must
not activate ingestion, derive private endpoints, or send probe requests.

The current immutable, read-only snapshot contains 20 distinct companies and 31
exact `www.paycomonline.net` observations, exceeding the earlier 10+ estimate.
They comprise 24 board URLs and seven job-detail URLs across the legacy query
and newer portal path families. This measurement is not a claim of complete
company or job coverage.

## Primary-source findings

- Paycom's [Terms of Use](https://www.paycom.com/terms-of-use/) prohibit using
  automated systems or software to extract or scrape data from Paycom websites
  and interfaces unless Paycom provides written authorization. They also limit
  site use to personal, noncommercial use absent a written agreement.
- Paycom's [General Terms and Conditions](https://cdn.paycom.com/mkon/www/media/resources-content/General_Terms_and_Conditions.pdf)
  state that API and SFTP Data Services may require Paycom-owned access keys,
  restrict them to client data and authorized purposes, and allow API-call
  limits.
- Paycom's [Applicant Tracking product documentation](https://www.paycom.com/software/applicant-tracking/)
  describes customer-controlled posting to websites, career sites, and job
  boards. It does not document an anonymous complete jobs API.

No official anonymous contract was verified for complete enumeration,
pagination, posting dates, locations, closure semantics, or USA-only filtering.
Public applicant URLs are not authorization to scrape or reverse-engineer their
backing services.

## Fail-closed behavior

The classifier recognizes only the exact measured shapes:

- `/v4/ats/web.php/jobs?clientkey=<32-hex>` board URLs, with only the measured
  `fromClientSide` or `session_nonce` additions;
- `/v4/ats/web.php/jobs/ViewJobDetails` with exact numeric job and client keys;
- `/v4/ats/web.php/portal/<client-key>/career-page`; and
- `/v4/ats/web.php/portal/<client-key>/jobs/<job-id>`.

It rejects unsafe or encoded paths, malformed or duplicate identifiers,
unrecognized query keys, incompatible query combinations, non-HTTPS URLs,
credentials, nonstandard ports, fragments, alternate hosts, subdomains, and
lookalike domains. It preserves exact observed URLs and performs zero network
requests.

Endpoint, complete-manifest, pagination, posting-date, location, USA-only
filtering, and bounded-HTTP validations remain false. The connector factory
refuses Paycom activation. The inventory remains classified as
`unknown_external`; this implementation does not alter schemas or discovery
classification.

## Activation requirements

Activation requires all of the following:

1. Written Paycom authorization and authorization from every applicable client
   permitting automated collection and public redistribution.
2. Paycom-issued access keys stored in the production secret manager.
3. An official jobs API contract proving complete external requisition
   enumeration, pagination, posting dates, locations, and closure semantics.
4. Proven USA-only filtering and bounded HTTP behavior before scheduler
   registration.
