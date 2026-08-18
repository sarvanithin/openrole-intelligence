"""Fail-closed parsing for exact, policy-held Paycom career URLs.

Paycom prohibits automated extraction and scraping without written
authorization. Its documented Data Services use Paycom-owned access keys for
client-scoped data. This module therefore performs local inventory
classification only and never derives endpoints or accesses the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

_HOST = "www.paycomonline.net"
_LEGACY_BOARD_PATH = "/v4/ats/web.php/jobs"
_LEGACY_DETAIL_PATH = "/v4/ats/web.php/jobs/ViewJobDetails"
_PORTAL_BOARD = re.compile(r"^/v4/ats/web\.php/portal/(?P<client_key>[0-9A-Fa-f]{32})/career-page$")
_PORTAL_DETAIL = re.compile(
    r"^/v4/ats/web\.php/portal/(?P<client_key>[0-9A-Fa-f]{32})/"
    r"jobs/(?P<job_id>[0-9]{1,20})$"
)
_CLIENT_KEY = re.compile(r"^[0-9A-Fa-f]{32}$")
_NONCE = re.compile(r"^[0-9A-Fa-f]{32}$")
_JOB_ID = re.compile(r"^[0-9]{1,20}$")
_MALFORMED_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")

PortalFamily = Literal["legacy_query", "portal"]
PageKind = Literal["board", "job_detail"]

PAYCOM_POLICY_REASON = (
    "policy-held: Paycom's official terms prohibit automated extraction or scraping without "
    "written authorization, documented Data Services require Paycom-owned client access keys, "
    "and no anonymous complete recruiting manifest was verified"
)


@dataclass(frozen=True, slots=True)
class PaycomPolicyHeldCandidate:
    """An exact observed Paycom career URL that is not authorized for ingestion."""

    observed_url: str
    host: str
    client_key: str
    board_path: str
    portal_family: PortalFamily
    page_kind: PageKind
    job_id: str = ""
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = PAYCOM_POLICY_REASON


@dataclass(frozen=True, slots=True)
class PaycomPolicyProbe:
    """A local-only policy result; it deliberately performs zero HTTP requests."""

    candidate: PaycomPolicyHeldCandidate
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


def classify_paycom_board_url(url: str) -> PaycomPolicyHeldCandidate | None:
    """Recognize only measured HTTPS Paycom board and job-detail URL shapes."""

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
    ):
        return None

    if parsed.path in {_LEGACY_BOARD_PATH, _LEGACY_DETAIL_PATH}:
        return _legacy_candidate(value, host, parsed.path, parsed.query)

    if parsed.query:
        return None
    board = _PORTAL_BOARD.fullmatch(parsed.path)
    if board is not None:
        return PaycomPolicyHeldCandidate(
            observed_url=value,
            host=host,
            client_key=board.group("client_key"),
            board_path=parsed.path,
            portal_family="portal",
            page_kind="board",
        )
    detail = _PORTAL_DETAIL.fullmatch(parsed.path)
    if detail is None:
        return None
    return PaycomPolicyHeldCandidate(
        observed_url=value,
        host=host,
        client_key=detail.group("client_key"),
        board_path=parsed.path,
        portal_family="portal",
        page_kind="job_detail",
        job_id=detail.group("job_id"),
    )


def probe_paycom_policy(url: str) -> PaycomPolicyProbe:
    """Return the Paycom policy hold for an exact URL without network access."""

    candidate = classify_paycom_board_url(url)
    if candidate is None:
        raise ValueError("URL is not an exact supported-shape Paycom public board observation")
    return PaycomPolicyProbe(candidate=candidate)


def _legacy_candidate(
    value: str,
    host: str,
    path: str,
    query_string: str,
) -> PaycomPolicyHeldCandidate | None:
    query = _unique_query(query_string)
    if query is None or _CLIENT_KEY.fullmatch(query.get("clientkey", "")) is None:
        return None
    if path == _LEGACY_DETAIL_PATH:
        if set(query) != {"clientkey", "job"} or _JOB_ID.fullmatch(query["job"]) is None:
            return None
        return PaycomPolicyHeldCandidate(
            observed_url=value,
            host=host,
            client_key=query["clientkey"],
            board_path=path,
            portal_family="legacy_query",
            page_kind="job_detail",
            job_id=query["job"],
        )

    if not set(query).issubset({"clientkey", "fromClientSide", "session_nonce"}):
        return None
    if "fromClientSide" in query and query["fromClientSide"] != "true":
        return None
    if "session_nonce" in query and _NONCE.fullmatch(query["session_nonce"]) is None:
        return None
    if "fromClientSide" in query and "session_nonce" in query:
        return None
    return PaycomPolicyHeldCandidate(
        observed_url=value,
        host=host,
        client_key=query["clientkey"],
        board_path=path,
        portal_family="legacy_query",
        page_kind="board",
    )


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
