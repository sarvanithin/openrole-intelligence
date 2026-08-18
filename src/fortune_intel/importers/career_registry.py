"""Retain a user-supplied company career-URL registry as passive inventory.

The registry is useful evidence, but it is not a primary-source verification.
This importer therefore never creates source candidates, changes a company
website, or enables a source.  It records every syntactically safe URL with
the row-level context needed for a later first-party review.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fortune_intel.discovery.ats import classify_ats_url
from fortune_intel.discovery.passive import classify_passive_or_unknown_url
from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url
from fortune_intel.storage.coverage_schema import FINGERPRINT_FAMILIES


_POLICY_HELD_KINDS = frozenset({"adp_workforce_now", "icims_public", "ukg_recruiting_public"})
_FAMILY_FOR_POLICY_KIND = {
    "adp_workforce_now": "adp",
    "icims_public": "icims",
    "ukg_recruiting_public": "ukg",
}


@dataclass(frozen=True, slots=True)
class CareerRegistryLead:
    company_id: int
    company_name: str
    career_url: str
    original_career_url: str
    verified_website_url: str
    row_source: str
    row_confidence: str
    family: str
    proposed_kind: str
    classifier_evidence: tuple[str, ...]
    normalized_base_url: str
    board_token: str


@dataclass(frozen=True, slots=True)
class CareerRegistryImportReport:
    rows_read: int
    rows_without_career_url: int
    imported: int
    standard_ats: int
    policy_held_ats: int
    custom_or_unrecognized: int


def _value(row: dict[str, str | None], name: str) -> str:
    if None in row:
        raise ValueError("CSV row has more columns than its header")
    return (row.get(name) or "").strip()


def _required(row: dict[str, str | None], row_number: int, *names: str) -> dict[str, str]:
    values = {name: _value(row, name) for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"row {row_number}: missing {', '.join(missing)}")
    return values


def _lead(row: dict[str, str | None], row_number: int) -> CareerRegistryLead | None:
    """Parse one registry row; a blank career URL is an intentional no-op."""

    career_url = _value(row, "career_url")
    if not career_url:
        return None
    values = _required(row, row_number, "company_id", "company_name")
    try:
        company_id = int(values["company_id"])
    except ValueError as error:
        raise ValueError(f"row {row_number}: company_id must be a positive integer") from error
    if company_id < 1:
        raise ValueError(f"row {row_number}: company_id must be a positive integer")

    original_url = normalize_public_url(career_url, field="career_url")
    # Fingerprints are deliberately HTTPS-only so that passive inventory never
    # hands an insecure URL to later verification. Keep the supplied HTTP URL
    # in evidence for auditability; promotion must still confirm the endpoint.
    parts = urlsplit(original_url)
    normalized_url = urlunsplit(("https", parts.netloc, parts.path, parts.query, parts.fragment))
    classified = classify_ats_url(normalized_url, origin="user-supplied career URL registry")
    if classified is not None:
        family = _FAMILY_FOR_POLICY_KIND.get(classified.connector_kind, "unknown_external")
        return CareerRegistryLead(
            company_id=company_id,
            company_name=values["company_name"],
            career_url=normalized_url,
            original_career_url=original_url,
            verified_website_url=normalize_public_url(
                _value(row, "verified_website_url"), field="verified_website_url", optional=True
            ),
            row_source=_value(row, "source"),
            row_confidence=_value(row, "confidence"),
            family=family,
            proposed_kind=classified.connector_kind,
            classifier_evidence=classified.evidence,
            normalized_base_url=classified.normalized_base_url,
            board_token=classified.board_token,
        )

    fingerprint = classify_passive_or_unknown_url(normalized_url, origin_page=None)
    family = fingerprint.family if fingerprint is not None else "unknown_external"
    evidence = fingerprint.evidence if fingerprint is not None else (
        "User-supplied public career URL; no supported or bounded passive ATS signature matched",
    )
    return CareerRegistryLead(
        company_id=company_id,
        company_name=values["company_name"],
        career_url=normalized_url,
        original_career_url=original_url,
        verified_website_url=normalize_public_url(
            _value(row, "verified_website_url"), field="verified_website_url", optional=True
        ),
        row_source=_value(row, "source"),
        row_confidence=_value(row, "confidence"),
        family=family if family in FINGERPRINT_FAMILIES else "unknown_external",
        proposed_kind=fingerprint.family if fingerprint is not None else "custom_or_unrecognized",
        classifier_evidence=evidence,
        normalized_base_url="",
        board_token="",
    )


def _classification_totals(leads: list[CareerRegistryLead]) -> tuple[int, int, int]:
    policy_held = sum(lead.proposed_kind in _POLICY_HELD_KINDS for lead in leads)
    standard = sum(
        lead.proposed_kind not in _POLICY_HELD_KINDS
        and lead.proposed_kind
        in {
            "ashby",
            "greenhouse",
            "lever",
            "oracle_recruiting",
            "smartrecruiters",
            "workday",
        }
        for lead in leads
    )
    return standard, policy_held, len(leads) - standard - policy_held


def _preserve_completed_verification(
    repository: JobRepository,
    lead: CareerRegistryLead,
    evidence: dict[str, object],
) -> dict[str, object]:
    """Keep terminal first-party review outcomes when the registry is refreshed.

    A refreshed CSV is an update to lead provenance, not a reason to send an
    already reviewed URL through the verifier again.  Only outcomes created
    from this same registry workflow are retained; unrelated fingerprints are
    deliberately left untouched.
    """

    with repository.connect() as connection:
        row = connection.execute(
            """SELECT evidence_json FROM career_source_fingerprints
            WHERE company_id = ? AND family = ? AND observed_url = ?""",
            (lead.company_id, lead.family, lead.career_url),
        ).fetchone()
    if row is None:
        return evidence
    try:
        previous = json.loads(str(row["evidence_json"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return evidence
    if not isinstance(previous, dict):
        return evidence
    if previous.get("review_method") != "user_supplied_career_url_registry":
        return evidence
    if previous.get("verification_status") not in {"verified", "rejected", "unsupported"}:
        return evidence

    evidence["verification_status"] = previous["verification_status"]
    if "verification_attempt" in previous:
        evidence["verification_attempt"] = previous["verification_attempt"]
    return evidence


def import_career_url_registry(
    repository: JobRepository,
    csv_path: str | Path,
    *,
    actor: str,
    observed_at: str,
) -> CareerRegistryImportReport:
    """Validate a complete user registry, then retain every nonblank URL passively.

    The all-or-nothing validation prevents a partially imported registry from
    looking complete.  ``actor`` and a timezone-aware ``observed_at`` make the
    user-supplied import independently auditable.
    """

    if not actor.strip():
        raise ValueError("actor is required")
    try:
        observed = datetime.fromisoformat(observed_at)
    except ValueError as error:
        raise ValueError("observed_at must be an ISO-8601 timestamp") from error
    if observed.tzinfo is None:
        raise ValueError("observed_at must include a timezone")

    path = Path(csv_path)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        parsed = [_lead(row, row_number) for row_number, row in enumerate(csv.DictReader(handle), start=2)]
    rows_read = len(parsed)
    leads = [lead for lead in parsed if lead is not None]
    seen: set[tuple[int, str]] = set()
    with repository.connect() as connection:
        for lead in leads:
            key = (lead.company_id, lead.career_url)
            if key in seen:
                raise ValueError(
                    f"duplicate career_url for company_id {lead.company_id}: {lead.career_url}"
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
        evidence: dict[str, object] = {
            "review_method": "user_supplied_career_url_registry",
            "verification_status": "unverified",
            "activation_allowed": False,
            "primary_source_verification_required": True,
            "proposed_kind": lead.proposed_kind,
            "normalized_base_url_lead": lead.normalized_base_url,
            "board_token_lead": lead.board_token,
            "classifier_evidence": list(lead.classifier_evidence),
            "registry": {
                "filename": path.name,
                "checksum_sha256": checksum,
                "row_source": lead.row_source,
                "row_confidence": lead.row_confidence,
                "verified_website_url": lead.verified_website_url,
                "career_url_original": lead.original_career_url,
            },
        }
        repository.upsert_source_fingerprint(
            lead.company_id,
            observed_url=lead.career_url,
            family=lead.family,
            evidence=_preserve_completed_verification(repository, lead, evidence),
            actor=actor,
            observed_at=observed.isoformat(),
            mark_discovered=False,
        )
    standard, policy_held, custom = _classification_totals(leads)
    return CareerRegistryImportReport(
        rows_read=rows_read,
        rows_without_career_url=rows_read - len(leads),
        imported=len(leads),
        standard_ats=standard,
        policy_held_ats=policy_held,
        custom_or_unrecognized=custom,
    )
