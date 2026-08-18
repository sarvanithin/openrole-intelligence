# Oracle Recruiting connector

The connector accepts only an explicit public Oracle Candidate Experience URL:

```text
https://<tenant>.oraclecloud.com/hcmUI/CandidateExperience/<locale>/sites/<site>
```

Discovery extracts the tenant host, locale, and site from that verified link. It
does not generate tenant names or try alternate site numbers. Hosts must be HTTPS
Oracle Cloud subdomains, identifiers are character-allowlisted, credentials and
nonstandard ports are rejected, and the JSON client never follows redirects.

## Completeness and dates

The anonymous Candidate Experience listing response is paged with Oracle's
`findReqs` finder. `limit` and `offset` stay inside that finder. Every page must
return the configured site number and requested offset, and `TotalJobsCount` must
remain stable. A total change, duplicate ID, malformed record, missing page, detail
failure, or page cap makes the manifest incomplete, so it cannot close jobs.

Each listing is enriched from the anonymous Candidate Experience detail response.
`ExternalPostedStartDate` is stored as `source_opened_at`; `PostedDate` is used only
when the detail has no exact timestamp. Full external description, canonical job
URL, primary and secondary locations, schedule, workplace type, and native
requisition identity are retained with field-level provenance.

Oracle's Fusion Cloud HCM reference documents the requisition finder, paging
fields, and posting-date response shapes. Oracle labels these Candidate Experience
resources for internal use, so their presence does not grant collection permission:

- https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingcejobrequisitions-get.html
- https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingcejobrequisitiondetails-get.html
- https://www.oracle.com/legal/terms/

An operator must review the applicable employer and vendor policy and run the
standard complete, non-empty probe before scheduling a source. Vendor-family
recognition is not permission to collect a particular employer's board.
