"""Promote exact ATS observations from verified company-site crawls to candidates."""

from __future__ import annotations

import json

from fortune_intel.discovery.ats import classify_ats_url
from fortune_intel.services.source_provenance import verified_company_seed_evidence
from fortune_intel.storage import JobRepository

_SUPPORTED_KINDS = frozenset(
    {"ashby", "greenhouse", "lever", "oracle_recruiting", "smartrecruiters", "workday"}
)


def _record_outcome(
    repository: JobRepository,
    *,
    fingerprint_id: int,
    evidence: dict[str, object],
    outcome: str,
) -> None:
    updated = dict(evidence)
    updated["candidate_promotion_status"] = outcome
    with repository.connect() as connection:
        connection.execute(
            "UPDATE career_source_fingerprints SET evidence_json = ? WHERE id = ?",
            (json.dumps(updated, sort_keys=True, separators=(",", ":")), fingerprint_id),
        )


def promote_verified_seed_fingerprints(
    repository: JobRepository,
    *,
    actor: str,
    limit: int = 500,
) -> dict[str, int]:
    """Create candidates only when a prior crawl proves first-party provenance.

    URLs contributed by a third-party registry are deliberately excluded.  The
    stored observation must instead contain a seed URL that is still recognized
    as a verified company website or career page.
    """

    if not actor.strip():
        raise ValueError("actor is required")
    if not 1 <= limit <= 10_000:
        raise ValueError("limit must be between 1 and 10000")
    with repository.connect() as connection:
        rows = connection.execute(
            """SELECT f.id fingerprint_id, f.observed_url, f.evidence_json,
                      c.id company_id, c.name company_name, c.website_url, c.career_url
            FROM career_source_fingerprints f JOIN companies c ON c.id = f.company_id
            WHERE f.family = 'unknown_external'
              AND f.evidence_json NOT LIKE '%"candidate_promotion_status"%'
            ORDER BY f.id LIMIT ?""",
            (limit,),
        ).fetchall()
    report = {"scanned": 0, "promoted": 0, "not_supported": 0, "not_primary": 0}
    for row in rows:
        report["scanned"] += 1
        try:
            evidence = json.loads(str(row["evidence_json"]))
        except json.JSONDecodeError:
            report["not_primary"] += 1
            continue
        if evidence.get("activation_allowed") is False or evidence.get("review_method") in {
            "third_party_discovery_lead",
            "licensed_company_website_lead",
        }:
            _record_outcome(
                repository,
                fingerprint_id=int(row["fingerprint_id"]),
                evidence=evidence,
                outcome="not_primary",
            )
            report["not_primary"] += 1
            continue
        classified = classify_ats_url(str(row["observed_url"]), origin="verified seed fingerprint")
        if classified is None or classified.connector_kind not in _SUPPORTED_KINDS:
            _record_outcome(
                repository,
                fingerprint_id=int(row["fingerprint_id"]),
                evidence=evidence,
                outcome="not_supported",
            )
            report["not_supported"] += 1
            continue
        company = dict(row)
        company["id"] = int(row["company_id"])
        website, career, _ = verified_company_seed_evidence(repository, company)
        verified_seeds = {url for url in (website, career) if url}
        observed_seeds = evidence.get("seed_urls_checked")
        if not (
            verified_seeds
            and isinstance(observed_seeds, list)
            and any(str(seed) in verified_seeds for seed in observed_seeds)
        ):
            # Leave it unmarked so a future verified website seed can authorize it.
            report["not_primary"] += 1
            continue
        repository.upsert_source_candidate(
            int(row["company_id"]),
            candidate_url=classified.normalized_base_url,
            kind=classified.connector_kind,
            confidence=0.96,
            evidence={
                "review_method": "verified_seed_fingerprint_promotion",
                "source_url": str(evidence.get("origin_page") or ""),
                "seed_urls_checked": [str(seed) for seed in observed_seeds],
                "fingerprint_id": int(row["fingerprint_id"]),
                "fingerprint_evidence": evidence.get("fingerprint_evidence", []),
                "board_token": classified.board_token,
                "candidate_url": classified.candidate_url,
            },
            robots_status="unknown",
            terms_status="review_required",
        )
        coverage = repository.get_company_coverage(int(row["company_id"]))
        if coverage is not None and str(coverage["disposition"]) != "supported":
            repository.set_company_disposition(
                int(row["company_id"]),
                "candidate",
                reason="Exact supported ATS URL observed during crawl from a verified company seed",
                actor=actor,
            )
        _record_outcome(
            repository,
            fingerprint_id=int(row["fingerprint_id"]),
            evidence=evidence,
            outcome="promoted",
        )
        report["promoted"] += 1
    return report
