"""Fail-closed parsing for exact, policy-held Rippling ATS URLs.

Rippling's supported APIs require a company-bound API key or OAuth access token.
This module records exact public ATS observations only; it performs no HTTP,
derives no board from a job URL, and exposes no scheduler connector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlsplit

_HOST = "ats.rippling.com"
_OBSERVED_LOCALES = frozenset({"en-AU"})
_COMPANY_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

PortalFamily = Literal["company_board", "localized_job"]
ObservationKind = Literal["board", "job_detail", "application"]

RIPPLING_POLICY_REASON = (
    "policy-held: Rippling APIs require a company-bound API key or OAuth token and "
    "administrator-authorized scopes; no documented anonymous ATS complete-manifest API "
    "mapped to the observed public URL was verified"
)


@dataclass(frozen=True, slots=True)
class RipplingPolicyHeldCandidate:
    """An exact observed Rippling ATS URL that cannot be activated."""

    observed_url: str
    host: str
    portal_family: PortalFamily
    company_slug: str
    board_path: str
    observation_kind: ObservationKind
    job_identifier: str = ""
    locale: str = ""
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = RIPPLING_POLICY_REASON


@dataclass(frozen=True, slots=True)
class RipplingPolicyProbe:
    """A local-only policy result that deliberately performs no HTTP."""

    candidate: RipplingPolicyHeldCandidate
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


def _company_candidate(
    value: str,
    path: str,
    parts: tuple[str, ...],
) -> RipplingPolicyHeldCandidate | None:
    if _COMPANY_SLUG.fullmatch(parts[0]) is None or parts[1] != "jobs":
        return None
    if len(parts) == 2:
        return RipplingPolicyHeldCandidate(
            observed_url=value,
            host=_HOST,
            portal_family="company_board",
            company_slug=parts[0],
            board_path=path,
            observation_kind="board",
        )
    if len(parts) != 4 or _UUID.fullmatch(parts[2]) is None or parts[3] != "apply":
        return None
    return RipplingPolicyHeldCandidate(
        observed_url=value,
        host=_HOST,
        portal_family="company_board",
        company_slug=parts[0],
        board_path=path,
        observation_kind="application",
        job_identifier=parts[2],
    )


def _localized_job_candidate(
    value: str,
    path: str,
    parts: tuple[str, ...],
    query: dict[str, list[str]],
) -> RipplingPolicyHeldCandidate | None:
    source_tokens = query.get("st", [])
    if (
        len(parts) != 4
        or parts[0] not in _OBSERVED_LOCALES
        or _COMPANY_SLUG.fullmatch(parts[1]) is None
        or parts[2] != "jobs"
        or _UUID.fullmatch(parts[3]) is None
        or set(query) != {"st"}
        or len(source_tokens) != 1
        or _UUID.fullmatch(source_tokens[0]) is None
    ):
        return None
    return RipplingPolicyHeldCandidate(
        observed_url=value,
        host=_HOST,
        portal_family="localized_job",
        company_slug=parts[1],
        board_path=path,
        observation_kind="job_detail",
        job_identifier=parts[3],
        locale=parts[0],
    )


def classify_rippling_board_url(url: str) -> RipplingPolicyHeldCandidate | None:
    """Recognize exact HTTPS Rippling ATS shapes present in inventory."""

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
        or host != _HOST
        or "%" in parsed.path
        or "//" in parsed.path
        or parsed.path.endswith("/")
    ):
        return None
    parts = tuple(parsed.path.removeprefix("/").split("/"))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if len(parts) in {2, 4} and parts[0] not in _OBSERVED_LOCALES:
        if query:
            return None
        return _company_candidate(value, parsed.path, parts)
    return _localized_job_candidate(value, parsed.path, parts, query)


def probe_rippling_policy(url: str) -> RipplingPolicyProbe:
    """Return the Rippling policy hold without accessing the network."""

    candidate = classify_rippling_board_url(url)
    if candidate is None:
        raise ValueError("URL is not an exact supported-shape Rippling ATS observation")
    return RipplingPolicyProbe(candidate=candidate)
