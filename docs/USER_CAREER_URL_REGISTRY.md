# User-supplied career URL registry

`job-intel import-career-url-registry` retains a user-supplied company-to-career-URL CSV as auditable, passive inventory. It is intentionally not a source activation mechanism.

Required columns are `company_id`, `company_name`, and `career_url`. The current registry may additionally include `verified_website_url`, `source`, and `confidence`; those values are retained as evidence. Rows with an empty `career_url` are counted but not imported.

```bash
job-intel --database data/live_index.db import-career-url-registry \
  data/exports/companies_career_urls_2026-08-14.csv \
  --actor registry-import@openrole.local \
  --observed-at 2026-08-15T00:00:00+00:00
```

The importer validates the whole file before writing anything:

- company ID and company name must exactly match the database;
- every nonblank career URL must be a public HTTP(S) URL;
- duplicate company/URL pairs abort the import;
- the file SHA-256, filename, source label, confidence, and website context are saved on each fingerprint.

Recognized public Greenhouse, Workday, Lever, Ashby, SmartRecruiters, and Oracle URLs are classified as standard ATS leads. ADP Workforce Now, iCIMS, and UKG Recruiting are classified as policy-held leads. All remaining URLs are still saved as custom or unrecognized leads. In every case, `activation_allowed` is `false`, no candidate is created, and no jobs are fetched until a separate first-party verification and policy review succeeds.

Legacy HTTP URLs are kept in the evidence record but normalized to an HTTPS-only passive fingerprint. This does not assert that the HTTPS endpoint works; later verification must confirm it.
