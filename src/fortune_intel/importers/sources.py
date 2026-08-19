"""Bulk import for reviewed deterministic ATS source registrations."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fortune_intel.connectors.adp_workforce_now import (
    parse_adp_workforce_now_source_key,
)
from fortune_intel.connectors.official_structured import parse_official_structured_source_key
from fortune_intel.connectors.oracle_recruiting import parse_oracle_recruiting_source_key
from fortune_intel.connectors.ukg_recruiting_public import (
    parse_ukg_recruiting_public_source_key,
)
from fortune_intel.connectors.workday import parse_workday_source_key
from fortune_intel.storage import JobRepository

_HOSTS = {
    "adp_workforce_now": set(),
    "greenhouse": {"boards.greenhouse.io", "job-boards.greenhouse.io"},
    "lever": {"jobs.lever.co", "jobs.eu.lever.co"},
    "oracle_recruiting": set(),
    "official_structured": set(),
    "ashby": {"jobs.ashbyhq.com"},
    "smartrecruiters": {"jobs.smartrecruiters.com"},
    "ukg_recruiting_public": set(),
    "workday": set(),
}


@dataclass(frozen=True, slots=True)
class SourceRegistration:
    company_name: str
    kind: str
    board_token: str
    base_url: str
    terms_url: str
    policy_approved_at: str
    owner_contact: str
    sync_interval_minutes: int = 60
    enabled: bool = True


def _value(row: dict[str, str], name: str) -> str:
    lowered = {key.strip().casefold(): (value or "").strip() for key, value in row.items()}
    return lowered.get(name.casefold(), "")


def _registration(row: dict[str, str], row_number: int) -> SourceRegistration:
    required = {
        name: _value(row, name)
        for name in (
            "company_name",
            "kind",
            "board_token",
            "base_url",
            "terms_url",
            "policy_approved_at",
            "owner_contact",
        )
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"row {row_number}: missing {', '.join(missing)}")
    kind = required["kind"].casefold()
    if kind not in _HOSTS:
        raise ValueError(f"row {row_number}: unsupported connector kind {kind}")
    parsed = urlsplit(required["base_url"])
    try:
        port = parsed.port
    except ValueError:
        port = -1
    approved_host = (
        (parsed.hostname or "").casefold() in _HOSTS[kind]
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and not parsed.query
        and not parsed.fragment
    )
    if kind == "workday":
        try:
            workday = parse_workday_source_key(required["board_token"])
        except ValueError as error:
            raise ValueError(f"row {row_number}: invalid Workday board_token") from error
        approved_host = (
            (parsed.hostname or "").casefold().rstrip(".") == workday.host
            and parsed.path.rstrip("/") == urlsplit(workday.public_base_url).path
            and parsed.username is None
            and parsed.password is None
            and port in {None, 443}
            and (not workday.uses_recruiting_path or port is None)
            and not parsed.query
            and not parsed.fragment
            and (
                not workday.uses_recruiting_path
                or ("?" not in required["base_url"] and "#" not in required["base_url"])
            )
        )
    if kind == "oracle_recruiting":
        try:
            oracle = parse_oracle_recruiting_source_key(required["board_token"])
        except ValueError as error:
            raise ValueError(f"row {row_number}: invalid Oracle Recruiting board_token") from error
        approved_host = (
            (parsed.hostname or "").casefold().rstrip(".") == oracle.host
            and parsed.path.rstrip("/") == urlsplit(oracle.public_base_url).path
            and parsed.username is None
            and parsed.password is None
            and port in {None, 443}
            and not parsed.query
            and not parsed.fragment
        )
    if kind == "official_structured":
        try:
            structured = parse_official_structured_source_key(required["board_token"])
        except ValueError as error:
            raise ValueError(
                f"row {row_number}: invalid official structured board_token"
            ) from error
        approved_host = required["base_url"] == structured.public_base_url
    if kind == "adp_workforce_now":
        try:
            adp = parse_adp_workforce_now_source_key(required["board_token"])
        except ValueError as error:
            raise ValueError(f"row {row_number}: invalid ADP Workforce Now board_token") from error
        approved_host = (
            required["base_url"] == adp.public_base_url
            and parsed.username is None
            and parsed.password is None
            and port is None
            and not parsed.fragment
        )
    if kind == "ukg_recruiting_public":
        try:
            ukg = parse_ukg_recruiting_public_source_key(required["board_token"])
        except ValueError as error:
            raise ValueError(f"row {row_number}: invalid UKG Recruiting board_token") from error
        approved_host = (
            required["base_url"] == ukg.public_base_url
            and parsed.username is None
            and parsed.password is None
            and port is None
            and not parsed.query
            and not parsed.fragment
            and "?" not in required["base_url"]
            and "#" not in required["base_url"]
        )
    if parsed.scheme != "https" or not approved_host:
        raise ValueError(f"row {row_number}: base_url is not an approved {kind} public host")
    terms = urlsplit(required["terms_url"])
    if terms.scheme != "https" or not terms.hostname:
        raise ValueError(f"row {row_number}: terms_url must be an absolute HTTPS URL")
    try:
        approved = datetime.fromisoformat(required["policy_approved_at"])
    except ValueError as error:
        raise ValueError(f"row {row_number}: invalid policy_approved_at") from error
    if approved.tzinfo is None:
        raise ValueError(f"row {row_number}: policy_approved_at must include a timezone")
    interval_text = _value(row, "sync_interval_minutes") or "60"
    try:
        interval = int(interval_text)
    except ValueError as error:
        raise ValueError(f"row {row_number}: invalid sync_interval_minutes") from error
    enabled_text = (_value(row, "enabled") or "true").casefold()
    if enabled_text not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"row {row_number}: enabled must be true or false")
    return SourceRegistration(
        company_name=required["company_name"],
        kind=kind,
        board_token=required["board_token"],
        base_url=required["base_url"],
        terms_url=required["terms_url"],
        policy_approved_at=approved.isoformat(),
        owner_contact=required["owner_contact"],
        sync_interval_minutes=interval,
        enabled=enabled_text in {"true", "1", "yes"},
    )


def import_source_registry(repository: JobRepository, csv_path: str | Path) -> int:
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        registrations = [
            _registration(row, row_number)
            for row_number, row in enumerate(csv.DictReader(handle), start=2)
        ]
    resolved = []
    for registration in registrations:
        company = repository.find_company_by_normalized_name(registration.company_name)
        if company is None:
            raise ValueError(
                f"company not found or ambiguous: {registration.company_name}; import the universe first"
            )
        resolved.append((int(company["id"]), registration))
    for company_id, registration in resolved:
        repository.upsert_career_source(
            company_id,
            kind=registration.kind,
            board_token=registration.board_token,
            base_url=registration.base_url,
            sync_interval_minutes=registration.sync_interval_minutes,
            enabled=registration.enabled,
            terms_url=registration.terms_url,
            policy_approved_at=registration.policy_approved_at,
            owner_contact=registration.owner_contact,
        )
    return len(resolved)
