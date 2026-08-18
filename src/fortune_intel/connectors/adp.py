"""Fail-closed parsing for exact, policy-held ADP career-board URLs.

ADP's documented Job Requisitions APIs require application scope and an
authorized Practitioner/system user. This module therefore inventories exact
public board observations only. It never derives an API URL, requests an
undocumented browser endpoint, or exposes a scheduler connector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlsplit

_WORKFORCE_HOST = "workforcenow.adp.com"
_RECRUITING_HOST = "recruiting.adp.com"
_MYJOBS_HOST = "myjobs.adp.com"
_ALLOWED_HOSTS = frozenset({_WORKFORCE_HOST, _RECRUITING_HOST, _MYJOBS_HOST})

_WORKFORCE_PATH = "/mascsr/default/mdf/recruitment/recruitment.html"
_WORKFORCE_POSTING_PATH = "/jobs/apply/posting.html"
_RECRUITING_PATH = "/srccar/public/RTI.home"
_MYJOBS_PATH_SUFFIXES = frozenset({(), ("cx",), ("cx", "job-listing")})

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_NUMERIC_CLIENT = re.compile(r"^[0-9]{1,20}$")
_RESERVED_MYJOBS_SLUGS = frozenset(
    {"api", "app", "apply", "auth", "candidate", "login", "oauth", "static", "www"}
)

PortalFamily = Literal["workforce_now", "recruiting_management", "myjobs"]

ADP_POLICY_REASON = (
    "policy-held: ADP's official Job Requisitions API requires Consumer Application "
    "Registry scope and an authorized Practitioner/system user; no documented anonymous "
    "complete-manifest API or supported mapping from the observed public portal URL was verified"
)


@dataclass(frozen=True, slots=True)
class ADPPolicyHeldCandidate:
    """An exact observed ADP board URL that is not authorized for ingestion."""

    observed_url: str
    host: str
    portal_family: PortalFamily
    tenant_identifier: str
    board_path: str
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = ADP_POLICY_REASON


@dataclass(frozen=True, slots=True)
class ADPPolicyProbe:
    """A local-only probe result; it deliberately performs zero HTTP requests."""

    candidate: ADPPolicyHeldCandidate
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


def _single_query_value(
    query: dict[str, list[str]],
    name: str,
    pattern: re.Pattern[str],
) -> str | None:
    values = query.get(name, [])
    if len(values) != 1 or pattern.fullmatch(values[0]) is None:
        return None
    return values[0]


def _workforce_candidate(
    value: str,
    host: str,
    path: str,
    query: dict[str, list[str]],
) -> ADPPolicyHeldCandidate | None:
    if path == _WORKFORCE_PATH:
        tenant = _single_query_value(query, "cid", _UUID)
        cost_center = _single_query_value(query, "ccId", _SAFE_TOKEN)
        if tenant is None or cost_center is None or any(key in query for key in ("client", "c")):
            return None
    elif path == _WORKFORCE_POSTING_PATH:
        tenant = _single_query_value(query, "client", _SAFE_TOKEN)
        cost_center = _single_query_value(query, "ccId", _SAFE_TOKEN)
        if tenant is None or cost_center is None or any(key in query for key in ("cid", "c")):
            return None
    else:
        return None
    return ADPPolicyHeldCandidate(
        observed_url=value,
        host=host,
        portal_family="workforce_now",
        tenant_identifier=tenant,
        board_path=path,
    )


def _recruiting_candidate(
    value: str,
    host: str,
    path: str,
    query: dict[str, list[str]],
) -> ADPPolicyHeldCandidate | None:
    if path != _RECRUITING_PATH or any(key in query for key in ("cid", "client", "ccId")):
        return None
    tenant = _single_query_value(query, "c", _NUMERIC_CLIENT)
    destination = _single_query_value(query, "d", _SAFE_TOKEN)
    if tenant is None or destination is None:
        return None
    return ADPPolicyHeldCandidate(
        observed_url=value,
        host=host,
        portal_family="recruiting_management",
        tenant_identifier=tenant,
        board_path=path,
    )


def _myjobs_candidate(
    value: str,
    host: str,
    path: str,
    query: dict[str, list[str]],
) -> ADPPolicyHeldCandidate | None:
    if not path.startswith("/") or path.endswith("/") or "//" in path:
        return None
    segments = tuple(path.removeprefix("/").split("/"))
    if (
        len(segments) < 1
        or tuple(segment.casefold() for segment in segments[1:]) not in _MYJOBS_PATH_SUFFIXES
    ):
        return None
    tenant = segments[0]
    if (
        _SAFE_TOKEN.fullmatch(tenant) is None
        or tenant.casefold() in _RESERVED_MYJOBS_SLUGS
        or "cid" in query
        or "client" in query
        or "ccId" in query
    ):
        return None
    if "c" in query and _single_query_value(query, "c", _NUMERIC_CLIENT) is None:
        return None
    if "d" in query and _single_query_value(query, "d", _SAFE_TOKEN) is None:
        return None
    return ADPPolicyHeldCandidate(
        observed_url=value,
        host=host,
        portal_family="myjobs",
        tenant_identifier=tenant,
        board_path=path,
    )


def classify_adp_board_url(url: str) -> ADPPolicyHeldCandidate | None:
    """Recognize only exact HTTPS shapes observed for ADP public job boards."""

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
    ):
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    if host == _WORKFORCE_HOST:
        return _workforce_candidate(value, host, parsed.path, query)
    if host == _RECRUITING_HOST:
        return _recruiting_candidate(value, host, parsed.path, query)
    return _myjobs_candidate(value, host, parsed.path, query)


def probe_adp_policy(url: str) -> ADPPolicyProbe:
    """Return the ADP policy hold for an exact URL without accessing the network."""

    candidate = classify_adp_board_url(url)
    if candidate is None:
        raise ValueError("URL is not an exact supported-shape ADP public board observation")
    return ADPPolicyProbe(candidate=candidate)
