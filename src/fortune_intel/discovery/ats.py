"""Classify known public ATS URLs without making network requests.

Discovery is intentionally separate from verification. A high-confidence result
means that a URL has an unambiguous, supported ATS shape; callers still need to
probe the connector and review source policy before enabling recurring sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from typing import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from fortune_intel.connectors.adp_workforce_now import adp_workforce_now_source_from_url
from fortune_intel.connectors.icims_public import icims_public_source_from_url
from fortune_intel.connectors.workday import workday_source
from fortune_intel.connectors.oracle_recruiting import oracle_recruiting_source
from fortune_intel.connectors.official_structured import official_structured_source
from fortune_intel.connectors.ukg_recruiting_public import (
    ukg_recruiting_public_source_from_url,
)

_TOKEN_LIMIT = 128
# Career widgets often keep their exact outbound ATS URL in an accessible data
# attribute instead of ``href`` (for example, a JS-controlled button).  These
# are observations from the already-fetched official page, not generated URLs.
_LINK_ATTRIBUTES = {
    "action",
    "href",
    "src",
    "data-career-url",
    "data-careers-url",
    "data-href",
    "data-job-url",
    "data-link",
    "data-url",
}
_JSON_SCRIPT_TYPES = frozenset({"application/json", "application/ld+json"})
_MAX_EMBEDDED_JSON_VALUES = 500
_MAX_EMBEDDED_JSON_TEXT = 8_192
_WORKDAY_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
_MYWORKDAYSITE_HOST = re.compile(r"^wd[0-9]+\.myworkdaysite\.com$")
_GREENHOUSE_RESERVED_WEB_ROUTES = frozenset({"embed", "jobs", "search"})
_SMARTRECRUITERS_RESERVED_WEB_ROUTES = frozenset({"my-applications"})


@dataclass(frozen=True, slots=True)
class AtsSourceCandidate:
    """A normalized connector registration inferred from a public URL."""

    connector_kind: str
    board_token: str
    normalized_base_url: str
    confidence: float
    evidence: tuple[str, ...]
    candidate_url: str


@dataclass(frozen=True, slots=True)
class _PatternMatch:
    connector_kind: str
    board_token: str
    normalized_base_url: str
    confidence: float
    evidence: str


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._json_chunks: list[str] | None = None
        self._json_scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): str(value or "") for name, value in attrs}
        for name, value in values.items():
            normalized_name = name.casefold()
            if normalized_name in _LINK_ATTRIBUTES and value:
                self.links.append((normalized_name, value.strip()))
        if tag.casefold() != "script":
            return
        script_type = values.get("type", "").partition(";")[0].strip().casefold()
        if script_type in _JSON_SCRIPT_TYPES or values.get("id") == "__NEXT_DATA__":
            self._json_chunks = []

    def handle_data(self, data: str) -> None:
        if self._json_chunks is not None:
            self._json_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or self._json_chunks is None:
            return
        self._json_scripts.append("".join(self._json_chunks))
        self._json_chunks = None

    def json_values(self) -> tuple[str, ...]:
        """Return bounded scalar strings from explicitly JSON script elements.

        No JavaScript is evaluated.  Invalid JSON and overlong values are
        ignored so an application state blob cannot turn discovery into a
        general-purpose script parser.
        """

        values: list[str] = []
        for raw in self._json_scripts:
            try:
                decoded = json.loads(raw)
            except (TypeError, ValueError):
                continue
            pending = [decoded]
            while pending and len(values) < _MAX_EMBEDDED_JSON_VALUES:
                current = pending.pop()
                if isinstance(current, str):
                    if len(current) <= _MAX_EMBEDDED_JSON_TEXT:
                        values.append(current)
                elif isinstance(current, dict):
                    pending.extend(current.values())
                elif isinstance(current, list):
                    pending.extend(current)
        return tuple(values)


def _safe_token(raw: str | None) -> str | None:
    if raw is None:
        return None
    token = unquote(raw).strip()
    if not token or len(token) > _TOKEN_LIMIT or token in {".", ".."}:
        return None
    if not token.isascii() or not token[0].isalnum():
        return None
    if not all(character.isalnum() or character in "._-" for character in token):
        return None
    return token


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _greenhouse(host: str, path: str, query: str) -> _PatternMatch | None:
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        parts = _segments(path)
        if len(parts) >= 2 and parts[0] == "embed" and parts[1] in {"job_app", "job_board"}:
            token = _safe_token((parse_qs(query).get("for") or [None])[0])
            evidence = "Greenhouse embed URL with an explicit 'for' board token"
            confidence = 0.98
        else:
            token = _safe_token(parts[0]) if parts else None
            if token and token.casefold() in _GREENHOUSE_RESERVED_WEB_ROUTES:
                token = None
            evidence = "Greenhouse public job-board host and board path"
            confidence = 0.99
        if token:
            return _PatternMatch(
                "greenhouse",
                token,
                f"https://boards.greenhouse.io/{token}",
                confidence,
                evidence,
            )
    if host == "boards-api.greenhouse.io":
        parts = _segments(path)
        if len(parts) >= 4 and parts[:2] == ["v1", "boards"] and parts[3] == "jobs":
            token = _safe_token(parts[2])
            if token:
                return _PatternMatch(
                    "greenhouse",
                    token,
                    f"https://boards.greenhouse.io/{token}",
                    1.0,
                    "Greenhouse public API board endpoint",
                )
    return None


def _lever(host: str, path: str) -> _PatternMatch | None:
    web_hosts = {"jobs.lever.co": "global", "jobs.eu.lever.co": "eu"}
    api_hosts = {"api.lever.co": "global", "api.eu.lever.co": "eu"}
    parts = _segments(path)
    if host in web_hosts:
        token = _safe_token(parts[0]) if parts else None
        if token:
            web_host = "jobs.eu.lever.co" if web_hosts[host] == "eu" else "jobs.lever.co"
            return _PatternMatch(
                "lever",
                token,
                f"https://{web_host}/{token}",
                0.99,
                f"Lever {web_hosts[host]} public postings host and site path",
            )
    if host in api_hosts and len(parts) >= 3 and parts[:2] == ["v0", "postings"]:
        token = _safe_token(parts[2])
        if token:
            web_host = "jobs.eu.lever.co" if api_hosts[host] == "eu" else "jobs.lever.co"
            return _PatternMatch(
                "lever",
                token,
                f"https://{web_host}/{token}",
                1.0,
                f"Lever {api_hosts[host]} public Postings API endpoint",
            )
    return None


def _ashby(host: str, path: str) -> _PatternMatch | None:
    parts = _segments(path)
    if host == "jobs.ashbyhq.com":
        token = _safe_token(parts[0]) if parts else None
        if token:
            return _PatternMatch(
                "ashby",
                token,
                f"https://jobs.ashbyhq.com/{token}",
                0.99,
                "Ashby public job-board host and board path",
            )
    if host == "api.ashbyhq.com" and parts[:2] == ["posting-api", "job-board"]:
        token = _safe_token(parts[2]) if len(parts) >= 3 else None
        if token:
            return _PatternMatch(
                "ashby",
                token,
                f"https://jobs.ashbyhq.com/{token}",
                1.0,
                "Ashby public Job Postings API endpoint",
            )
    return None


def _smartrecruiters(host: str, path: str) -> _PatternMatch | None:
    parts = _segments(path)
    if host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"}:
        token = _safe_token(parts[0]) if parts else None
        if token and token.casefold() in _SMARTRECRUITERS_RESERVED_WEB_ROUTES:
            token = None
        if token:
            return _PatternMatch(
                "smartrecruiters",
                token,
                f"https://jobs.smartrecruiters.com/{token}",
                0.99,
                "SmartRecruiters public careers host and company path",
            )
    if (
        host == "api.smartrecruiters.com"
        and len(parts) >= 4
        and parts[:2] == ["v1", "companies"]
        and parts[3] == "postings"
    ):
        token = _safe_token(parts[2])
        if token:
            return _PatternMatch(
                "smartrecruiters",
                token,
                f"https://jobs.smartrecruiters.com/{token}",
                1.0,
                "SmartRecruiters public Posting API endpoint",
            )
    return None


def _icims(host: str, url: str) -> _PatternMatch | None:
    try:
        source = icims_public_source_from_url(url)
    except ValueError:
        return None
    return _PatternMatch(
        "icims_public",
        source.key,
        source.public_base_url,
        1.0,
        "Exact unfiltered iCIMS customer portal /jobs/search URL",
    )


def _workday(
    host: str,
    path: str,
    query: str,
    fragment: str,
) -> _PatternMatch | None:
    parts = _segments(path)
    decoded_parts = [unquote(part) for part in parts]
    if any(part in {".", ".."} or "/" in part or "\\" in part for part in decoded_parts):
        return None
    recruiting_host = _MYWORKDAYSITE_HOST.fullmatch(host) is not None
    if recruiting_host and (query or fragment):
        return None
    if recruiting_host and "" in path.split("/")[1:-1]:
        return None
    if len(parts) >= 5 and parts[:2] == ["wday", "cxs"]:
        tenant = _safe_token(parts[2])
        site = _safe_token(parts[3])
        valid_endpoint = (
            (parts[4] == "jobs" and len(parts) == 5) or (parts[4] == "job" and len(parts) >= 6)
            if recruiting_host
            else parts[4] in {"job", "jobs"}
        )
        if tenant and site and valid_endpoint:
            try:
                source = workday_source(host, tenant, site)
            except ValueError:
                return None
            return _PatternMatch(
                "workday",
                source.key,
                source.public_base_url,
                1.0,
                "Workday public CXS external-career-site endpoint",
            )
    if recruiting_host:
        if len(parts) < 3 or parts[0] != "recruiting":
            return None
        tenant = _safe_token(parts[1])
        site = _safe_token(parts[2])
        valid_public_path = len(parts) == 3 or (len(parts) >= 5 and parts[3] == "job")
        if not tenant or not site or not valid_public_path:
            return None
        try:
            source = workday_source(host, tenant, site)
        except ValueError:
            return None
        return _PatternMatch(
            "workday",
            source.key,
            source.public_base_url,
            0.99,
            "Workday recruiting-path public external career site",
        )
    site_index = 1 if len(parts) >= 2 and _WORKDAY_LOCALE.fullmatch(parts[0]) else 0
    site = (
        _safe_token(parts[site_index])
        if len(parts) > site_index and parts[site_index] != "wday"
        else None
    )
    tenant = host.partition(".")[0]
    if site:
        try:
            source = workday_source(host, tenant, site)
        except ValueError:
            return None
        return _PatternMatch(
            "workday",
            source.key,
            source.public_base_url,
            0.99,
            "Workday-hosted public external career site",
        )
    return None


def _oracle_recruiting(host: str, path: str) -> _PatternMatch | None:
    parts = _segments(path)
    if len(parts) < 5 or parts[:2] != ["hcmUI", "CandidateExperience"]:
        return None
    if parts[3] != "sites":
        return None
    try:
        source = oracle_recruiting_source(host, parts[2], parts[4])
    except ValueError:
        return None
    if len(parts) > 5 and parts[5] not in {"job", "jobs", "requisitions"}:
        return None
    return _PatternMatch(
        "oracle_recruiting",
        source.key,
        source.public_base_url,
        0.99,
        "Oracle Recruiting public Candidate Experience tenant and site path",
    )


def _adp_workforce_now(
    host: str,
    path: str,
    query: str,
    fragment: str,
) -> _PatternMatch | None:
    if host != "workforcenow.adp.com" or fragment:
        return None
    try:
        source = adp_workforce_now_source_from_url(f"https://{host}{path}?{query}")
    except ValueError:
        return None
    return _PatternMatch(
        "adp_workforce_now",
        source.key,
        source.public_base_url,
        1.0,
        "Exact ADP Workforce Now public career-center client and career-center IDs",
    )


def _amazon_jobs(host: str, path: str, fragment: str) -> _PatternMatch | None:
    if host not in {"amazon.jobs", "www.amazon.jobs"} or fragment:
        return None
    parts = _segments(path)
    if parts not in (["en", "search"], ["en", "search.json"]):
        return None
    return _PatternMatch(
        "amazon_jobs",
        "us",
        "https://www.amazon.jobs/en/search?country=USA",
        1.0,
        "Amazon Jobs public search endpoint with explicit U.S. country facet",
    )


def _apple_jobs(host: str, path: str, fragment: str) -> _PatternMatch | None:
    if host != "jobs.apple.com" or fragment or _segments(path) != ["en-us", "search"]:
        return None
    return _PatternMatch(
        "apple_jobs",
        "us",
        "https://jobs.apple.com/en-us/search?location=united-states-USA",
        1.0,
        "Apple Jobs public U.S.-filtered server-rendered search",
    )


def _ukg_recruiting_public(host: str, url: str) -> _PatternMatch | None:
    if host not in {
        "recruiting.ultipro.com",
        "recruiting2.ultipro.com",
        "recruiting.ultipro.ca",
    }:
        return None
    try:
        source = ukg_recruiting_public_source_from_url(url)
    except ValueError:
        return None
    return _PatternMatch(
        "ukg_recruiting_public",
        source.key,
        source.public_base_url,
        1.0,
        "Exact public UKG Recruiting tenant and external job-board UUID",
    )


def classify_ats_url(url: str, *, origin: str = "candidate URL") -> AtsSourceCandidate | None:
    """Return a supported ATS candidate for ``url``, or ``None``.

    Only exact allow-listed public ATS hosts and recognized paths are accepted.
    HTTP candidates are normalized to HTTPS and receive a small confidence
    reduction so operators can see that transport was inferred.
    """

    value = url.strip()
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"}:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.username is not None or parsed.password is not None or port not in {None, 80, 443}:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if _MYWORKDAYSITE_HOST.fullmatch(host) and ("?" in value or "#" in value):
        return None
    match = (
        _greenhouse(host, parsed.path, parsed.query)
        or _amazon_jobs(host, parsed.path, parsed.fragment)
        or _apple_jobs(host, parsed.path, parsed.fragment)
        or _adp_workforce_now(host, parsed.path, parsed.query, parsed.fragment)
        or _ukg_recruiting_public(host, value)
        or _lever(host, parsed.path)
        or _ashby(host, parsed.path)
        or _smartrecruiters(host, parsed.path)
        or _icims(host, value)
        or _workday(host, parsed.path, parsed.query, parsed.fragment)
        or _oracle_recruiting(host, parsed.path)
    )
    if match is None:
        return None
    if match.connector_kind == "adp_workforce_now" and (
        parsed.scheme.casefold() != "https" or port is not None or "#" in value
    ):
        return None
    if (
        match.connector_kind == "workday"
        and _MYWORKDAYSITE_HOST.fullmatch(host)
        and (parsed.scheme.casefold() != "https" or port is not None)
    ):
        return None
    if match.connector_kind == "icims_public" and (
        parsed.scheme.casefold() != "https" or port is not None
    ):
        # iCIMS is inventory-only and must preserve an exact observed HTTPS
        # portal. Unlike approved public feeds, transport is not inferred.
        return None
    confidence = match.confidence
    evidence = [match.evidence, origin]
    if parsed.scheme.casefold() == "http":
        confidence -= 0.05
        evidence.append("HTTP candidate normalized to HTTPS; transport requires verification")
    return AtsSourceCandidate(
        connector_kind=match.connector_kind,
        board_token=match.board_token,
        normalized_base_url=match.normalized_base_url,
        confidence=round(confidence, 2),
        evidence=tuple(evidence),
        candidate_url=value,
    )


def classify_official_structured_url(
    url: str,
    *,
    origin: str,
) -> AtsSourceCandidate | None:
    """Classify an explicitly observed official sitemap/feed, never a guessed path."""

    try:
        source = official_structured_source(url)
    except ValueError:
        return None
    path = urlsplit(source.manifest_url).path.casefold()
    if not path.endswith((".xml", ".rss", ".atom")):
        return None
    return AtsSourceCandidate(
        connector_kind="official_structured",
        board_token=source.key,
        normalized_base_url=source.public_base_url,
        confidence=0.95,
        evidence=(
            "Exact official-host sitemap or syndication URL observed in company HTML",
            origin,
            "Activation requires exhaustive complete-manifest probing and policy approval",
        ),
        candidate_url=source.manifest_url,
    )


def discover_ats_sources(
    candidate_urls: Iterable[str] = (),
    *,
    html: str | None = None,
    page_url: str | None = None,
) -> tuple[AtsSourceCandidate, ...]:
    """Classify and deduplicate explicit URLs plus links extracted from HTML."""

    candidates: list[AtsSourceCandidate] = []
    for url in candidate_urls:
        candidate = classify_ats_url(url)
        if candidate is not None:
            candidates.append(candidate)

    if html:
        parser = _LinkCollector()
        parser.feed(html)
        for attribute, raw_url in parser.links:
            resolved = urljoin(page_url, raw_url) if page_url else raw_url
            candidate = classify_ats_url(resolved, origin=f"HTML {attribute} attribute")
            if candidate is not None:
                candidates.append(candidate)
        for raw_url in parser.json_values():
            resolved = urljoin(page_url, raw_url) if page_url else raw_url
            candidate = classify_ats_url(
                resolved,
                origin="exact URL in an application/json or JSON-LD script element",
            )
            if candidate is not None:
                candidates.append(candidate)

    by_source: dict[tuple[str, str, str], AtsSourceCandidate] = {}
    for candidate in candidates:
        key = (
            candidate.connector_kind,
            candidate.board_token.casefold(),
            candidate.normalized_base_url.casefold(),
        )
        existing = by_source.get(key)
        if existing is None:
            by_source[key] = candidate
        elif candidate.confidence > existing.confidence:
            combined_evidence = tuple(dict.fromkeys((*candidate.evidence, *existing.evidence)))
            by_source[key] = AtsSourceCandidate(
                connector_kind=candidate.connector_kind,
                board_token=candidate.board_token,
                normalized_base_url=candidate.normalized_base_url,
                confidence=candidate.confidence,
                evidence=combined_evidence,
                candidate_url=candidate.candidate_url,
            )
        elif candidate.confidence == existing.confidence:
            combined_evidence = tuple(dict.fromkeys((*existing.evidence, *candidate.evidence)))
            by_source[key] = AtsSourceCandidate(
                connector_kind=existing.connector_kind,
                board_token=existing.board_token,
                normalized_base_url=existing.normalized_base_url,
                confidence=existing.confidence,
                evidence=combined_evidence,
                candidate_url=existing.candidate_url,
            )
    return tuple(
        sorted(
            by_source.values(),
            key=lambda item: (-item.confidence, item.connector_kind, item.board_token.casefold()),
        )
    )
