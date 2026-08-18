# Connector fixture provenance

The `workday_*.json` fixtures are minimized from anonymous responses served by
Workday's own external career site on 2026-08-07:

- Listing: `POST https://workday.wd5.myworkdayjobs.com/wday/cxs/workday/Workday/jobs`
- Detail: `GET https://workday.wd5.myworkdayjobs.com/wday/cxs/workday/Workday/job/...`
- Recruiting-path hosts use the same fixed-host CXS shape, for example
  `POST https://wd1.myworkdaysite.com/wday/cxs/snapchat/snap/jobs`; their public
  board remains `https://wd1.myworkdaysite.com/recruiting/snapchat/snap`.

Names, identifiers, paths, and field shapes are retained where they exercise
the connector contract; the long job description was shortened.

Protocol interpretation is constrained by Workday's official documentation for
[external career sites](https://doc.workday.com/workday-education/en-us/course-manuals/recruiting-for-administrators/career-sites.html),
which states that the public job-listing page contains active jobs, locations,
requisition numbers, and days posted, while the job-detail page can display the
posted date, employee type, and description. The connector treats the exact
`startDate` from the detail response as the source-provided opening date and
keeps the relative `postedOn` label only as metadata.

The `oracle_recruiting_*.json` fixtures are minimized from anonymous Oracle
Candidate Experience listing and detail responses retrieved on 2026-08-07. They
preserve the site number, native job identity, offset/total, exact external
posting-start timestamp, and response shapes used to verify completeness and
date provenance. The description and locations were shortened.
