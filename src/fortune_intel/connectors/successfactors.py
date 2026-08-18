"""Fail-closed parsing for exact, policy-held SAP SuccessFactors career URLs.

SAP's supported Recruiting OData APIs require registered OAuth credentials and
Recruiting permissions. This module records exact public career observations;
it does not derive API hosts, query browser internals, or expose a scheduler
connector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlsplit

_CAREER_HOSTS = frozenset(
    {
        "career2.successfactors.eu",
        "career4.successfactors.com",
        "career5.successfactors.eu",
        "career8.successfactors.com",
        "career41.sapsf.com",
    }
)
_PERFORMANCE_HOSTS = frozenset(
    {
        "performancemanager.successfactors.eu",
        "performancemanager4.successfactors.com",
    }
)
_ALLOWED_HOSTS = _CAREER_HOSTS | _PERFORMANCE_HOSTS
_COMPANY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_JOB_ID = re.compile(r"^[0-9]{1,20}$")
_SIGNED_TOKEN = re.compile(r"^[A-Fa-f0-9]{16,128}$")
_LOCALE = re.compile(r"^[a-z]{2}_[A-Z]{2}$")

PortalFamily = Literal["career", "performance_manager", "private_job"]
ObservationKind = Literal["board", "job_detail"]

SUCCESSFACTORS_POLICY_REASON = (
    "policy-held: SAP SuccessFactors Recruiting OData requires registered OAuth credentials "
    "and Recruiting export/field permissions; no documented anonymous complete-manifest API "
    "or supported mapping from the observed career URL was verified"
)


@dataclass(frozen=True, slots=True)
class SuccessFactorsPolicyHeldCandidate:
    """An exact observed SuccessFactors career URL that cannot be activated."""

    observed_url: str
    host: str
    portal_family: PortalFamily
    company_identifier: str
    board_path: str
    observation_kind: ObservationKind = "board"
    job_identifier: str = ""
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = SUCCESSFACTORS_POLICY_REASON


@dataclass(frozen=True, slots=True)
class SuccessFactorsPolicyProbe:
    """A local-only policy result that deliberately performs no HTTP."""

    candidate: SuccessFactorsPolicyHeldCandidate
    network_requests: int = 0
    endpoint_validated: bool = False
    complete_manifest_validated: bool = False
    pagination_validated: bool = False
    posting_dates_validated: bool = False
    locations_validated: bool = False
    us_filter_compatible: bool = False
    bounded_http_validated: bool = False
    activation_allowed: bool = False
    outcome: str = "policy_held"


def _consistent_query_value(
    query: dict[str, list[str]],
    name: str,
    pattern: re.Pattern[str],
) -> str | None:
    values = query.get(name, [])
    if not values or len(set(values)) != 1 or pattern.fullmatch(values[0]) is None:
        return None
    return values[0]


def _career_candidate(
    value: str,
    host: str,
    path: str,
    query: dict[str, list[str]],
) -> SuccessFactorsPolicyHeldCandidate | None:
    if path not in {"/career", "/careers"}:
        return None
    company = _consistent_query_value(query, "company", _COMPANY)
    if company is None:
        return None
    career_company = query.get("career_company")
    if career_company is not None and (
        len(set(career_company)) != 1
        or _COMPANY.fullmatch(career_company[0]) is None
        or career_company[0] != company
    ):
        return None
    if "lang" in query and _consistent_query_value(query, "lang", _LOCALE) is None:
        return None
    return SuccessFactorsPolicyHeldCandidate(
        observed_url=value,
        host=host,
        portal_family="career",
        company_identifier=company,
        board_path=path,
    )


def _private_job_candidate(
    value: str,
    host: str,
    path: str,
    query: dict[str, list[str]],
) -> SuccessFactorsPolicyHeldCandidate | None:
    if path != "/sfcareer/jobreqcareerpvt":
        return None
    company = _consistent_query_value(query, "company", _COMPANY)
    job_identifier = _consistent_query_value(query, "jobId", _JOB_ID)
    signed_token = _consistent_query_value(query, "st", _SIGNED_TOKEN)
    if company is None or job_identifier is None or signed_token is None:
        return None
    return SuccessFactorsPolicyHeldCandidate(
        observed_url=value,
        host=host,
        portal_family="private_job",
        company_identifier=company,
        board_path=path,
        observation_kind="job_detail",
        job_identifier=job_identifier,
    )


def _performance_candidate(
    value: str,
    host: str,
    path: str,
    query: dict[str, list[str]],
) -> SuccessFactorsPolicyHeldCandidate | None:
    if path == "/sf/careers/jobsearch":
        company = _consistent_query_value(query, "bplte_company", _COMPANY)
    elif path == "/sf/careers":
        company = _consistent_query_value(query, "company", _COMPANY)
    else:
        return None
    if company is None:
        return None
    return SuccessFactorsPolicyHeldCandidate(
        observed_url=value,
        host=host,
        portal_family="performance_manager",
        company_identifier=company,
        board_path=path,
    )


def classify_successfactors_board_url(url: str) -> SuccessFactorsPolicyHeldCandidate | None:
    """Recognize exact HTTPS SuccessFactors career shapes present in inventory."""

    value = url.strip()
    if (
        not value
        or len(value) > 4096
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return None
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or host not in _ALLOWED_HOSTS
        or "%" in parsed.path
        or "//" in parsed.path
    ):
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    if host in _PERFORMANCE_HOSTS:
        return _performance_candidate(value, host, parsed.path, query)
    private_candidate = _private_job_candidate(value, host, parsed.path, query)
    if private_candidate is not None:
        return private_candidate
    return _career_candidate(value, host, parsed.path, query)


def probe_successfactors_policy(url: str) -> SuccessFactorsPolicyProbe:
    """Return the SuccessFactors policy hold without accessing the network."""

    candidate = classify_successfactors_board_url(url)
    if candidate is None:
        raise ValueError(
            "URL is not an exact supported-shape SAP SuccessFactors career observation"
        )
    return SuccessFactorsPolicyProbe(candidate=candidate)
