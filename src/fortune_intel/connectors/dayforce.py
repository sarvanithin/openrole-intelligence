"""Fail-closed parsing for exact, policy-held Dayforce career URLs.

Dayforce's API agreement requires explicit, verifiable consent from each client
whose data is accessed, while detailed API documentation is restricted to its
developer network. This module inventories exact public URL observations only;
it performs no HTTP and derives no API or tenant endpoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

_JOBS_HOST = "jobs.dayforcehcm.com"
_LEGACY_HOSTS = frozenset(
    {
        "dayforcehcm.com",
        "www.dayforcehcm.com",
        "us231.dayforcehcm.com",
        "us232.dayforcehcm.com",
        "us241.dayforcehcm.com",
        "us242.dayforcehcm.com",
        "usr58.dayforcehcm.com",
    }
)
_ALLOWED_HOSTS = _LEGACY_HOSTS | {_JOBS_HOST}
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_JOB_ID = re.compile(r"^[0-9]{1,20}$")

PortalFamily = Literal["jobs_portal", "candidate_portal"]
ObservationKind = Literal["board", "job_detail"]

DAYFORCE_POLICY_REASON = (
    "policy-held: Dayforce's official API agreement requires explicit, verifiable client "
    "consent, detailed Job Postings API documentation is restricted to developer-network "
    "members, and no authorized anonymous complete-manifest contract was verified"
)


@dataclass(frozen=True, slots=True)
class DayforcePolicyHeldCandidate:
    """An exact observed Dayforce career URL that cannot be activated."""

    observed_url: str
    host: str
    portal_family: PortalFamily
    tenant_identifier: str
    portal_identifier: str
    board_path: str
    observation_kind: ObservationKind = "board"
    job_identifier: str = ""
    locale: str = "en-US"
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = DAYFORCE_POLICY_REASON


@dataclass(frozen=True, slots=True)
class DayforcePolicyProbe:
    """A local-only result that makes the absence of validation explicit."""

    candidate: DayforcePolicyHeldCandidate
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


def _jobs_candidate(
    value: str,
    path: str,
    segments: tuple[str, ...],
) -> DayforcePolicyHeldCandidate | None:
    if segments and segments[0] == "en-US":
        parts = segments[1:]
    else:
        parts = segments
    if len(parts) not in {2, 4}:
        return None
    tenant, portal = parts[:2]
    if _TOKEN.fullmatch(tenant) is None or _TOKEN.fullmatch(portal) is None:
        return None
    observation_kind: ObservationKind = "board"
    job_identifier = ""
    if len(parts) == 4:
        if parts[2].casefold() != "jobs" or _JOB_ID.fullmatch(parts[3]) is None:
            return None
        observation_kind = "job_detail"
        job_identifier = parts[3]
    return DayforcePolicyHeldCandidate(
        observed_url=value,
        host=_JOBS_HOST,
        portal_family="jobs_portal",
        tenant_identifier=tenant,
        portal_identifier=portal,
        board_path=path,
        observation_kind=observation_kind,
        job_identifier=job_identifier,
    )


def _legacy_candidate(
    value: str,
    host: str,
    path: str,
    segments: tuple[str, ...],
) -> DayforcePolicyHeldCandidate | None:
    if len(segments) not in {3, 5} or segments[:2] != ("CandidatePortal", "en-US"):
        return None
    tenant = segments[2]
    if _TOKEN.fullmatch(tenant) is None:
        return None
    portal = ""
    if len(segments) == 5:
        if segments[3].casefold() != "site" or _TOKEN.fullmatch(segments[4]) is None:
            return None
        portal = segments[4]
    return DayforcePolicyHeldCandidate(
        observed_url=value,
        host=host,
        portal_family="candidate_portal",
        tenant_identifier=tenant,
        portal_identifier=portal,
        board_path=path,
    )


def classify_dayforce_board_url(url: str) -> DayforcePolicyHeldCandidate | None:
    """Recognize only exact HTTPS Dayforce shapes present in the inventory."""

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
        or not parsed.path.startswith("/")
        or "//" in parsed.path
    ):
        return None
    path_without_trailing_slash = parsed.path[:-1] if parsed.path.endswith("/") else parsed.path
    segments = tuple(path_without_trailing_slash.removeprefix("/").split("/"))
    if host == _JOBS_HOST:
        if parsed.path.endswith("/"):
            return None
        return _jobs_candidate(value, parsed.path, segments)
    return _legacy_candidate(value, host, parsed.path, segments)


def probe_dayforce_policy(url: str) -> DayforcePolicyProbe:
    """Return the Dayforce policy hold without performing a network request."""

    candidate = classify_dayforce_board_url(url)
    if candidate is None:
        raise ValueError("URL is not an exact supported-shape Dayforce public board observation")
    return DayforcePolicyProbe(candidate=candidate)
