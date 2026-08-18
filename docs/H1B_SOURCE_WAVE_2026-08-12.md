# H-1B source-acquisition wave — 2026-08-12

This wave began with 335 `reviewed_exact_legal_name` H-1B companies that did not
have an enabled source with a recorded successful sync. It prioritized the 85
companies carrying a `third_party_discovery_lead` fingerprint. Third-party
agreement was used only as a lead: candidate ATS URLs were registered only when
observed on a company-controlled careers page or an official redirect.

## Outcome

- Priority companies crawled from stored official seeds: 85
- Newly verified ATS candidates: 15 URLs across 13 companies
- Pending supported candidates gated: 41 URLs across 33 companies
- Activated after policy review and a complete-manifest probe: 27 sources across
  20 companies
- Jobs transactionally ingested by those 27 sources: 6,719
- Probe failures retained as `discovered`: 14 candidates across 14 companies
- Candidates marked `rejected`: 0
- Exact-match H-1B companies still lacking a healthy source: 315

The two complete-empty First Solar sites (`CX_10` and `CX_7`) were activated
only after a second independent complete-empty observation. They created no
jobs.

## Newly verified official-link candidates

The auditable import is
`data/imports/h1b_third_party_leads_reviewed_candidates_20260813.csv`.

- Amplitude, Inc. — Greenhouse
- BORGWARNER INC — Workday
- Bandwidth Inc. — Greenhouse
- CORCEPT THERAPEUTICS INC — Greenhouse
- EXTREME NETWORKS INC — Lever
- Five9, Inc. — Greenhouse
- Freshworks Inc. — SmartRecruiters
- OOMA INC — Greenhouse
- RESMED INC — three Workday boards
- Sonos Inc — Workday
- Sprinklr, Inc. — Workday
- TANDEM DIABETES CARE INC — Workday
- VIAVI SOLUTIONS INC. — Workday

## Activated sources and jobs ingested

| Company | Sources | Jobs |
| --- | ---: | ---: |
| Amplitude, Inc. | 1 | 31 |
| BORGWARNER INC | 1 | 314 |
| Bandwidth Inc. | 1 | 33 |
| CORCEPT THERAPEUTICS INC | 1 | 72 |
| Cheniere Energy, Inc. | 1 | 24 |
| EXTREME NETWORKS INC | 1 | 120 |
| FIRST SOLAR, INC. | 6 | 293 |
| Five9, Inc. | 1 | 141 |
| Freshworks Inc. | 1 | 153 |
| HONEYWELL INTERNATIONAL INC | 1 | 1,296 |
| HUMANA INC | 2 | 2,216 |
| Hub Group, Inc. | 1 | 99 |
| MYRIAD GENETICS INC | 1 | 3 |
| ONTO INNOVATION INC. | 1 | 212 |
| OOMA INC | 1 | 11 |
| RESMED INC | 2 | 12 |
| ROYAL BANK OF CANADA | 1 | 1,428 |
| Sprinklr, Inc. | 1 | 85 |
| TANDEM DIABETES CARE INC | 1 | 36 |
| VIAVI SOLUTIONS INC. | 1 | 140 |

## Probe failures retained for correction or retry

- ABBOTT LABORATORIES — duplicate Workday native job ID
- AMERICAN EAGLE OUTFITTERS INC — duplicate Oracle native job IDs
- AXCELIS TECHNOLOGIES INC — invalid Workday external job URLs
- Block, Inc. — non-absolute Greenhouse job URLs
- COPART INC — Workday records missing required titles
- EMERSON ELECTRIC CO — duplicate Oracle native job ID
- Guidewire Software, Inc. — invalid Workday external job URLs
- JPMORGAN CHASE & CO — Oracle total changed during pagination
- MAGNITE, INC. — configured Workday listing endpoint returned HTTP 422
- MICROCHIP TECHNOLOGY INC — invalid Workday external job URLs
- Neutron Holdings, Inc. — Ashby endpoint returned HTTP 404
- REGENERON PHARMACEUTICALS, INC. — invalid Workday external job URLs
- RESMED INC (`Resmed_External_Careers`) — invalid Workday external job URLs;
  its other two verified boards activated successfully
- Sonos Inc — invalid Workday external job URLs

## Final disposition of the 85 priority companies

Supported (13): Amplitude, BORGWARNER, Bandwidth, Corcept Therapeutics,
Extreme Networks, Five9, Freshworks, Humana, Ooma, ResMed, Sprinklr, Tandem
Diabetes Care, and VIAVI Solutions.

Candidate retained after a failed probe (1): Sonos.

Blocked by robots, an unsafe redirect, or seed fetch failure (31): AutoNation,
Angi, Box, Beam Therapeutics, CI&T, Cable One, Cyngn, DaVita, Dexcom, Doximity,
Equinix, Etsy, Eikon Therapeutics, Fate Therapeutics, Gartner, Hasbro, HP,
MicroVision, MNTN, Nasdaq, Plexus, Prime Medicine, Progyny, PubMatic, Revvity,
Standard BioTools, Summit Therapeutics, Syndax Pharmaceuticals, Upwork, Yext,
and ZipRecruiter.

No supported deterministic ATS found in the bounded official crawl (40): 10x
Genomics, Avnet, Blackbaud, Block, Braze, CleanSpark, Conagra Brands, Copart,
CarGurus, Coursera, Cricut, Dick's Sporting Goods, Datadog, Ecolab, Five Below,
Gap, Groupon, Ingredion, Inspire Medical Systems, LivePerson, Life360,
MillerKnoll, Marqeta, Morningstar, NerdWallet, Public Storage, Robert Half,
Spire, Salesforce, Septerna, Stitch Fix, Stride, Trimble, Tyson Foods, Toast,
Ubiquiti, Ultragenyx Pharmaceutical, Veracyte, WEX, and Xometry.

## Validation

- `PRAGMA integrity_check`: `ok`
- `tests/test_source_candidate_importer.py`
- `tests/test_bulk_source_approval.py`
- `tests/test_source_approval.py`
- Result: 15 tests passed

Pre-wave database backup:
`data/backups/live_index-before-h1b-source-wave-20260812.db`.
