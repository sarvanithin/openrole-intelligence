# ADP connector policy hold

Status: **exact candidate inventory only; recurring ingestion is disabled**.

The platform recognizes only the exact public-board shapes already observed on
`workforcenow.adp.com`, `recruiting.adp.com`, and `myjobs.adp.com`. It preserves
the observed URL and tenant identifier but does not derive an ADP API URL,
request undocumented browser endpoints, automate a browser, or register ADP
with the scheduler.

## Official evidence

- ADP's official [Workforce Now Job Requisitions API guide, chapter 1](https://developers.adp.com/articles/preview/guide-job-requisitions-api-guide-for-adp-workforce-now-0?chapter=1)
  says the API returns only requisitions the requester is authorized to view.
- [Chapter 2](https://developers.adp.com/articles/preview/guide-job-requisitions-api-guide-for-adp-workforce-now-0?chapter=2)
  documents `GET /staffing/v1/job-requisitions`, requires the Job Requisition
  read scope in the Consumer Application Registry, and identifies Practitioner
  (including a system user) as the supported actor.
- [Chapter 5](https://developers.adp.com/articles/preview/guide-job-requisitions-api-guide-for-adp-workforce-now-0?chapter=5)
  documents `$top`/`$skip` pagination with a maximum of 20 records, open and
  job-seeker-visible filtering, and structured location fields.
- ADP's official [Recruiting Management Job Requisitions guide](https://developers.adp.com/articles/preview/guide-job-requisitions-v1-api-guide-for-adp-recruiting-management-0?chapter=1)
  describes client-specific API access and Marketplace API setup at both the
  client and vendor levels.

Those documented APIs are not anonymous public-board feeds, and the official
documentation does not establish a supported mapping from an observed public
career URL to authorized API access. Querying private UI internals would not
prove complete pagination, stable posting identity, opening dates, locations,
or closure reconciliation. Factory construction therefore fails closed.

Activation requires documented collection authorization or employer-issued ADP
access. A future connector must then pass complete pagination, record and
posting-date validation, US-geography compatibility, bounded HTTP, empty-manifest
safety, and two-run closure tests before scheduling.
