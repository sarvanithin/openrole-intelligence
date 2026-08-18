"""Fail-closed parsing for exact, policy-held Oracle Taleo career URLs.

Oracle documents candidate-facing Career Section URL shapes, but supported
Taleo APIs require tenant credentials and tenant-specific service definitions.
Oracle's terms also prohibit automated access without express written
permission. This module therefore performs local inventory classification only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

_ENTERPRISE_HOST = re.compile(r"^(?P<zone>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.taleo\.net$")
_BUSINESS_HOST = re.compile(r"^(?P<zone>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.tbe\.taleo\.net$")
_RESERVED_ENTERPRISE_ZONES = frozenset({"api", "client", "tbe", "www"})
_SECTION = r"[A-Za-z0-9][A-Za-z0-9_+.-]{0,127}"
_ENTERPRISE_PAGE = re.compile(
    rf"^/careersection/(?P<section>{_SECTION})/(?P<page>jobsearch|jobdetail)\.ftl$"
)
_TBE_PAGE = re.compile(
    r"^/(?P<shard>[A-Za-z0-9]{3,32})/ats/careers/v2/"
    r"(?P<page>jobSearch|searchResults)$"
)
_TBE_DISPATCHER = "/dispatcher/servlet/DispatcherServlet"
_MALFORMED_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_ORG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_DIGITS = re.compile(r"^[0-9]{1,20}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2}(?:[_-][A-Za-z]{2})?$")
_TIMEZONE = re.compile(r"^[A-Za-z0-9_:+./-]{1,128}$")
_SAFE_SOURCE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_ENTERPRISE_SEARCH_KEYS = frozenset(
    {
        "f",
        "ftlcompclass",
        "ignoreSavedQuery",
        "jobfield",
        "lang",
        "location",
        "portal",
        "radius",
        "radiusType",
        "searchExpanded",
    }
)
_ENTERPRISE_DETAIL_KEYS = frozenset({"job", "lang", "src", "tz", "tzname"})
_TBE_KEYS = frozenset({"act", "cws", "org"})
_TBE_REDIRECT_ACTIONS = frozenset({"redirectCws", "redirectCwsV2"})

PortalFamily = Literal["enterprise", "business_edition"]
PageKind = Literal["job_search", "job_detail", "search_results", "redirect"]

TALEO_POLICY_REASON = (
    "policy-held: Oracle Taleo APIs require tenant-specific service access and credentials, "
    "no documented anonymous complete recruiting manifest was verified, and Oracle terms "
    "prohibit automated access without express written permission"
)


@dataclass(frozen=True, slots=True)
class TaleoPolicyHeldCandidate:
    """An exact observed Taleo career URL that is not authorized for ingestion."""

    observed_url: str
    host: str
    portal_family: PortalFamily
    zone: str
    board_path: str
    page_kind: PageKind
    section: str = ""
    organization: str = ""
    career_website: str = ""
    job_id: str = ""
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = TALEO_POLICY_REASON


@dataclass(frozen=True, slots=True)
class TaleoPolicyProbe:
    """A local-only policy result; it deliberately performs zero HTTP requests."""

    candidate: TaleoPolicyHeldCandidate
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


def classify_taleo_board_url(url: str) -> TaleoPolicyHeldCandidate | None:
    """Recognize only measured Oracle Taleo career-board URL shapes."""

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
        or "%" in parsed.path
    ):
        return None
    query = _parse_query(parsed.query)
    if query is None:
        return None

    business_host = _BUSINESS_HOST.fullmatch(host)
    if business_host is not None:
        return _business_candidate(
            value,
            host,
            business_host.group("zone"),
            parsed.path,
            query,
        )

    enterprise_host = _ENTERPRISE_HOST.fullmatch(host)
    if enterprise_host is None:
        return None
    zone = enterprise_host.group("zone")
    if zone in _RESERVED_ENTERPRISE_ZONES:
        return None
    return _enterprise_candidate(value, host, zone, parsed.path, query)


def probe_taleo_policy(url: str) -> TaleoPolicyProbe:
    """Return the Taleo policy hold for an exact URL without network access."""

    candidate = classify_taleo_board_url(url)
    if candidate is None:
        raise ValueError("URL is not an exact supported-shape Taleo public board observation")
    return TaleoPolicyProbe(candidate=candidate)


def _enterprise_candidate(
    value: str,
    host: str,
    zone: str,
    path: str,
    query: dict[str, list[str]],
) -> TaleoPolicyHeldCandidate | None:
    page = _ENTERPRISE_PAGE.fullmatch(path)
    if page is None:
        return None
    section = page.group("section")
    if page.group("page") == "jobsearch":
        if not _valid_enterprise_search_query(query):
            return None
        return TaleoPolicyHeldCandidate(
            observed_url=value,
            host=host,
            portal_family="enterprise",
            zone=zone,
            board_path=path,
            page_kind="job_search",
            section=section,
        )
    if not _valid_enterprise_detail_query(query):
        return None
    return TaleoPolicyHeldCandidate(
        observed_url=value,
        host=host,
        portal_family="enterprise",
        zone=zone,
        board_path=path,
        page_kind="job_detail",
        section=section,
        job_id=query["job"][0],
    )


def _business_candidate(
    value: str,
    host: str,
    zone: str,
    path: str,
    query: dict[str, list[str]],
) -> TaleoPolicyHeldCandidate | None:
    if set(query) - _TBE_KEYS or not _one_matches(query, "org", _SAFE_ORG):
        return None
    if not _one_matches(query, "cws", _DIGITS):
        return None
    action = _one(query, "act")
    if ("act" in query and action is None) or (
        action is not None and action not in _TBE_REDIRECT_ACTIONS
    ):
        return None
    page = _TBE_PAGE.fullmatch(path)
    if page is not None:
        shard = page.group("shard")
        if re.fullmatch(rf"{re.escape(zone)}[0-9]{{2}}", shard, re.IGNORECASE) is None:
            return None
        if page.group("page") == "searchResults" and action is not None:
            return None
        page_kind: PageKind = (
            "search_results" if page.group("page") == "searchResults" else "job_search"
        )
    elif path == _TBE_DISPATCHER and action in _TBE_REDIRECT_ACTIONS:
        page_kind = "redirect"
    else:
        return None
    return TaleoPolicyHeldCandidate(
        observed_url=value,
        host=host,
        portal_family="business_edition",
        zone=zone,
        board_path=path,
        page_kind=page_kind,
        organization=query["org"][0],
        career_website=query["cws"][0],
    )


def _valid_enterprise_search_query(query: dict[str, list[str]]) -> bool:
    if set(query) - _ENTERPRISE_SEARCH_KEYS:
        return False
    if any(len(values) != 1 for key, values in query.items() if key != "jobfield"):
        return False
    if "jobfield" in query and (
        not query["jobfield"]
        or any(_DIGITS.fullmatch(value) is None for value in query["jobfield"])
    ):
        return False
    validators = {
        "lang": _LANGUAGE,
        "portal": _DIGITS,
        "location": _DIGITS,
        "radius": _DIGITS,
        "ftlcompclass": _SAFE_COMPONENT,
    }
    if any(
        not _one_matches(query, key, pattern) for key, pattern in validators.items() if key in query
    ):
        return False
    if "radiusType" in query and _one(query, "radiusType") not in {"K", "M"}:
        return False
    if "searchExpanded" in query and _one(query, "searchExpanded") not in {"false", "true"}:
        return False
    if "ignoreSavedQuery" in query and _one(query, "ignoreSavedQuery") != "":
        return False
    return "f" not in query or _printable_value(_one(query, "f"), maximum=1024)


def _valid_enterprise_detail_query(query: dict[str, list[str]]) -> bool:
    if set(query) - _ENTERPRISE_DETAIL_KEYS or not _one_matches(query, "job", _SAFE_JOB_ID):
        return False
    if any(len(values) != 1 for values in query.values()):
        return False
    if "lang" in query and not _one_matches(query, "lang", _LANGUAGE):
        return False
    if "src" in query and not _one_matches(query, "src", _SAFE_SOURCE):
        return False
    return all(_one_matches(query, key, _TIMEZONE) for key in ("tz", "tzname") if key in query)


def _parse_query(query_string: str) -> dict[str, list[str]] | None:
    if (
        _MALFORMED_ESCAPE.search(query_string)
        or query_string.startswith("&")
        or query_string.endswith("&")
        or "&&" in query_string
    ):
        return None
    pairs = parse_qsl(query_string, keep_blank_values=True, strict_parsing=False)
    query: dict[str, list[str]] = {}
    for key, value in pairs:
        if not key or not _printable_value(key, maximum=128) or not _printable_value(value):
            return None
        query.setdefault(key, []).append(value)
    return query


def _one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key, [])
    return values[0] if len(values) == 1 else None


def _one_matches(query: dict[str, list[str]], key: str, pattern: re.Pattern[str]) -> bool:
    value = _one(query, key)
    return value is not None and pattern.fullmatch(value) is not None


def _printable_value(value: str | None, *, maximum: int = 2048) -> bool:
    return (
        value is not None
        and len(value) <= maximum
        and all(ord(character) >= 32 and ord(character) != 127 for character in value)
    )
