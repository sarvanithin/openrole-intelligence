"""Fail-closed parsing for exact, policy-held Paylocity Recruiting URLs.

Paylocity's supported APIs require bearer authentication, registered API access,
and client-specific production authorization. This module records exact public
observations only; it performs no HTTP and derives no browser or API route.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

_HOST = "recruiting.paylocity.com"
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_NUMERIC_ID = re.compile(r"^[0-9]{1,20}$")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,255}$")

PortalFamily = Literal["uuid_board", "legacy_list", "job"]
ObservationKind = Literal["board", "job_detail", "application"]

PAYLOCITY_POLICY_REASON = (
    "policy-held: Paylocity APIs require bearer authentication, registered client/partner "
    "access, and client-specific production authorization; no documented anonymous Recruiting "
    "complete-manifest API was verified"
)


@dataclass(frozen=True, slots=True)
class PaylocityPolicyHeldCandidate:
    """An exact observed Paylocity Recruiting URL that cannot be activated."""

    observed_url: str
    host: str
    portal_family: PortalFamily
    board_path: str
    observation_kind: ObservationKind
    board_identifier: str = ""
    job_identifier: str = ""
    company_slug: str = ""
    job_slug: str = ""
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = PAYLOCITY_POLICY_REASON


@dataclass(frozen=True, slots=True)
class PaylocityPolicyProbe:
    """A local-only policy result that deliberately performs no HTTP."""

    candidate: PaylocityPolicyHeldCandidate
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


def _all_jobs_candidate(
    value: str,
    path: str,
    parts: tuple[str, ...],
) -> PaylocityPolicyHeldCandidate | None:
    if len(parts) not in {4, 5} or _UUID.fullmatch(parts[3]) is None:
        return None
    company_slug = parts[4] if len(parts) == 5 else ""
    if company_slug and _SLUG.fullmatch(company_slug) is None:
        return None
    return PaylocityPolicyHeldCandidate(
        observed_url=value,
        host=_HOST,
        portal_family="uuid_board",
        board_path=path,
        observation_kind="board",
        board_identifier=parts[3],
        company_slug=company_slug,
    )


def _job_candidate(
    value: str,
    path: str,
    parts: tuple[str, ...],
    action: str,
) -> PaylocityPolicyHeldCandidate | None:
    expected_lengths = {4, 6} if action == "details" else {6}
    if len(parts) not in expected_lengths or _NUMERIC_ID.fullmatch(parts[3]) is None:
        return None
    company_slug = parts[4] if len(parts) == 6 else ""
    job_slug = parts[5] if len(parts) == 6 else ""
    if company_slug and (
        _SLUG.fullmatch(company_slug) is None or _SLUG.fullmatch(job_slug) is None
    ):
        return None
    return PaylocityPolicyHeldCandidate(
        observed_url=value,
        host=_HOST,
        portal_family="job",
        board_path=path,
        observation_kind="job_detail" if action == "details" else "application",
        job_identifier=parts[3],
        company_slug=company_slug,
        job_slug=job_slug,
    )


def _legacy_list_candidate(
    value: str,
    path: str,
    parts: tuple[str, ...],
) -> PaylocityPolicyHeldCandidate | None:
    if (
        len(parts) != 5
        or _NUMERIC_ID.fullmatch(parts[3]) is None
        or _SLUG.fullmatch(parts[4]) is None
    ):
        return None
    return PaylocityPolicyHeldCandidate(
        observed_url=value,
        host=_HOST,
        portal_family="legacy_list",
        board_path=path,
        observation_kind="board",
        board_identifier=parts[3],
        company_slug=parts[4],
    )


def classify_paylocity_board_url(url: str) -> PaylocityPolicyHeldCandidate | None:
    """Recognize exact HTTPS Paylocity Recruiting shapes present in inventory."""

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
        or parsed.query
        or host != _HOST
        or "%" in parsed.path
        or "//" in parsed.path
        or parsed.path.endswith("/")
    ):
        return None
    parts = tuple(parsed.path.removeprefix("/").split("/"))
    if len(parts) < 3 or tuple(part.casefold() for part in parts[:2]) != (
        "recruiting",
        "jobs",
    ):
        return None
    action = parts[2].casefold()
    if action == "all":
        return _all_jobs_candidate(value, parsed.path, parts)
    if action in {"apply", "details"}:
        return _job_candidate(value, parsed.path, parts, action)
    if action == "list":
        return _legacy_list_candidate(value, parsed.path, parts)
    return None


def probe_paylocity_policy(url: str) -> PaylocityPolicyProbe:
    """Return the Paylocity policy hold without accessing the network."""

    candidate = classify_paylocity_board_url(url)
    if candidate is None:
        raise ValueError("URL is not an exact supported-shape Paylocity Recruiting observation")
    return PaylocityPolicyProbe(candidate=candidate)
