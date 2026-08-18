# OpenRole public roadmap

OpenRole is an evidence-first job-intelligence project for current U.S. roles.
This roadmap explains where contributions have the most leverage. It is not a
promise of employer sponsorship or a claim that every company is covered.

## Near-term priorities

### 1. Broader verified employer coverage

- Add documented, public ATS connectors with complete-pagination tests.
- Verify employer-controlled career pages and correct stale sources.
- Improve company identity matching without guessing subsidiaries or aliases.

### 2. Better U.S.-only job quality

- Improve location parsing for remote, hybrid, multi-location, and territory
  postings.
- Add regression cases when a non-U.S. role reaches a public result.
- Improve freshness and closure observations without treating a transient empty
  response as a closed job.

### 3. Explainable sponsorship evidence

- Add tests for real-world wording that should be explicit positive, explicit
  negative, or insufficient evidence.
- Improve reason text and accessibility of evidence explanations.
- Keep current job-posting text separate from historical employer-level data.

### 4. Public-beta operations

- Improve deployment, health checks, backups, and observability.
- Improve the public API documentation and developer setup experience.
- Make the job explorer faster and more accessible on mobile devices.

## A good first contribution

The most useful first issues are small, testable, and auditable:

- Correct an official employer career-page URL with public evidence.
- Add a regression test for an H-1B evidence or U.S.-location edge case.
- Improve an error message, documentation example, or accessibility detail.
- Add tests for a documented public ATS endpoint.

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before starting. Do not submit
credentials, applicant data, copied job descriptions, guessed URLs, or a
private/reverse-engineered endpoint.

## Definition of done for source coverage

A company is not reported as covered merely because a URL exists. Before a
source becomes active, OpenRole requires a reviewed, employer- or ATS-supported
source identity, allowed collection policy, a complete-manifest probe, and a
successful synchronization. See [docs/METHODOLOGY.md](docs/METHODOLOGY.md) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full model.
