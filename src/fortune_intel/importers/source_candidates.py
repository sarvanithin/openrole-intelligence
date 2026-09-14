"""Import exact, primary-source-reviewed ATS candidates without crawling guesses."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
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
        else classify_ats_url(values["candidate_url"], origin="primary-source-reviewed ATS URL")
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


def import_verified_employer_portals(
    repository: JobRepository,
    csv_path: str | Path,
    *,
    actor: str,
) -> dict[str, int]:
    """Import an audited employer-portal report without inventing a connector.

    The report must identify a current official employer portal.  Only URLs
    that match one of the platform's deterministic public connectors become
    reviewable candidates; every other portal is deliberately skipped here.
    """

    if not actor.strip():
        raise ValueError("actor is required")
    report = {"rows_read": 0, "imported": 0, "existing": 0, "skipped": 0, "unmatched": 0}
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    companies = {int(item["id"]): item for item in repository.list_companies()}
    with repository.connect() as connection:
        existing_candidates = {
            (int(row["company_id"]), str(row["kind"]), str(row["base_url"]))
            for row in connection.execute("SELECT company_id, kind, base_url FROM career_sources")
        }
        existing_candidates.update(
            (int(row["company_id"]), str(row["kind"]), str(row["candidate_url"]))
            for row in connection.execute(
                "SELECT company_id, kind, candidate_url FROM career_source_candidates"
            )
        )
    coverage_updates: dict[int, str] = {}
    for row_number, row in enumerate(rows, start=2):
        report["rows_read"] += 1
        status = _value(row, "verification_status").casefold()
        if status != "verified_current_official_url":
            report["skipped"] += 1
            continue
        try:
            company_id = int(_value(row, "company_id"))
        except ValueError:
            report["unmatched"] += 1
            continue
        company = companies.get(company_id)
        if company is None or str(company["name"]) != _value(row, "company_name"):
            report["unmatched"] += 1
            continue
        candidate_url = _value(row, "verified_job_url")
        classified = classify_ats_url(candidate_url, origin="audited employer portal report")
        if classified is None:
            report["skipped"] += 1
            continue
        identity = (company_id, classified.connector_kind, classified.normalized_base_url)
        if identity in existing_candidates:
            report["existing"] += 1
            continue
        source_url = normalize_public_url(
            _value(row, "verified_company_website"),
            field=f"row {row_number} verified_company_website",
        )
        verified_date = _value(row, "verification_date")
        try:
            verified_at = datetime.fromisoformat(verified_date).replace(tzinfo=UTC).isoformat()
        except ValueError as error:
            raise ValueError(f"row {row_number}: invalid verification_date") from error
        repository.upsert_source_candidate(
            company_id,
            candidate_url=classified.normalized_base_url,
            kind=classified.connector_kind,
            confidence=0.98,
            evidence={
                "review_method": "audited_verified_employer_portal",
                "source_url": source_url,
                "board_token": classified.board_token,
                "classifier_evidence": list(classified.evidence),
                "portal_scope": _value(row, "portal_scope"),
                "verification_note": _value(row, "verification_note"),
            },
            robots_status="unknown",
            terms_status="review_required",
            discovered_at=verified_at,
        )
        coverage_updates[company_id] = verified_at
        report["imported"] += 1
        existing_candidates.add(identity)
    if coverage_updates:
        with repository.connect() as connection:
            for company_id, reviewed_at in coverage_updates.items():
                connection.execute(
                    """UPDATE company_coverage SET disposition = 'candidate',
                        reason = 'Audited current official employer portal uses a supported public ATS',
                        last_reviewed_at = ?, reviewed_by = ?, updated_at = ?
                    WHERE company_id = ? AND disposition NOT IN ('supported', 'stale')""",
                    (reviewed_at, actor.strip(), reviewed_at, company_id),
                )
    return report
