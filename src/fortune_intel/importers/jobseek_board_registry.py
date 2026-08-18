"""Import Jobseek board configuration as attributed, passive discovery inventory.

The Jobseek ``boards.csv`` registry is a valuable lead source, not primary
employer evidence.  This importer intentionally never registers a career
source, never creates a schedulable source candidate, and never imports a job
posting.  Every usable board is retained as a fingerprint for the existing
first-party verification workflow.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fortune_intel.connectors.eightfold import classify_eightfold_board_url
from fortune_intel.discovery.ats import AtsSourceCandidate, classify_ats_url
from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_schema import FINGERPRINT_FAMILIES


JOBSEEK_REPOSITORY_URL = "https://github.com/colophon-group/jobseek"
JOBSEEK_BOARDS_PATH = "apps/crawler/data/boards.csv"
JOBSEEK_COMPANIES_PATH = "apps/crawler/data/companies.csv"
JOBSEEK_LICENSE_NOTICE_PATH = "LICENSE-JOB-DATA"

_REVISION = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_WORKDAY_INSTANCE = re.compile(r"^wd[0-9]{1,4}$", re.IGNORECASE)
_DIRECT_KINDS = frozenset(
    {
        "adp_workforce_now",
        "ashby",
        "greenhouse",
        "icims_public",
        "lever",
        "oracle_recruiting",
        "smartrecruiters",
        "ukg_recruiting_public",
        "workday",
    }
)
_FAMILY_BY_KIND = {
    "adp_workforce_now": "adp",
    "icims_public": "icims",
    "ukg_recruiting_public": "ukg",
}


@dataclass(frozen=True, slots=True)
class JobseekBoardImportReport:
    rows_read: int
    imported: int
    policy_held: int
    unsupported: int
    unmatched_companies: int


@dataclass(frozen=True, slots=True)
class _BoardLead:
    company_name: str
    company_slug: str
    board_slug: str
    board_url: str
    monitor_type: str
    monitor_config: dict[str, Any]
    scraper_type: str
    scraper_config: dict[str, Any]
    candidate: AtsSourceCandidate | None
    policy_held_kind: str = ""


def _value(row: dict[str, str | None], name: str) -> str:
    if None in row:
        raise ValueError("CSV row has more columns than its header")
    return (row.get(name) or "").strip()


def _read_csv(path: str | Path) -> list[dict[str, str | None]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _config(value: str, *, field: str, row_number: int) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"row {row_number}: invalid {field} JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError(f"row {row_number}: {field} must be a JSON object")
    return decoded


def _workday_from_config(
    board_url: str,
    monitor_type: str,
    monitor_config: dict[str, Any],
) -> AtsSourceCandidate | None:
    """Use an explicit Jobseek Workday config only when it names every component.

    The registry sometimes places a company landing page in ``board_url`` but
    stores the precise public Workday tenant/site configuration alongside it.
    This is data normalization, not URL discovery: absent or malformed fields
    are rejected rather than inferred.
    """

    if monitor_type != "workday":
        return None
    company = monitor_config.get("company")
    instance = monitor_config.get("wd_instance")
    site = monitor_config.get("site")
    if not all(isinstance(value, str) and value.strip() for value in (company, instance, site)):
        return None
    tenant = company.strip()
    normalized_instance = instance.strip().casefold()
    career_site = site.strip()
    if not _WORKDAY_INSTANCE.fullmatch(normalized_instance):
        return None
    candidate_url = f"https://{tenant}.{normalized_instance}.myworkdayjobs.com/{career_site}"
    candidate = classify_ats_url(
        candidate_url, origin="exact Jobseek Workday monitor configuration"
    )
    if candidate is None or candidate.connector_kind != "workday":
        return None
    return candidate


def _lead(
    row: dict[str, str | None],
    company_names: dict[str, str],
    row_number: int,
) -> _BoardLead | None:
    company_slug = _value(row, "company_slug")
    board_slug = _value(row, "board_slug")
    board_url = _value(row, "board_url")
    monitor_type = _value(row, "monitor_type").casefold()
    missing = [
        name
        for name, value in (
            ("company_slug", company_slug),
            ("board_slug", board_slug),
            ("board_url", board_url),
            ("monitor_type", monitor_type),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"row {row_number}: missing {', '.join(missing)}")
    company_name = company_names.get(company_slug)
    if company_name is None:
        raise ValueError(f"row {row_number}: unknown company_slug {company_slug}")
    monitor_config = _config(
        _value(row, "monitor_config"), field="monitor_config", row_number=row_number
    )
    scraper_config = _config(
        _value(row, "scraper_config"), field="scraper_config", row_number=row_number
    )
    candidate = classify_ats_url(board_url, origin="Jobseek board registry URL")
    if candidate is None:
        candidate = _workday_from_config(board_url, monitor_type, monitor_config)
    if candidate is not None and candidate.connector_kind not in _DIRECT_KINDS:
        candidate = None
    policy_held_kind = ""
    if candidate is None and classify_eightfold_board_url(board_url) is not None:
        policy_held_kind = "eightfold"
    if candidate is None and not policy_held_kind:
        return None
    return _BoardLead(
        company_name=company_name,
        company_slug=company_slug,
        board_slug=board_slug,
        board_url=board_url,
        monitor_type=monitor_type,
        monitor_config=monitor_config,
        scraper_type=_value(row, "scraper_type").casefold(),
        scraper_config=scraper_config,
        candidate=candidate,
        policy_held_kind=policy_held_kind,
    )


def _company_names(rows: list[dict[str, str | None]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row_number, row in enumerate(rows, start=2):
        slug = _value(row, "slug")
        name = _value(row, "name")
        if not slug or not name:
            raise ValueError(f"company row {row_number}: missing slug or name")
        if slug in names:
            raise ValueError(f"company row {row_number}: duplicate slug {slug}")
        names[slug] = name
    return names


def _timestamp(value: str, *, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.isoformat()


def _checksum(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def import_jobseek_board_registry(
    repository: JobRepository,
    *,
    boards_csv: str | Path,
    companies_csv: str | Path,
    source_revision: str,
    retrieved_at: str,
    actor: str,
    permission_basis: str,
) -> JobseekBoardImportReport:
    """Retain canonical Jobseek board URLs as third-party, non-activatable leads.

    ``source_revision`` must identify the exact upstream git object used.  A
    caller cannot provide a moving branch name, and unsupported board formats
    are skipped (and counted) instead of being turned into generic scrapers.
    """

    if not actor.strip():
        raise ValueError("actor is required")
    if not permission_basis.strip():
        raise ValueError("permission_basis is required to record authorization context")
    if _REVISION.fullmatch(source_revision) is None:
        raise ValueError("source_revision must be an immutable 40-character git SHA")
    observed_at = _timestamp(retrieved_at, field="retrieved_at")
    board_rows = _read_csv(boards_csv)
    company_names = _company_names(_read_csv(companies_csv))
    parsed = [
        _lead(row, company_names, row_number) for row_number, row in enumerate(board_rows, start=2)
    ]
    leads = [lead for lead in parsed if lead is not None]

    resolved: list[tuple[int, _BoardLead]] = []
    unmatched = 0
    seen: set[tuple[int, str]] = set()
    for lead in leads:
        company = repository.find_company_by_normalized_name(lead.company_name)
        if company is None:
            unmatched += 1
            continue
        company_id = int(company["id"])
        key = (company_id, lead.board_url)
        if key in seen:
            raise ValueError(
                f"duplicate board URL for resolved company {lead.company_name}: {lead.board_url}"
            )
        seen.add(key)
        resolved.append((company_id, lead))

    boards_path = Path(boards_csv)
    companies_path = Path(companies_csv)
    source_base = f"{JOBSEEK_REPOSITORY_URL}/blob/{source_revision}"
    provenance = {
        "dataset": "Jobseek board registry",
        "attribution": "Colophon Group / Jobseek",
        "repository_url": JOBSEEK_REPOSITORY_URL,
        "source_revision": source_revision.casefold(),
        "boards_url": f"{source_base}/{JOBSEEK_BOARDS_PATH}",
        "companies_url": f"{source_base}/{JOBSEEK_COMPANIES_PATH}",
        "license_notice_url": f"{source_base}/{JOBSEEK_LICENSE_NOTICE_PATH}",
        "permission_basis": permission_basis.strip(),
        "boards_checksum_sha256": _checksum(boards_path),
        "companies_checksum_sha256": _checksum(companies_path),
    }
    policy_held = 0
    for company_id, lead in resolved:
        candidate = lead.candidate
        proposed_kind = candidate.connector_kind if candidate is not None else lead.policy_held_kind
        if lead.policy_held_kind:
            policy_held += 1
        family = _FAMILY_BY_KIND.get(proposed_kind, "unknown_external")
        if family not in FINGERPRINT_FAMILIES:
            family = "unknown_external"
        repository.upsert_source_fingerprint(
            company_id,
            observed_url=lead.board_url,
            family=family,
            evidence={
                "review_method": "jobseek_board_registry",
                "verification_status": "unverified",
                "activation_allowed": False,
                "primary_source_verification_required": True,
                "proposed_kind": proposed_kind,
                "policy_held": bool(lead.policy_held_kind),
                "policy_reason": (
                    "No anonymous complete-manifest connector is available for Eightfold"
                    if lead.policy_held_kind
                    else ""
                ),
                "normalized_base_url_lead": candidate.normalized_base_url if candidate else "",
                "board_token_lead": candidate.board_token if candidate else "",
                "classifier_evidence": list(candidate.evidence) if candidate else [],
                "source": provenance,
                "registry_record": {
                    "company_slug": lead.company_slug,
                    "board_slug": lead.board_slug,
                    "board_url": lead.board_url,
                    "monitor_type": lead.monitor_type,
                    "monitor_config": lead.monitor_config,
                    "scraper_type": lead.scraper_type,
                    "scraper_config": lead.scraper_config,
                },
            },
            actor=actor,
            observed_at=observed_at,
            mark_discovered=False,
        )
    return JobseekBoardImportReport(
        rows_read=len(board_rows),
        imported=len(resolved),
        policy_held=policy_held,
        unsupported=len(board_rows) - len(leads),
        unmatched_companies=unmatched,
    )
