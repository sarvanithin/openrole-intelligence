# OpenJobs direct-identity website wave — 2026-08-13

The OpenJobs `companies_v2.json` dataset (MIT, commit
`cdcc533521afb61f4e60657b3dbe06e484ccddcf`) was used only to nominate possible
company websites. Each listed seed was fetched directly over HTTPS without
following redirects. A seed was imported only where the returned first-party
HTML `<title>` or visible page text contained the company’s exact normalized
legal name. Corporate suffixes and punctuation were normalized; short names
and third-party profile pages were rejected.

Eight exact first-party identity checks passed. Their source pages, identity
check timestamp, and importing actor are recorded in
`data/imports/openjobs_direct_identity_website_seeds_20260813.csv`. The
subsequent discovery workflow remains responsible for finding a supported ATS
link on each verified company site; a website seed itself is never a job
source.
