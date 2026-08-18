"""Fail-closed parsing for exact, policy-held Paycor Recruiting URLs.

Paycor documents ATS APIs, but access requires a subscription key, OAuth token,
and client-admin activation. Paycor also prohibits unapproved automated access.
This module therefore classifies exact career-board observations locally and
never derives endpoints, accesses the network, or exposes a scheduler connector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

_HOST = "recruitingbypaycor.com"
_BOARD_PATH = "/career/CareerHome.action"
_CLIENT_ID = re.compile(r"^[0-9A-Fa-f]{32}$")
_MALFORMED_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")

PAYCOR_POLICY_REASON = (
    "policy-held: Paycor's official ATS APIs require an APIM subscription key, OAuth access "
    "token, and Paycor client-admin activation; Paycor terms prohibit unapproved automated "
    "scraping; no anonymous complete recruiting manifest was verified"
)


@dataclass(frozen=True, slots=True)
class PaycorPolicyHeldCandidate:
    """An exact Paycor Recruiting board URL that is not authorized for ingestion."""

    observed_url: str
    host: str
    client_id: str
    board_path: str
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = PAYCOR_POLICY_REASON


@dataclass(frozen=True, slots=True)
class PaycorPolicyProbe:
    """A local-only policy result; it deliberately performs zero HTTP requests."""

    candidate: PaycorPolicyHeldCandidate
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


def classify_paycor_board_url(url: str) -> PaycorPolicyHeldCandidate | None:
    """Recognize only the exact measured Paycor Recruiting board URL shape."""

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
        or parsed.path != _BOARD_PATH
        or "%" in parsed.path
    ):
        return None
    query = _unique_query(parsed.query)
    if query is None or set(query) != {"clientId"}:
        return None
    client_id = query["clientId"]
    if _CLIENT_ID.fullmatch(client_id) is None:
        return None
    return PaycorPolicyHeldCandidate(
        observed_url=value,
        host=host,
        client_id=client_id,
        board_path=parsed.path,
    )


def probe_paycor_policy(url: str) -> PaycorPolicyProbe:
    """Return the Paycor policy hold for an exact URL without network access."""

    candidate = classify_paycor_board_url(url)
    if candidate is None:
        raise ValueError("URL is not an exact supported-shape Paycor Recruiting observation")
    return PaycorPolicyProbe(candidate=candidate)


def _unique_query(query_string: str) -> dict[str, str] | None:
    if (
        not query_string
        or _MALFORMED_ESCAPE.search(query_string)
        or query_string.startswith("&")
        or query_string.endswith("&")
        or "&&" in query_string
    ):
        return None
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
