# Paycor Recruiting integration policy

Decision date: 2026-08-10

## Status

Paycor Recruiting is **inventory-only and policy-held**. The platform may
classify exact career-board URLs already found in the unknown-external
inventory, but it must not derive API routes, activate ingestion, or send probe
requests.

The current immutable, read-only snapshot contains five distinct companies and
five `recruitingbypaycor.com` observations. Every observation has the exact
`/career/CareerHome.action?clientId=<32-hex>` shape. This is an inventory
measurement, not a claim of complete company or job coverage.

## Primary-source findings

- Paycor's [Developer Portal quick start](https://developers.paycor.com/guides)
  requires a Paycor client account, application OAuth client ID and secret,
  APIM subscription key, OAuth access token, and production activation by a
  Paycor Company/Payroll/HR administrator.
- Paycor's [Public API reference](https://developers.paycor.com/try) documents
  ATS account and job-list endpoints but explicitly requires creating and
  activating a client and obtaining an access token before calls are allowed.
- Paycor's [Developer API License Agreement](https://developers.paycor.com/terms-of-use)
  requires a unique Paycor-issued API key and limits API access to issued keys.
- Paycor's [general Terms of Use](https://www.paycor.com/terms-of-use/)
  prohibit robots and other automatic means used for scraping, crawling,
  harvesting, monitoring, or copying unless Paycor expressly approves them.

Paycor therefore has an official jobs API, but it is an authenticated,
client-approved API—not an anonymous public manifest. Without tenant approval
and credentials, completeness, pagination, dates, locations, closure semantics,
and USA-only filtering cannot be validated.

## Fail-closed behavior

The classifier accepts only HTTPS URLs on the exact
`recruitingbypaycor.com` host with the exact measured CareerHome path and one
32-hex `clientId`. It rejects missing, malformed, additional, or duplicate
query values; alternate path casing; unsafe or encoded paths; credentials;
nonstandard ports; fragments; subdomains; alternate hosts; and lookalike
domains. It preserves the exact observed URL and performs zero network
requests.

Endpoint, complete-manifest, pagination, posting-date, location, USA-only
filtering, and bounded-HTTP validations remain false. The connector factory
refuses Paycor activation. Inventory classification remains
`unknown_external`; this implementation does not change schemas or discovery.

## Activation requirements

Activation requires all of the following:

1. A Paycor Developer application and APIM subscription key.
2. OAuth credentials and explicit activation by every applicable Paycor client
   administrator, with authorization for public redistribution.
3. Tenant identifiers mapped through official API responses rather than URL
   inference.
4. Contract tests proving complete external requisition pagination, posting
   dates, locations, closures, USA-only filtering, and bounded HTTP behavior.
