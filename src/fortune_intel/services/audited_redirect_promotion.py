"""Promote safely audited career redirects into review-only ATS candidates.

The overnight audit is intentionally read-only and records redirects without
following them.  This service is the separate, opt-in second gate: it accepts
only audit records whose origin still belongs to the company in the database,
validates the absolute HTTPS redirect destination resolves publicly, and then
checks the exact ATS board for the company's identity.  It never enables a
source; the regular complete-manifest approval flow remains required.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fortune_intel.discovery.ats import classify_ats_url
from fortune_intel.services.licensed_lead_verification import (
    LeadPage,
    _identity_surface,
    fetch_lead_page,
)
from fortune_intel.storage import JobRepository

_AUDIT_TARGET_TYPES = frozenset({"enabled_source", "registry_supported_ats", "registry_portal"})


def _public_https_url(
    raw_url: str,
    *,
    resolver: Callable[[str], Iterable[str]],
) -> str | None:
    """Normalize an absolute public HTTPS URL without making an HTTP request."""

    parsed = urlsplit(raw_url.strip())
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    host = parsed.hostname.casefold().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    try:
        addresses = tuple(resolver(host))
    except (OSError, ValueError):
        return None
    if not addresses:
        return None
    try:
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            return None
    except ValueError:
        return None
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _default_resolver(host: str) -> Iterable[str]:
    return {entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}


def _record_key(record: dict[str, Any]) -> str:
    return f"{record['target_type']}:{record['company_id']}:{record['url']}"


def _valid_redirect_record(record: object) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    if record.get("outcome") != "redirect" or record.get("target_type") not in _AUDIT_TARGET_TYPES:
        return None
    if not isinstance(record.get("company_id"), int) or record["company_id"] <= 0:
        return None
    if not isinstance(record.get("url"), str) or not isinstance(record.get("location"), str):
        return None
    status = record.get("http_status")
    if not isinstance(status, int) or not 300 <= status < 400:
        return None
    if record.get("key") != _record_key(record):
        return None
    return record


def _audit_origin_is_current(repository: JobRepository, record: dict[str, Any]) -> bool:
    """Do not trust a JSONL entry unless its logged origin still belongs to it."""

    company_id = int(record["company_id"])
    source_url = str(record["url"])
    with repository.connect() as connection:
        if record["target_type"] == "enabled_source":
            row = connection.execute(
                """SELECT 1 FROM career_sources
                WHERE company_id = ? AND base_url = ? AND enabled = 1 LIMIT 1""",
                (company_id, source_url),
            ).fetchone()
        else:
            row = connection.execute(
                """SELECT 1 FROM career_source_fingerprints
                WHERE company_id = ? AND observed_url = ?
                  AND json_extract(evidence_json, '$.review_method')
                        = 'user_supplied_career_url_registry'
                LIMIT 1""",
                (company_id, source_url),
            ).fetchone()
    return row is not None


def _load_redirect_records(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            record = _valid_redirect_record(parsed)
            if record is not None:
                yield record


def promote_audited_redirects(
    repository: JobRepository,
    *,
    audit_results_path: str | Path,
    actor: str,
    policy_urls: dict[str, str],
    policy_approved_at: str,
    limit: int = 100,
    resolver: Callable[[str], Iterable[str]] = _default_resolver,
    page_fetcher: Callable[[str], LeadPage] = fetch_lead_page,
) -> dict[str, int]:
    """Create candidates from direct ATS redirects, never career sources.

    The audit file is an input queue, not a trust authority.  Records are
    reconciled to an enabled first-party source or a user-supplied registry
    observation before their redirect location is considered.
    """

    if not actor.strip():
        raise ValueError("actor is required")
    if not policy_approved_at.strip():
        raise ValueError("policy_approved_at is required")
    if not 1 <= limit <= 10_000:
        raise ValueError("limit must be between 1 and 10000")
    audit_path = Path(audit_results_path).expanduser()
    if not audit_path.is_file():
        raise ValueError("audit_results_path must be a readable JSONL file")

    report = {"scanned": 0, "verified": 0, "rejected": 0, "skipped": 0}
    seen: set[tuple[int, str]] = set()
    for record in _load_redirect_records(audit_path):
        if report["scanned"] >= limit:
            break
        redirect_url = _public_https_url(str(record["location"]), resolver=resolver)
        key = (int(record["company_id"]), redirect_url or str(record["location"]))
        if key in seen:
            continue
        seen.add(key)
        report["scanned"] += 1
        if redirect_url is None or not _audit_origin_is_current(repository, record):
            report["skipped"] += 1
            continue
        candidate = classify_ats_url(
            redirect_url,
            origin="audited redirect from a verified company career URL",
        )
        if candidate is None or candidate.connector_kind not in policy_urls:
            report["skipped"] += 1
            continue
        page = page_fetcher(candidate.normalized_base_url)
        if page.final_url != candidate.normalized_base_url:
            report["rejected"] += 1
            continue
        identity = _identity_surface(str(record.get("company_name") or ""), page)
        if identity is None:
            report["rejected"] += 1
            continue
        surface, title = identity
        company_id = int(record["company_id"])
        repository.upsert_source_candidate(
            company_id,
            candidate_url=candidate.normalized_base_url,
            kind=candidate.connector_kind,
            confidence=0.99,
            evidence={
                "review_method": "audited_redirect_exact_ats_identity",
                "verification_status": "verified",
                "activation_allowed": False,
                "board_token": candidate.board_token,
                "candidate_url": candidate.candidate_url,
                "audit_provenance": {
                    "audit_results_path": str(audit_path.resolve()),
                    "audit_key": record["key"],
                    "source_url": record["url"],
                    "redirect_location": redirect_url,
                    "http_status": record["http_status"],
                    "started_at": record.get("started_at"),
                    "completed_at": record.get("completed_at"),
                },
                "identity_check": {
                    "method": "direct_ats_html_exact_normalized_company_name",
                    "surface": surface,
                    "title": title,
                    "status": page.status,
                    "content_type": page.content_type,
                    "body_sha256": hashlib.sha256(page.body).hexdigest(),
                },
            },
            terms_url=policy_urls[candidate.connector_kind],
            terms_status="permitted",
            terms_reviewed_at=policy_approved_at,
            discovered_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        )
        coverage = repository.get_company_coverage(company_id)
        if coverage is not None and str(coverage["disposition"]) != "supported":
            repository.set_company_disposition(
                company_id,
                "candidate",
                reason="Audited first-party redirect verified at exact ATS board identity",
                actor=actor,
            )
        report["verified"] += 1
    return report
