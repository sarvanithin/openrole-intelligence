"""Import licensed third-party career URL leads as passive, unverified inventory."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fortune_intel.discovery.ats import classify_ats_url
from fortune_intel.discovery.passive import classify_passive_or_unknown_url
from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url
from fortune_intel.storage.coverage_schema import FINGERPRINT_FAMILIES

_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


@dataclass(frozen=True, slots=True)
class DiscoveryLead:
    company_id: int
    company_name: str
    lead_url: str
    family: str
    proposed_kind: str
    classifier_evidence: tuple[str, ...]
    normalized_base_url: str
    board_token: str
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


def _lead(row: dict[str, str], row_number: int) -> DiscoveryLead:
    required = {
        name: _value(row, name)
        for name in (
            "company_id",
            "company_name",
            "lead_url",
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
    lead_url = required["lead_url"].strip()
    normalize_public_url(lead_url, field="lead_url")
    if not lead_url.startswith("https://"):
        raise ValueError(f"row {row_number}: lead_url must use HTTPS")
    source_url = normalize_public_url(required["source_url"], field="source_url")
    license_url = normalize_public_url(required["license_url"], field="license_url")
    classified = classify_ats_url(lead_url, origin="unverified third-party discovery lead")
    normalized_base_url = ""
    board_token = ""
    if classified is not None:
        # Supported connector families are deliberately stored in passive inventory,
        # whose current schema uses unknown_external for non-policy-held families.
        passive_kind = (
            "icims" if classified.connector_kind == "icims_public" else classified.connector_kind
        )
        family = passive_kind if passive_kind in FINGERPRINT_FAMILIES else "unknown_external"
        proposed_kind = classified.connector_kind
        classifier_evidence = classified.evidence
        normalized_base_url = classified.normalized_base_url
        board_token = classified.board_token
    else:
        fingerprint = classify_passive_or_unknown_url(lead_url, origin_page=source_url)
        if fingerprint is None:
            raise ValueError(
                f"row {row_number}: lead_url is not a recognized bounded career or ATS URL"
            )
        family = fingerprint.family
        proposed_kind = fingerprint.family
        classifier_evidence = fingerprint.evidence
    expected_kind = _value(row, "kind").casefold()
    compatible_kind = expected_kind == "icims" and proposed_kind == "icims_public"
    if expected_kind and expected_kind != proposed_kind and not compatible_kind:
        raise ValueError(f"row {row_number}: expected {expected_kind}, classified {proposed_kind}")
    return DiscoveryLead(
        company_id=company_id,
        company_name=required["company_name"],
        lead_url=lead_url,
        family=family,
        proposed_kind=proposed_kind,
        classifier_evidence=classifier_evidence,
        normalized_base_url=normalized_base_url,
        board_token=board_token,
        source_dataset=required["source_dataset"],
        source_record_id=required["source_record_id"],
        source_url=source_url,
        source_checksum=checksum.casefold(),
        license_id=required["license_id"],
        license_url=license_url,
        license_reviewed_at=_timestamp(
            required["license_reviewed_at"],
            field="license_reviewed_at",
            row_number=row_number,
        ),
        retrieved_at=_timestamp(
            required["retrieved_at"], field="retrieved_at", row_number=row_number
        ),
        actor=required["actor"],
    )


def import_discovery_leads(repository: JobRepository, csv_path: str | Path) -> int:
    """Validate the full licensed registry, then retain leads without activating them."""

    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        leads = [
            _lead(row, row_number) for row_number, row in enumerate(csv.DictReader(handle), start=2)
        ]
    seen: set[tuple[int, str]] = set()
    with repository.connect() as connection:
        for lead in leads:
            key = (lead.company_id, lead.lead_url)
            if key in seen:
                raise ValueError(
                    f"duplicate lead_url for company_id {lead.company_id}: {lead.lead_url}"
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
            observed_url=lead.lead_url,
            family=lead.family,
            evidence={
                "review_method": "third_party_discovery_lead",
                "verification_status": "unverified",
                "activation_allowed": False,
                "primary_source_verification_required": True,
                "proposed_kind": lead.proposed_kind,
                "normalized_base_url_lead": lead.normalized_base_url,
                "board_token_lead": lead.board_token,
                "classifier_evidence": list(lead.classifier_evidence),
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
