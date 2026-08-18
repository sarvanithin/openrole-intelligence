"""Fail-closed parsing for exact, policy-held Jobvite career URLs.

Jobvite's terms prohibit scraping and redistribution of Job Postings, while its
official integration material describes customer-enabled API access. This
module therefore performs local inventory classification only: it never derives
an endpoint, accesses the network, or exposes a scheduler connector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

_HOST = "jobs.jobvite.com"
_SAFE_TOKEN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_BOARD_PATH = re.compile(rf"^/(?P<company>{_SAFE_TOKEN})/?$")
_JOB_PATH = re.compile(rf"^/(?P<company>{_SAFE_TOKEN})/job/(?P<job_id>{_SAFE_TOKEN})$")
_RESERVED_COMPANY_SLUGS = frozenset(
    {
        "api",
        "apply",
        "auth",
        "job",
        "jobalerts",
        "jobs",
        "login",
        "static",
        "www",
    }
)
_JOB_QUERY_KEYS = frozenset({"fr", "nl"})
_MALFORMED_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")

PageKind = Literal["board", "job_detail"]

JOBVITE_POLICY_REASON = (
    "policy-held: Jobvite's official terms prohibit scraping and third-party redistribution "
    "of Job Postings, no documented anonymous complete recruiting manifest was verified, "
    "and official integrations use customer-enabled API access"
)


@dataclass(frozen=True, slots=True)
class JobvitePolicyHeldCandidate:
    """An exact Jobvite job-board URL that is not authorized for ingestion."""

    observed_url: str
    host: str
    company_slug: str
    board_path: str
    page_kind: PageKind
    job_id: str = ""
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = JOBVITE_POLICY_REASON


@dataclass(frozen=True, slots=True)
class JobvitePolicyProbe:
    """A local-only policy result; it deliberately performs zero HTTP requests."""

    candidate: JobvitePolicyHeldCandidate
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


def classify_jobvite_board_url(url: str) -> JobvitePolicyHeldCandidate | None:
    """Recognize only exact HTTPS Jobvite company-board and job-detail shapes."""

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

    board = _BOARD_PATH.fullmatch(parsed.path)
    if board is not None:
        company_slug = board.group("company")
        if company_slug.casefold() in _RESERVED_COMPANY_SLUGS or parsed.query:
            return None
        return JobvitePolicyHeldCandidate(
            observed_url=value,
            host=host,
            company_slug=company_slug,
            board_path=parsed.path,
            page_kind="board",
        )

    job = _JOB_PATH.fullmatch(parsed.path)
    if job is None or job.group("company").casefold() in _RESERVED_COMPANY_SLUGS:
        return None
    query = _unique_query(parsed.query)
    if query is None or not set(query).issubset(_JOB_QUERY_KEYS):
        return None
    if "fr" in query and query["fr"] != "true":
        return None
    if "nl" in query and query["nl"] != "1":
        return None
    return JobvitePolicyHeldCandidate(
        observed_url=value,
        host=host,
        company_slug=job.group("company"),
        board_path=parsed.path,
        page_kind="job_detail",
        job_id=job.group("job_id"),
    )


def probe_jobvite_policy(url: str) -> JobvitePolicyProbe:
    """Return the Jobvite policy hold for an exact URL without network access."""

    candidate = classify_jobvite_board_url(url)
    if candidate is None:
        raise ValueError("URL is not an exact supported-shape Jobvite public board observation")
    return JobvitePolicyProbe(candidate=candidate)


def _unique_query(query_string: str) -> dict[str, str] | None:
    if (
        _MALFORMED_ESCAPE.search(query_string)
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
