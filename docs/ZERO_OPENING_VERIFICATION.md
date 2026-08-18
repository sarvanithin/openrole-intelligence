# Zero-opening source verification

A verified career board can legitimately publish no current openings. An empty
response can also be caused by a bad board token, upstream outage, parser regression,
or incomplete pagination. Schema v9 distinguishes those cases with persistent,
consecutive complete-empty observations.

## Candidate approval

The first complete probe that returns zero jobs records observation `1 of 2`. It
does not approve the candidate or register a scheduled source. Run the same reviewed
approval command again as an independent probe:

```bash
job-intel approve-source-candidate 123 \
  --terms-url https://example.org/terms \
  --policy-approved-at 2026-08-10T12:00:00Z \
  --actor reviewer@example.org
```

A second consecutive complete-empty probe may approve and register the legitimate
zero-opening board. The successful approval probe is also persisted as the source's
initial complete ingestion; it is not fetched again immediately. A complete non-empty
probe resets the candidate counter to zero and follows normal approval. Failed or
incomplete probes neither increment nor reset the counter.

Bulk approval reports first observations as `empty_pending_verification`, separate
from connector failures. Rerun a reviewed batch to obtain the independent second
observation; do not edit the database counter manually.

## Scheduled synchronization

Candidate verification and registered-source verification use separate counters. A
newly approved zero-opening source therefore starts its scheduled-source counter at
zero. Its approval probe is recorded as a successful complete sync run, and the next
network fetch is scheduled after the configured interval.

For a registered source:

1. The first complete-empty manifest is `anomalous_empty`. It is not marked complete,
   cannot increment any job's missed-manifest count, and uses failure backoff.
2. The second consecutive complete-empty manifest is accepted as complete and healthy.
   It enters the ordinary two-complete-manifest closure grace period.
3. If old active jobs exist, that second empty observation records their first missed
   complete manifest. A third consecutive complete-empty manifest records the second
   miss and may close them.
4. Any complete non-empty success resets the source counter. Failed, partial, and
   incomplete runs preserve it without advancing it.

This design requires both zero-opening confirmation and the existing two-manifest
closure grace. One empty response can never activate a source or close jobs.

Operators can inspect `consecutive_complete_empty_observations` through source status
and distinguish a pending anomaly from a verified empty board. Readiness requires
schema version 9, including the counter on both `career_source_candidates` and
`career_sources`.
