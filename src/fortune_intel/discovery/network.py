"""Bounded, operator-initiated discovery of public ATS career sources."""

from __future__ import annotations

import ipaddress
import socket
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterable, Mapping, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

from fortune_intel.discovery.ats import (
    AtsSourceCandidate,
    classify_ats_url,
    classify_official_structured_url,
    discover_ats_sources,
)
from fortune_intel.discovery.passive import (
    PassiveSourceFingerprint,
    classify_passive_ats_url,
    classify_passive_or_unknown_url,
    has_career_url_marker,
)

_CAREER_SUBDOMAINS = {"career", "careers", "employment", "jobs", "recruiting", "www"}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_PASSIVE_FINGERPRINTS = 50
_MAX_ROBOTS_SITEMAPS = 20
_CAREER_LINK_TEXT = (
    "career",
    "employment",
    "job",
    "join our team",
    "join the team",
    "open positions",
    "search positions",
    "search jobs",
    "view openings",
    "work with us",
    "work here",
)
# These attributes are explicit career-widget signals, rather than general
# JavaScript navigation metadata.  They frequently contain a same-company
# page such as ``/work`` whose path does not otherwise disclose that it leads
# to careers.  We use them only to extend the existing same-company crawl;
# an external URL is still classified passively or as a supported ATS and is
# never fetched from this path.
_CAREER_DATA_ATTRIBUTES = frozenset(
    {
        "data-career-url",
        "data-careers-url",
        "data-job-url",
    }
)


@dataclass(frozen=True, slots=True)
class FetchResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    start_url: str
    disposition: str
    candidates: tuple[AtsSourceCandidate, ...]
    evidence: tuple[str, ...]
    pages_checked: tuple[str, ...]
    fingerprints: tuple[PassiveSourceFingerprint, ...] = ()


