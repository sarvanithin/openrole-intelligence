"""Import licensed company-website leads as passive inventory.

The registry accepted here is deliberately not a website-seed registry.  It is
only a traceable set of suggestions from an approved dataset.  A separate
first-party verification service must inspect the suggested site before the
``companies.website_url`` field may be written.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url

_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


@dataclass(frozen=True, slots=True)
class WebsiteLead:
    company_id: int
    company_name: str
    website_url: str
    source_dataset: str
    source_record_id: str
    source_url: str
    source_checksum: str
    license_id: str
    license_url: str
    license_reviewed_at: str
    retrieved_at: str
    actor: str


def _value(row: dict[str, str], name: str) -> str:
    if None in row:
        raise ValueError("CSV row has more columns than its header")
    lowered = {key.strip().casefold(): (value or "").strip() for key, value in row.items()}
    return lowered.get(name.casefold(), "")


def _timestamp(value: str, *, field: str, row_number: int) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"row {row_number}: invalid {field}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"row {row_number}: {field} must include a timezone")
    return parsed.isoformat()


def _lead(row: dict[str, str], row_number: int) -> WebsiteLead:
    required = {
        name: _value(row, name)
        for name in (
            "company_id",
            "company_name",
            "website_url",
            "source_dataset",
            "source_record_id",
            "source_url",
            "source_checksum",
            "license_id",
            "license_url",
            "license_status",
            "license_reviewed_at",
            "retrieved_at",
            "actor",
        )
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"row {row_number}: missing {', '.join(missing)}")
    try:
        company_id = int(required["company_id"])
    except ValueError as error:
        raise ValueError(f"row {row_number}: company_id must be a positive integer") from error
    if company_id < 1:
        raise ValueError(f"row {row_number}: company_id must be a positive integer")
    if required["license_status"].casefold() != "permitted":
        raise ValueError(
            f"row {row_number}: license_status must be permitted after operator review"
        )
    checksum = required["source_checksum"]
    if _SHA256.fullmatch(checksum) is None:
        raise ValueError(f"row {row_number}: source_checksum must be a SHA-256 hex digest")
    website = normalize_public_url(required["website_url"], field="website_url")
    if not website.startswith("https://"):
        raise ValueError(f"row {row_number}: website_url must use HTTPS")
    return WebsiteLead(
        company_id=company_id,
        company_name=required["company_name"],
        website_url=website,
        source_dataset=required["source_dataset"],
        source_record_id=required["source_record_id"],
        source_url=normalize_public_url(required["source_url"], field="source_url"),
        source_checksum=checksum.casefold(),
        license_id=required["license_id"],
        license_url=normalize_public_url(required["license_url"], field="license_url"),
        license_reviewed_at=_timestamp(
            required["license_reviewed_at"], field="license_reviewed_at", row_number=row_number
        ),
        retrieved_at=_timestamp(required["retrieved_at"], field="retrieved_at", row_number=row_number),
        actor=required["actor"],
    )


def import_website_leads(repository: JobRepository, csv_path: str | Path) -> int:
    """Validate a complete licensed registry and retain it without seeding websites."""

    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        leads = [
            _lead(row, row_number) for row_number, row in enumerate(csv.DictReader(handle), start=2)
        ]
    seen: set[tuple[int, str]] = set()
    with repository.connect() as connection:
        for lead in leads:
            key = (lead.company_id, lead.website_url)
            if key in seen:
                raise ValueError(
                    f"duplicate website_url for company_id {lead.company_id}: {lead.website_url}"
                )
            seen.add(key)
            company = connection.execute(
                "SELECT name FROM companies WHERE id = ?", (lead.company_id,)
            ).fetchone()
            if company is None or str(company["name"]) != lead.company_name:
                raise ValueError(
                    "exact company identity mismatch: company_id and company_name must match"
                )
    for lead in leads:
        repository.upsert_source_fingerprint(
            lead.company_id,
            observed_url=lead.website_url,
            family="unknown_external",
            evidence={
                "review_method": "licensed_company_website_lead",
                "verification_status": "unverified",
                "website_seed_promotion_allowed": False,
                "primary_site_identity_confirmation_required": True,
                "source_dataset": lead.source_dataset,
                "source_record_id": lead.source_record_id,
                "source_url": lead.source_url,
                "source_checksum_sha256": lead.source_checksum,
                "license": {
                    "id": lead.license_id,
                    "url": lead.license_url,
                    "status": "permitted",
                    "reviewed_at": lead.license_reviewed_at,
                },
            },
            actor=lead.actor,
            observed_at=lead.retrieved_at,
            mark_discovered=False,
        )
    return len(leads)
