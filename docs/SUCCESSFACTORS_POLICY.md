# SAP SuccessFactors connector policy hold

Status: **exact candidate inventory only; recurring ingestion is disabled**.

The platform recognizes only the exact SuccessFactors and `sapsf.com` hosts and
career URL shapes already present in the source inventory. It distinguishes
career search pages from signed individual job-detail observations and preserves
the exact URL. It does not derive an API server, data-center shard, company URL,
or search endpoint, automate a browser, or register SuccessFactors with the
scheduler.

## Official evidence

- SAP's official [JobRequisition OData reference](https://help.sap.com/docs/successfactors-platform/sap-successfactors-api-reference-guide-odata-v2/jobrequisition)
  requires Recruiting operator field permissions or administrative OData API
  Requisition Export permission. It also notes that the Recruiting OData API must
  be enabled in Provisioning.
- The official [OAuth access-token guide](https://help.sap.com/docs/successfactors-platform/sap-successfactors-hcm-suite-sfapi-developer-guide/requesting-access-token)
  requires a company ID, registered client/API key, and signed SAML assertion;
  API access can additionally be restricted by source IP.
- The official [JobRequisitionPosting reference](https://help.sap.com/docs/successfactors-platform/sap-successfactors-api-reference-guide-odata-v2/jobrequisitionposting)
  documents posting start/end dates, but access requires Recruiting OData
  permissions.
- SAP documents collection pagination using [`$top` and `$skip`](https://help.sap.com/docs/successfactors-platform/sap-successfactors-api-reference-guide-odata-v2/query-with-top-and-skip).
- SAP's official [third-party Recruiting integration guidance](https://help.sap.com/docs/successfactors-recruiting/integrating-recruiting-with-third-party-vendors)
  says to have a contract with a third-party vendor before configuring an
  integration. SAP separately recommends configured XML feeds when distributing
  jobs in bulk to job boards or compliance networks.

The supported API can represent pagination and posting dates, but it is not an
anonymous career-site feed. No official contract was found that maps the exact
observed public career URL to authorized OData access or guarantees that the
browser-facing page exposes a complete manifest. The platform therefore cannot
validate complete pagination, stable identity, original opening dates,
structured locations, US-only compatibility, bounded request behavior, or
closure reconciliation. Factory construction fails closed.

Activation requires employer-authorized SAP OAuth credentials and Recruiting
permissions, or an employer-configured job-distribution feed. A future connector
must still pass complete pagination, record and posting-date validation,
US-geography compatibility, bounded HTTP, empty-manifest safety, and two-run
closure tests before scheduling.
