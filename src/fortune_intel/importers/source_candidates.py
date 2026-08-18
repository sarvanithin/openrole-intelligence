"""Import exact, primary-source-reviewed ATS candidates without crawling guesses."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fortune_intel.discovery.ats import (
    AtsSourceCandidate,
    classify_ats_url,
    classify_official_structured_url,
)
from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url


@dataclass(frozen=True, slots=True)
class ReviewedSourceCandidate:
    company_name: str
    candidate: AtsSourceCandidate
    source_url: str
    verified_at: str
    actor: str
    robots_checked_at: str | None = None


def _value(row: dict[str, str], name: str) -> str:
    lowered = {key.strip().casefold(): (value or "").strip() for key, value in row.items()}
    return lowered.get(name.casefold(), "")


def _candidate(row: dict[str, str], row_number: int) -> ReviewedSourceCandidate:
    values = {
        name: _value(row, name)
        for name in ("company_name", "candidate_url", "source_url", "verified_at", "actor")
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"row {row_number}: missing {', '.join(missing)}")
    expected_kind = _value(row, "kind").casefold()
    classified = (
        classify_official_structured_url(
            values["candidate_url"], origin="primary-source-reviewed structured manifest"
        )
        if expected_kind == "official_structured"
        else classify_ats_url(
            values["candidate_url"], origin="primary-source-reviewed ATS URL"
        )
    )
    if classified is None:
        raise ValueError(f"row {row_number}: candidate_url is not a recognized exact ATS URL")
    if expected_kind and expected_kind != classified.connector_kind:
        raise ValueError(
            f"row {row_number}: expected {expected_kind}, classified {classified.connector_kind}"
        )
    source_url = normalize_public_url(values["source_url"], field="source_url")
    try:
        verified = datetime.fromisoformat(values["verified_at"])
    except ValueError as error:
        raise ValueError(f"row {row_number}: invalid verified_at") from error
    if verified.tzinfo is None:
        raise ValueError(f"row {row_number}: verified_at must include a timezone")
    robots_checked_at = None
    if classified.connector_kind == "icims_public":
        robots_url = normalize_public_url(_value(row, "robots_url"), field="robots_url")
        expected_robots = f"https://{urlsplit(classified.normalized_base_url).hostname}/robots.txt"
        if robots_url != expected_robots or _value(row, "robots_status").casefold() != "allowed":
            raise ValueError(
                f"row {row_number}: iCIMS requires an exact same-host allowed robots review"
            )
        try:
            robots_checked = datetime.fromisoformat(_value(row, "robots_checked_at"))
        except ValueError as error:
            raise ValueError(f"row {row_number}: invalid robots_checked_at") from error
        if robots_checked.tzinfo is None:
            raise ValueError(f"row {row_number}: robots_checked_at must include a timezone")
        robots_checked_at = robots_checked.isoformat()
    return ReviewedSourceCandidate(
        company_name=values["company_name"],
        candidate=classified,
        source_url=source_url,
        verified_at=verified.isoformat(),
        actor=values["actor"],
        robots_checked_at=robots_checked_at,
    )


def import_reviewed_source_candidates(repository: JobRepository, csv_path: str | Path) -> int:
    """Validate an entire registry, then persist exact candidates and audit events."""

    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        seeds = [
            _candidate(row, row_number)
            for row_number, row in enumerate(csv.DictReader(handle), start=2)
        ]
    resolved: list[tuple[dict[str, object], ReviewedSourceCandidate]] = []
    for seed in seeds:
        company = repository.find_company_by_normalized_name(seed.company_name)
        if company is None:
            raise ValueError(f"company not found or ambiguous: {seed.company_name}")
        resolved.append((company, seed))
    for company, seed in resolved:
        candidate = seed.candidate
        company_id = int(company["id"])
        repository.upsert_source_candidate(
            company_id,
            candidate_url=candidate.normalized_base_url,
            kind=candidate.connector_kind,
            confidence=candidate.confidence,
            evidence={
                "review_method": "primary_source_exact_ats_url",
                "source_url": seed.source_url,
                "board_token": candidate.board_token,
                "classifier_evidence": list(candidate.evidence),
            },
            robots_status="allowed" if seed.robots_checked_at else "unknown",
            robots_checked_at=seed.robots_checked_at,
            terms_status="review_required",
            discovered_at=seed.verified_at,
        )
        coverage = repository.get_company_coverage(company_id)
        disposition = str(coverage["disposition"])
        if disposition not in {"supported", "stale"}:
            repository.set_company_disposition(
                company_id,
                "candidate",
                reason=(
                    f"Reviewed {candidate.connector_kind} ATS candidate verified from "
                    f"{seed.source_url}"
                ),
                actor=seed.actor,
                reviewed_at=seed.verified_at,
            )
    return len(resolved)
