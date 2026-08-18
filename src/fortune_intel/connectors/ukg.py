"""Fail-closed parsing for exact, policy-held UKG/UltiPro recruiting URLs.

UKG's documented API access uses administrator-issued machine-to-machine
credentials, and UKG's terms prohibit unauthorized automated access. This
module therefore inventories exact recruiting-board observations; it does not
derive endpoints, access the network, or expose a scheduler connector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

_UKG_HOSTS = frozenset(
    {
        "recruiting.ultipro.com",
        "recruiting2.ultipro.com",
        "recruiting.ultipro.ca",
    }
)
_TENANT = r"[A-Za-z0-9]{2,64}"
_UUID = r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
_TENANT_ROOT = re.compile(rf"^/(?P<tenant>{_TENANT})/?$")
_BOARD_ROOT = re.compile(rf"^/(?P<tenant>{_TENANT})/JobBoard/(?P<board_id>{_UUID})/?$")
_OPPORTUNITY_DETAIL = re.compile(
    rf"^/(?P<tenant>{_TENANT})/JobBoard/(?P<board_id>{_UUID})/"
    r"Opportunity/OpportunityDetail/?$"
)
_UUID_VALUE = re.compile(rf"^{_UUID}$")
_BOARD_QUERY_KEYS = frozenset({"q", "o", "w", "wc", "we", "wpst", "f5"})

UKG_POLICY_REASON = (
    "policy-held: UKG's official developer access uses administrator-issued "
    "machine-to-machine credentials, no documented anonymous complete recruiting "
    "manifest API was verified, and UKG terms prohibit automated scraping/access "
    "without approval"
)


@dataclass(frozen=True, slots=True)
class UKGPolicyHeldCandidate:
    """An exact observed UKG recruiting URL that is not authorized for ingestion."""

    observed_url: str
    host: str
    tenant: str
    board_path: str
    board_id: str = ""
    opportunity_id: str = ""
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = UKG_POLICY_REASON


@dataclass(frozen=True, slots=True)
class UKGPolicyProbe:
    """A local-only policy result; it deliberately performs zero HTTP requests."""

    candidate: UKGPolicyHeldCandidate
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


def classify_ukg_board_url(url: str) -> UKGPolicyHeldCandidate | None:
    """Recognize only measured HTTPS UKG/UltiPro recruiting URL shapes."""

    value = url.strip()
    if not value or len(value) > 4096:
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
        or host not in _UKG_HOSTS
        or "%" in parsed.path
        or "\\" in parsed.path
    ):
        return None

    tenant_match = _TENANT_ROOT.fullmatch(parsed.path)
    if tenant_match is not None:
        if parsed.query:
            return None
        return UKGPolicyHeldCandidate(
            observed_url=value,
            host=host,
            tenant=tenant_match.group("tenant"),
            board_path=parsed.path,
        )

    detail_match = _OPPORTUNITY_DETAIL.fullmatch(parsed.path)
    if detail_match is not None:
        query = _unique_query(parsed.query)
        opportunity_id = query.get("opportunityId") if query is not None else None
        if (
            query is None
            or set(query) != {"opportunityId"}
            or opportunity_id is None
            or _UUID_VALUE.fullmatch(opportunity_id) is None
        ):
            return None
        return UKGPolicyHeldCandidate(
            observed_url=value,
            host=host,
            tenant=detail_match.group("tenant"),
            board_path=parsed.path,
            board_id=detail_match.group("board_id"),
            opportunity_id=opportunity_id,
        )

    board_match = _BOARD_ROOT.fullmatch(parsed.path)
    if board_match is None:
        return None
    query = _unique_query(parsed.query)
    if query is None or not set(query).issubset(_BOARD_QUERY_KEYS):
        return None
    return UKGPolicyHeldCandidate(
        observed_url=value,
        host=host,
        tenant=board_match.group("tenant"),
        board_path=parsed.path,
        board_id=board_match.group("board_id"),
    )


def probe_ukg_policy(url: str) -> UKGPolicyProbe:
    """Return the UKG policy hold for an exact URL without network access."""

    candidate = classify_ukg_board_url(url)
    if candidate is None:
        raise ValueError("URL is not an exact supported-shape UKG recruiting observation")
    return UKGPolicyProbe(candidate=candidate)


def _unique_query(query_string: str) -> dict[str, str] | None:
    """Parse a query only when every key occurs exactly once."""

    try:
        pairs = parse_qsl(query_string, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    query: dict[str, str] = {}
    for key, value in pairs:
        if key in query:
            return None
        query[key] = value
    return query
