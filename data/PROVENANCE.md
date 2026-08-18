# Data provenance

The code is MIT licensed. Data imported by an operator keeps the rights and
restrictions of its original source; the project license does not relicense it.

## Distributed sample

`data/sample/companies.csv` and the `seed-demo` command contain invented company
names and `example.com` URLs. They exist only for tests, screenshots, and local
product exploration.

## Not distributed

Commercial company rankings, private target lists, DOL/USCIS disclosure files,
raw job-page captures, Google credentials, local databases, and spreadsheet
exports are deliberately excluded from Git. Operators are responsible for having
the right to use their imported collection.

The original repository contained several curated company CSV files of uncertain
provenance. They were removed from the open-source working tree during this
redesign. They remain recoverable from prior Git history for a private provenance
review, but should not be republished without confirming their source and license.

## Government sources

The importer supports operator-downloaded DOL OFLC disclosure releases. The local
database records the source filename and SHA-256 checksum. Government data can
still contain employer-submitted errors and does not prove that a current role
will sponsor an applicant.

The SEC company-ticker association file can seed a broader public-company universe.
It is periodically updated and is not a list of every active U.S. business. Preserve
the retrieval date and source URL, and use the SEC-requested identifying User-Agent.

## Wikidata company URLs

The optional Wikidata importer performs an identifier join, never a name search:
the stored SEC CIK is matched to Central Index Key (P5531), then a single official
jobs URL (P10311) and a single official website (P856) may be imported. Accepted
values record the exact Wikidata item and property path in the coverage audit log.
Wikidata structured data is released under CC0, but it is community maintained and
can be incomplete, stale, or incorrect. The importer therefore refuses duplicate
CIK identities, multiple values, invalid public URLs, and conflicts with existing
values. Missing and ambiguous results remain an explicit review backlog.

Live access uses the official Wikidata Query Service in sequential `VALUES` batches,
a contactable User-Agent, gzip, Retry-After, and bounded exponential backoff. Keep
the retrieval logs with the database. A Wikidata URL is a discovery seed; it does
not bypass robots rules, terms review, connector probing, or source approval.

Primary references:

- [Wikidata CIK property P5531](https://www.wikidata.org/wiki/Property:P5531)
- [Wikidata official jobs URL P10311](https://www.wikidata.org/wiki/Property:P10311)
- [Wikidata official website P856](https://www.wikidata.org/wiki/Property:P856)
- [Wikidata Query Service interface](https://www.wikidata.org/wiki/Help:Queries)
- [Wikimedia robot policy](https://wikitech.wikimedia.org/wiki/Robot_policy)
- [Wikidata licensing](https://www.wikidata.org/wiki/Wikidata:Licensing)

## Commercial rankings

Fortune does not publish a “Fortune 5000”; its broadest U.S. revenue ranking is the
Fortune 1000. The Inc. 5000 is a separate commercial compilation. Fortune and Inc.
terms restrict scraping and redistribution, so those lists are supported only as
private operator-supplied filters unless the operator obtains written public
redistribution rights.
