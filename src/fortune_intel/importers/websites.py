"""Import reviewed canonical company websites with auditable provenance."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url


@dataclass(frozen=True, slots=True)
class WebsiteSeed:
    company_name: str
    website_url: str
    career_url: str
    source_url: str
    verified_at: str
    actor: str


def _value(row: dict[str, str], name: str) -> str:
    if None in row:
        raise ValueError("CSV row has more columns than its header")
    lowered = {key.strip().casefold(): (value or "").strip() for key, value in row.items()}
    return lowered.get(name.casefold(), "")


def _seed(row: dict[str, str], row_number: int) -> WebsiteSeed:
    values = {
        name: _value(row, name)
        for name in ("company_name", "website_url", "source_url", "verified_at", "actor")
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"row {row_number}: missing {', '.join(missing)}")
    website = normalize_public_url(values["website_url"], field="website_url")
    career = normalize_public_url(_value(row, "career_url"), field="career_url", optional=True)
    source = normalize_public_url(values["source_url"], field="source_url")
    try:
        verified = datetime.fromisoformat(values["verified_at"])
    except ValueError as error:
        raise ValueError(f"row {row_number}: invalid verified_at") from error
    if verified.tzinfo is None:
        raise ValueError(f"row {row_number}: verified_at must include a timezone")
    return WebsiteSeed(
        company_name=values["company_name"],
        website_url=website,
        career_url=career,
        source_url=source,
        verified_at=verified.isoformat(),
        actor=values["actor"],
    )


def import_company_websites(repository: JobRepository, csv_path: str | Path) -> int:
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        seeds = [
            _seed(row, row_number) for row_number, row in enumerate(csv.DictReader(handle), start=2)
        ]
    resolved: list[tuple[dict[str, object], WebsiteSeed]] = []
    for seed in seeds:
        company = repository.find_company_by_normalized_name(seed.company_name)
        if company is None:
            raise ValueError(f"company not found or ambiguous: {seed.company_name}")
        resolved.append((company, seed))
    for company, seed in resolved:
        company_id = repository.upsert_company(
            str(company["name"]),
            website_url=seed.website_url,
            career_url=seed.career_url,
        )
        coverage = repository.get_company_coverage(company_id)
        reason = f"Canonical website seed verified from {seed.source_url}"
        if seed.career_url:
            reason += f"; reviewed career URL {seed.career_url}"
        repository.set_company_disposition(
            company_id,
            str(coverage["disposition"]),
            reason=reason,
            actor=seed.actor,
            reviewed_at=seed.verified_at,
        )
    return len(resolved)
