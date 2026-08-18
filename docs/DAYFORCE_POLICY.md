# Dayforce connector policy hold

Status: **exact candidate inventory only; recurring ingestion is disabled**.

The platform recognizes only the exact `dayforcehcm.com` hosts and public career
URL shapes already present in the source inventory. It distinguishes candidate
portal roots from individual job-detail observations, preserves the exact URL,
and does not derive a shard, tenant, API URL, or board root. It does not request
undocumented browser endpoints, automate a browser, or register Dayforce with
the scheduler.

## Official evidence

- The official [Dayforce Job Postings API Explorer entry](https://developers.dayforce.com/Build/API-Explorer/Recruiting/Job-Postings/Get-Job-Postings.aspx)
  redirects anonymous visitors to registration/sign-in and states that setup,
  configuration, and reference documentation is available exclusively to
  developer-network members.
- The official [Dayforce API Access Rights Agreement](https://developers.dayforce.com/Build/API-Terms)
  requires explicit, verifiable consent from a Dayforce client before accessing
  that client's API data. It also requires integrations to follow the API
  documentation and prohibits exceeding or circumventing access and call limits.
- Dayforce's [Website Terms of Use](https://www.dayforce.com/uk/terms) prohibit
  sending more requests than a human can reasonably produce, obtaining materials
  through means not intentionally provided, and automated retrieval that
  circumvents the site's intended structure or navigation.

Without client consent and access to the documented contract, the platform
cannot prove a complete job manifest, pagination termination, stable posting
identity, original opening dates, structured locations, US-only compatibility,
rate limits, or closure reconciliation. The visible candidate portal and its
browser behavior are not treated as an API contract. Factory construction
therefore fails closed.

Activation requires explicit employer consent plus authorized Dayforce developer
access. A future connector must then pass complete pagination, record and
posting-date validation, US-geography compatibility, bounded HTTP, empty-manifest
safety, and two-run closure tests before scheduling.
