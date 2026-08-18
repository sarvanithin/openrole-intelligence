"""Fail-closed classification of exact, policy-held Avature observations.

Avature exposes customer-configured integration endpoints rather than a documented
anonymous, uniform job-manifest API.  This module therefore records only URL
shapes present in the measured inventory and deliberately performs no HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlsplit

_HOSTS = frozenset(
    {
        "ally.avature.net",
        "jackhenry.avature.net",
        "ross.avature.net",
        "synopsys.avature.net",
    }
)
_ALLY_HOST = "ally.avature.net"
_SEARCH_PATH = "/careers/SearchJobs/"
_DFS_PATH = "/careers/SearchJobs/%23dfs"
_OBSERVED_FILTERS = frozenset(
    {
        "[1318373]",
        "[20865,20877]",
        "[20867,20887,20871]",
        "[20870,20872,1316147,20874,20875,20885,20886,20887]",
        "[20873,2057593]",
        "[20884,20879,20881]",
        "[265477]",
    }
)

ObservationKind = Literal["board", "filtered_search"]

AVATURE_POLICY_REASON = (
    "policy-held: Avature integrations use customer-admin-configured custom endpoints "
    "and vendor credentials/API keys; no documented anonymous standardized "
    "complete-manifest API or redistribution authorization was verified"
)


@dataclass(frozen=True, slots=True)
class AvaturePolicyHeldCandidate:
    """An exact Avature career-board observation that cannot be activated."""

    observed_url: str
    host: str
    tenant: str
    board_path: str
    observation_kind: ObservationKind
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = AVATURE_POLICY_REASON


@dataclass(frozen=True, slots=True)
class AvaturePolicyProbe:
    """A local-only policy result that deliberately performs no HTTP."""

    candidate: AvaturePolicyHeldCandidate
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


def _single_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key, [])
    return values[0] if len(values) == 1 else None


def _is_observed_search(path: str, query: dict[str, list[str]]) -> bool:
    """Match only the search/filter combinations present in inventory."""

    if _single_value(query, "listFilterMode") != "1":
        return False
    if _single_value(query, "jobRecordsPerPage") != "6":
        return False
    if path == _DFS_PATH:
        return set(query) == {"listFilterMode", "jobRecordsPerPage"}
    if path != _SEARCH_PATH or set(query) != {
        "667",
        "667_format",
        "listFilterMode",
        "jobRecordsPerPage",
    }:
        return False
    return (
        _single_value(query, "667") in _OBSERVED_FILTERS
        and _single_value(query, "667_format") == "613"
    )


def classify_avature_board_url(url: str) -> AvaturePolicyHeldCandidate | None:
    """Recognize only exact HTTPS Avature board/search observations in inventory.

    Talent-community, login, application, and generic landing pages are rejected:
    none is evidence of a complete public job manifest.
    """

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
        or host not in _HOSTS
        or "//" in parsed.path
    ):
        return None

    # The other three measured hosts contain only talent/login/landing observations.
    if host != _ALLY_HOST:
        return None
    if parsed.path == "/careers" and not parsed.query:
        return AvaturePolicyHeldCandidate(
            observed_url=value,
            host=host,
            tenant="ally",
            board_path=parsed.path,
            observation_kind="board",
        )
    if "%" in parsed.path and parsed.path != _DFS_PATH:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not _is_observed_search(parsed.path, query):
        return None
    return AvaturePolicyHeldCandidate(
        observed_url=value,
        host=host,
        tenant="ally",
        board_path=parsed.path,
        observation_kind="filtered_search",
    )


def probe_avature_policy(url: str) -> AvaturePolicyProbe:
    """Return the Avature policy hold without accessing the network."""

    candidate = classify_avature_board_url(url)
    if candidate is None:
        raise ValueError("URL is not an exact supported-shape Avature board observation")
    return AvaturePolicyProbe(candidate=candidate)