@dataclass(frozen=True, slots=True)
class FetchFailure(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class _RobotsPolicy:
    parser: RobotFileParser | None
    default_allowed: bool
    reason: str
    sitemap_urls: tuple[str, ...] = ()

    def allows(self, user_agent: str, url: str) -> tuple[bool, str]:
        if self.parser is None:
            return self.default_allowed, self.reason
        allowed = self.parser.can_fetch(user_agent, url)
        return allowed, "robots permits URL" if allowed else "robots disallows URL"


def _robots_sitemaps(body: bytes) -> tuple[str, ...]:
    """Read bounded, explicit sitemap directives without inferring paths.

    ``robots.txt`` has a standard ``Sitemap:`` extension.  The directive is
    useful here only as first-party evidence for an *already observed* exact
    manifest URL.  Relative values and unrecognized directives are ignored;
    URL validation and company-boundary checks happen before a candidate is
    produced.
    """

    found: list[str] = []
    for line in body.decode("utf-8", errors="replace").splitlines():
        key, separator, value = line.partition(":")
        if separator != ":" or key.strip().casefold() != "sitemap":
            continue
        sitemap = value.partition("#")[0].strip()
        if sitemap and sitemap not in found:
            found.append(sitemap)
        if len(found) >= _MAX_ROBOTS_SITEMAPS:
            break
    return tuple(found)


@dataclass(frozen=True, slots=True)
class _PageResult:
    html: str | None
    final_url: str | None
    redirect_candidate: AtsSourceCandidate | None
    disposition: str
    evidence: str
    redirect_fingerprint: PassiveSourceFingerprint | None = None


class HttpFetcher(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: tuple[float, float],
        max_bytes: int,
    ) -> FetchResponse: ...


class RequestsHtmlFetcher:
    """Streaming requests adapter that never follows redirects automatically."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: tuple[float, float],
        max_bytes: int,
    ) -> FetchResponse:
        try:
            response = self.session.get(
                url,
                headers=dict(headers),
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > max_bytes:
                response.close()
                raise FetchFailure("response_too_large", "response exceeded the byte limit")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=16_384):
                body.extend(chunk)
                if len(body) > max_bytes:
                    response.close()
                    raise FetchFailure("response_too_large", "response exceeded the byte limit")
            response.close()
        except FetchFailure:
            raise
        except (ValueError, requests.RequestException) as error:
            raise FetchFailure("request_failed", f"request failed: {error}") from error
        return FetchResponse(int(response.status_code), dict(response.headers), bytes(body))


class _HrefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[tuple[str, str]] = []
        self.career_data_hrefs: list[tuple[str, str]] = []
        self.structured_hrefs: list[str] = []
        self._anchor_href: str | None = None
        self._anchor_context: list[str] = []

    def _finish_anchor(self) -> None:
        if self._anchor_href is not None:
            self.hrefs.append((self._anchor_href, " ".join(self._anchor_context)))
        self._anchor_href = None
        self._anchor_context = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): str(value or "") for name, value in attrs}
        href = values.get("href", "").strip()
        link_context = " ".join(
            value for value in (values.get("aria-label", ""), values.get("title", "")) if value
        )
        if not href:
            href = ""
        elif tag.casefold() == "a":
            # HTML does not permit nested anchors.  Closing a malformed prior
            # anchor before recording the next one keeps discovery bounded.
            self._finish_anchor()
            self._anchor_href = href
            self._anchor_context = [
                value for value in (values.get("aria-label", ""), values.get("title", "")) if value
            ]
        for attribute in _CAREER_DATA_ATTRIBUTES:
            target = values.get(attribute, "").strip()
            if target:
                self.career_data_hrefs.append((target, f"{attribute} career-widget signal"))
        # Generic data URL fields are too broad on their own.  They are useful
        # only when the page itself gives the element a career-oriented label.
        if _has_career_link_text(link_context):
            for attribute in ("data-href", "data-url"):
                target = values.get(attribute, "").strip()
                if target:
                    self.career_data_hrefs.append((target, link_context))
        rel = {part.casefold() for part in values.get("rel", "").split()}
        media_type = values.get("type", "").partition(";")[0].strip().casefold()
        if tag.casefold() == "link" and (
            "sitemap" in rel
            or ("alternate" in rel and media_type in {
                "application/rss+xml", "application/atom+xml", "application/xml", "text/xml"
            })
        ):
            self.structured_hrefs.append(href)

    def handle_data(self, data: str) -> None:
        if self._anchor_href is not None:
            self._anchor_context.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a":
            self._finish_anchor()

    def finish(self) -> None:
        self._finish_anchor()


def _has_career_link_text(value: str) -> bool:
    normalized = " ".join(value.casefold().split())
    return any(marker in normalized for marker in _CAREER_LINK_TEXT)


class _RejectedTarget(ValueError):
    pass


def _default_resolver(host: str) -> Iterable[str]:
    return {entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}


def _header(headers: Mapping[str, str], name: str) -> str:
    expected = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == expected), "")


class CareerSourceDiscovery:
    """Discover ATS candidates from a small, policy-aware company-site crawl."""

    def __init__(
        self,
        *,
        fetcher: HttpFetcher | None = None,
        resolver=_default_resolver,
        user_agent: str = "OpenRole-Discovery/0.1",
        max_pages: int = 4,
        max_html_bytes: int = 1_000_000,
        max_robots_bytes: int = 256_000,
        max_redirects: int = 3,
        max_links_per_page: int = 100,
        timeout: tuple[float, float] = (5.0, 15.0),
    ) -> None:
        if not 1 <= max_pages <= 10:
            raise ValueError("max_pages must be between 1 and 10")
        if not 1_024 <= max_html_bytes <= 5_000_000:
            raise ValueError("max_html_bytes must be between 1024 and 5000000")
        if not 1_024 <= max_robots_bytes <= 1_000_000:
            raise ValueError("max_robots_bytes must be between 1024 and 1000000")
        if not 0 <= max_redirects <= 5:
            raise ValueError("max_redirects must be between 0 and 5")
        if not 1 <= max_links_per_page <= 500:
            raise ValueError("max_links_per_page must be between 1 and 500")
        self.fetcher = fetcher or RequestsHtmlFetcher()
        self.resolver = resolver
        self.user_agent = user_agent
        self.max_pages = max_pages
        self.max_html_bytes = max_html_bytes
        self.max_robots_bytes = max_robots_bytes
        self.max_redirects = max_redirects
        self.max_links_per_page = max_links_per_page
        self.timeout = timeout
        self._robots_cache: dict[str, _RobotsPolicy] = {}

    def _validate_public_https(self, url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as error:
            raise _RejectedTarget("URL has an invalid port") from error
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise _RejectedTarget("URL must be absolute HTTPS")
        if parsed.username is not None or parsed.password is not None or port not in {None, 443}:
            raise _RejectedTarget("URL credentials and non-HTTPS ports are not allowed")
        host = parsed.hostname.casefold().rstrip(".")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise _RejectedTarget("IP-literal hosts are not allowed")
        try:
            addresses = tuple(self.resolver(host))
        except (OSError, ValueError) as error:
            raise _RejectedTarget("hostname resolution failed") from error
        if not addresses:
            raise _RejectedTarget("hostname did not resolve")
        for address in addresses:
            try:
                parsed_address = ipaddress.ip_address(address)
            except ValueError as error:
                raise _RejectedTarget("resolver returned an invalid address") from error
            if not parsed_address.is_global:
                raise _RejectedTarget("hostname resolves to a private or reserved address")
        normalized = urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
        return normalized, host

    def _normalize_operator_start(self, url: str) -> tuple[str, str, bool]:
        """Validate a verified seed, upgrading only its exact HTTP host to HTTPS."""

        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as error:
            raise _RejectedTarget("URL has an invalid port") from error
        upgraded = False
        if parsed.scheme.casefold() == "http":
            if not parsed.hostname:
                raise _RejectedTarget("URL must be absolute HTTP or HTTPS")
            if parsed.username is not None or parsed.password is not None or port not in {None, 80}:
                raise _RejectedTarget("URL credentials and non-default ports are not allowed")
            host = parsed.hostname.casefold().rstrip(".")
            url = urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
            upgraded = True
        normalized, host = self._validate_public_https(url)
        return normalized, host, upgraded

    @staticmethod
    def _boundary(initial_host: str) -> str:
        return initial_host[4:] if initial_host.startswith("www.") else initial_host

    @staticmethod
    def _same_company(host: str, boundary: str) -> bool:
        if host in {boundary, f"www.{boundary}"}:
            return True
        suffix = f".{boundary}"
        if not host.endswith(suffix):
            return False
        prefix = host[: -len(suffix)]
        return prefix in _CAREER_SUBDOMAINS

    def _one_request(self, url: str, *, max_bytes: int) -> FetchResponse:
        self._validate_public_https(url)
        return self.fetcher.get(
            url,
            headers={"Accept": "text/html,text/plain;q=0.9", "User-Agent": self.user_agent},
            timeout=self.timeout,
            max_bytes=max_bytes,
        )

    def _robots_allows(self, url: str) -> tuple[bool, str]:
        parsed = urlsplit(url)
        origin = f"https://{parsed.hostname}"
        cached = self._robots_cache.get(origin)
        if cached is not None:
            return cached.allows(self.user_agent, url)
        robots_url = f"{origin}/robots.txt"
        try:
            response = self._one_request(robots_url, max_bytes=self.max_robots_bytes)
            redirects = 0
            while response.status_code in _REDIRECT_STATUSES:
                if redirects >= self.max_redirects:
                    raise FetchFailure("redirect_limit", "robots redirect limit exceeded")
                location = _header(response.headers, "Location")
                target = urljoin(robots_url, location)
                target_url, target_host = self._validate_public_https(target)
                if target_host != parsed.hostname:
                    raise _RejectedTarget("robots redirect left its origin")
                robots_url = target_url
                response = self._one_request(robots_url, max_bytes=self.max_robots_bytes)
                redirects += 1
        except (FetchFailure, _RejectedTarget) as error:
            policy = _RobotsPolicy(None, False, f"robots check failed closed: {error}")
        else:
            if response.status_code in {401, 403}:
                policy = _RobotsPolicy(
                    None, False, f"robots access denied with HTTP {response.status_code}"
                )
            elif 400 <= response.status_code < 500:
                policy = _RobotsPolicy(
                    None,
                    True,
                    f"robots unavailable with HTTP {response.status_code}; crawl allowed",
                )
            elif not 200 <= response.status_code < 300:
                policy = _RobotsPolicy(
                    None, False, f"robots check failed closed with HTTP {response.status_code}"
                )
            else:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                lines = response.body.decode("utf-8", errors="replace").splitlines()
                parser.parse(lines)
                policy = _RobotsPolicy(
                    parser,
                    False,
                    "robots policy loaded",
                    _robots_sitemaps(response.body),
                )
        self._robots_cache[origin] = policy
        return policy.allows(self.user_agent, url)

    def _robots_structured_candidates(
        self,
        page_url: str,
        boundary: str,
    ) -> tuple[AtsSourceCandidate, ...]:
        """Return manifest candidates explicitly advertised by this host's robots file.

        We never fetch a manifest while discovering it.  A later connector
        probe must prove a complete JobPosting manifest and an explicit
        source-level robots review is still required before activation.
        """

        host = urlsplit(page_url).hostname
        if not host:
            return ()
        policy = self._robots_cache.get(f"https://{host}")
        if policy is None:
            return ()
        candidates: list[AtsSourceCandidate] = []
        for observed_url in policy.sitemap_urls:
            try:
                target_url, target_host = self._validate_public_https(observed_url)
            except _RejectedTarget:
                continue
            if not self._same_company(target_host, boundary):
                continue
            candidate = classify_official_structured_url(
                target_url,
                origin=f"exact Sitemap directive in https://{host}/robots.txt",
            )
            if candidate is not None:
                candidates.append(candidate)
        return tuple(candidates)

    def _fetch_page(self, url: str, boundary: str) -> _PageResult:
        current = url
        redirects = 0
        while True:
            allowed, reason = self._robots_allows(current)
            if not allowed:
                return _PageResult(None, None, None, "robots_denied", reason)
            response = self._one_request(current, max_bytes=self.max_html_bytes)
            if response.status_code in _REDIRECT_STATUSES:
                if redirects >= self.max_redirects:
                    return _PageResult(
                        None, None, None, "redirect_limit", "page redirect limit exceeded"
                    )
                location = _header(response.headers, "Location")
                if not location:
                    return _PageResult(
                        None,
                        None,
                        None,
                        "unsafe_redirect",
                        "redirect response did not include a Location header",
                    )
                target = urljoin(current, location)
                try:
                    target_url, target_host = self._validate_public_https(target)
                except _RejectedTarget as error:
                    return _PageResult(
                        None,
                        None,
                        None,
                        "unsafe_redirect",
                        f"unsafe redirect rejected: {error}",
                    )
                candidate = classify_ats_url(
                    target_url, origin="redirect Location from an operator-approved company page"
                )
                if candidate is not None:
                    return _PageResult(
                        None,
                        None,
                        candidate,
                        "ats_redirect",
                        (
                            f"redirect target {target_url} resolved to public IP address(es) and "
                            "matched a supported ATS URL shape; target was not fetched and requires "
                            "later identity verification plus connector probing"
                        ),
                    )
                fingerprint = classify_passive_or_unknown_url(target_url, origin_page=current)
                if fingerprint is not None:
                    return _PageResult(
                        None,
                        None,
                        None,
                        "passive_redirect",
                        (
                            f"redirect target {target_url} resolved to public IP address(es); "
                            "external career redirect inventoried without fetching its target"
                        ),
                        fingerprint,
                    )
                if not self._same_company(target_host, boundary):
                    return _PageResult(
                        None,
                        None,
                        None,
                        "unsafe_redirect",
                        (
                            f"redirect target {target_url} resolved to public IP address(es) but "
                            "left the verified company boundary"
                        ),
                    )
                # This is a public, same-company redirect. Its destination is
                # re-checked for robots policy before the next request.
                current = target_url
                redirects += 1
                continue
            if not 200 <= response.status_code < 300:
                return _PageResult(
                    None,
                    None,
                    None,
                    "fetch_failed",
                    f"page returned HTTP {response.status_code}",
                )
            content_type = _header(response.headers, "Content-Type").casefold()
            if not content_type.startswith("text/html"):
                return _PageResult(None, None, None, "not_html", "page was not HTML")
            return _PageResult(
                response.body.decode("utf-8", errors="replace"),
                current,
                None,
                "html_fetched",
                "bounded HTML fetched",
            )

    def discover(self, start_url: str) -> DiscoveryReport:
        """Run a bounded crawl from one operator-supplied company URL."""
        self._robots_cache.clear()
        try:
            normalized_start, initial_host, upgraded = self._normalize_operator_start(start_url)
        except _RejectedTarget as error:
            return DiscoveryReport(start_url, "rejected_start_url", (), (str(error),), ())
        boundary = self._boundary(initial_host)
        queue = deque([normalized_start])
        queued = {normalized_start}
        pages_checked: list[str] = []
        evidence: list[str] = [
            (
                "verified HTTP seed upgraded to HTTPS on the exact same host; "
                "no alternate domain or path was inferred"
                if upgraded
                else "crawl started from an operator-provided HTTPS URL"
            )
        ]
        candidates: dict[tuple[str, str], AtsSourceCandidate] = {}
        fingerprints: dict[tuple[str, str], PassiveSourceFingerprint] = {}
        page_dispositions: list[str] = []
        initial_fingerprint = classify_passive_ats_url(
            normalized_start, origin_page=normalized_start
        )
        if initial_fingerprint is not None:
            key = (initial_fingerprint.family, initial_fingerprint.observed_url)
            fingerprints[key] = initial_fingerprint
        while queue and len(pages_checked) < self.max_pages:
            page_url = queue.popleft()
            pages_checked.append(page_url)
            try:
                result = self._fetch_page(page_url, boundary)
            except (FetchFailure, _RejectedTarget) as error:
                evidence.append(f"{page_url}: fetch rejected: {error}")
                page_dispositions.append("fetch_failed")
                continue
            page_dispositions.append(result.disposition)
            evidence.append(f"{page_url}: {result.evidence}")
            for candidate in self._robots_structured_candidates(page_url, boundary):
                key = (candidate.connector_kind, candidate.board_token.casefold())
                candidates[key] = candidate
            if result.redirect_candidate is not None:
                key = (
                    result.redirect_candidate.connector_kind,
                    result.redirect_candidate.board_token.casefold(),
                )
                candidates[key] = result.redirect_candidate
            if result.redirect_fingerprint is not None:
                item = result.redirect_fingerprint
                fingerprints[(item.family, item.observed_url)] = item
            if result.html is None or result.final_url is None:
                continue
            found = discover_ats_sources(html=result.html, page_url=result.final_url)
            for candidate in found:
                key = (candidate.connector_kind, candidate.board_token.casefold())
                existing = candidates.get(key)
                if existing is None or candidate.confidence > existing.confidence:
                    candidates[key] = candidate
            collector = _HrefCollector()
            collector.feed(result.html)
            collector.finish()
            for href in collector.structured_hrefs[: self.max_links_per_page]:
                target = urljoin(result.final_url, href)
                try:
                    target_url, target_host = self._validate_public_https(target)
                except _RejectedTarget:
                    continue
                if not self._same_company(target_host, boundary):
                    continue
                candidate = classify_official_structured_url(
                    target_url,
                    origin=f"structured manifest link on {result.final_url}",
                )
                if candidate is not None:
                    candidates[(candidate.connector_kind, candidate.board_token.casefold())] = candidate
            links = tuple(dict.fromkeys((*collector.hrefs, *collector.career_data_hrefs)))
            for href, link_text in links[: self.max_links_per_page]:
                target = urljoin(result.final_url, href)
                if classify_ats_url(target) is not None:
                    continue
                has_career_marker = has_career_url_marker(target) or _has_career_link_text(link_text)
                passive = classify_passive_ats_url(target, origin_page=result.final_url)
                if passive is None and not has_career_marker:
                    continue
                try:
                    target_url, target_host = self._validate_public_https(target)
                except _RejectedTarget:
                    continue
                same_company = self._same_company(target_host, boundary)
                structured = classify_official_structured_url(
                    target_url,
                    origin=f"explicit sitemap/feed anchor on {result.final_url}",
                )
                if same_company and structured is not None:
                    candidates[(structured.connector_kind, structured.board_token.casefold())] = structured
                    continue
                passive = classify_passive_or_unknown_url(target_url, origin_page=result.final_url)
                if same_company and passive is not None and passive.family == "unknown_external":
                    passive = None
                if passive is not None and len(fingerprints) < _MAX_PASSIVE_FINGERPRINTS:
                    fingerprints[(passive.family, passive.observed_url)] = passive
                if not has_career_marker or not same_company:
                    continue
                if target_url not in queued:
                    queued.add(target_url)
                    queue.append(target_url)
        if candidates:
            disposition = "candidates_found"
            evidence.append("discovery candidates require connector probing and policy approval")
        elif "robots_denied" in page_dispositions:
            disposition = "robots_denied"
        elif "unsafe_redirect" in page_dispositions:
            disposition = "unsafe_redirect"
        elif "fetch_failed" in page_dispositions:
            disposition = "fetch_failed"
        elif pages_checked:
            disposition = "no_supported_ats_found"
        else:
            disposition = "no_pages_checked"
        return DiscoveryReport(
            start_url=start_url,
            disposition=disposition,
            candidates=tuple(
                sorted(
                    candidates.values(),
                    key=lambda item: (-item.confidence, item.connector_kind, item.board_token),
                )
            ),
            evidence=tuple(evidence),
            pages_checked=tuple(pages_checked),
            fingerprints=tuple(sorted(fingerprints.values())),
        )
