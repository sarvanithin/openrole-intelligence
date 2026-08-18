"""Fail-closed jobs from an exact official sitemap or syndication feed."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import requests

from fortune_intel.connectors.common import clean_html, clean_text, normalize_timestamp
from fortune_intel.connectors.models import ConnectorError, ConnectorJob, ConnectorResult

_XML_TYPES = frozenset({"application/xml", "text/xml", "application/rss+xml", "application/atom+xml"})
_MAX_URL = 4096


@dataclass(frozen=True, slots=True)
class OfficialStructuredSource:
    manifest_url: str

    @property
    def key(self) -> str:
        return self.manifest_url

    @property
    def public_base_url(self) -> str:
        return self.manifest_url


@dataclass(frozen=True, slots=True)
class TextResponse:
    url: str
    content_type: str
    text: str


class TextClient(Protocol):
    def get_text(self, url: str, *, max_bytes: int) -> TextResponse: ...


def _public_https_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("structured source URL has an invalid port") from error
    if (
        len(value) > _MAX_URL
        or parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        raise ValueError("structured source URL must be an exact public HTTPS URL")
    host = parsed.hostname.casefold().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("structured source URL cannot use an IP literal")
    if any(part in {".", ".."} for part in parsed.path.split("/")):
        raise ValueError("structured source URL cannot contain path traversal")
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def official_structured_source(url: str) -> OfficialStructuredSource:
    return OfficialStructuredSource(_public_https_url(url))


def parse_official_structured_source_key(value: str) -> OfficialStructuredSource:
    return official_structured_source(value)


class OfficialStructuredHttpClient:
    """Bounded no-redirect text client pinned to one public origin."""

    def __init__(self, origin_url: str, *, session: requests.Session | None = None) -> None:
        parsed = urlsplit(_public_https_url(origin_url))
        self.host = parsed.hostname or ""
        self.session = session or requests.Session()

    def _validate_target(self, url: str) -> str:
        normalized = _public_https_url(url)
        if urlsplit(normalized).hostname != self.host:
            raise ValueError("structured source traversal left its exact official host")
        addresses = {item[4][0] for item in socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
            raise ValueError("structured source host did not resolve exclusively to public addresses")
        return normalized

    def get_text(self, url: str, *, max_bytes: int) -> TextResponse:
        target = self._validate_target(url)
        response = self.session.get(
            target,
            headers={"Accept": "text/html,application/xml,text/xml", "User-Agent": "OpenRole-Structured/0.1"},
            timeout=(5.0, 30.0),
            allow_redirects=False,
            stream=True,
        )
        try:
            if not 200 <= int(response.status_code) < 300:
                raise ValueError(f"structured source returned HTTP {response.status_code}")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=65_536):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ValueError("structured source response exceeded the byte limit")
        finally:
            response.close()
        try:
            text = bytes(body).decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("structured source response was not UTF-8") from error
        content_type = str(response.headers.get("Content-Type") or "").partition(";")[0].casefold()
        return TextResponse(target, content_type, text)


class _JsonLdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.in_json_ld = False
        self.parts: list[str] = []
        self.documents: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        values = {name.casefold(): str(value or "").casefold() for name, value in attrs}
        if values.get("type", "").split(";", 1)[0].strip() == "application/ld+json":
            self.in_json_ld = True
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.in_json_ld:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self.in_json_ld:
            self.documents.append("".join(self.parts))
            self.in_json_ld = False
            self.parts = []


class OfficialStructuredConnector:
    """Traverse an exact manifest and require one JobPosting per enumerated URL."""

    source = "official_structured"

    def __init__(
        self,
        source_key: str,
        *,
        client: TextClient | None = None,
        max_manifests: int = 100,
        max_jobs: int = 10_000,
        max_bytes: int = 5_000_000,
    ) -> None:
        self.structured = parse_official_structured_source_key(source_key)
        if not 1 <= max_manifests <= 1_000 or not 1 <= max_jobs <= 100_000:
            raise ValueError("structured source limits are out of range")
        self.client = client or OfficialStructuredHttpClient(self.structured.manifest_url)
        self.max_manifests = max_manifests
        self.max_jobs = max_jobs
        self.max_bytes = max_bytes
        self.host = urlsplit(self.structured.manifest_url).hostname or ""

    def fetch(self) -> ConnectorResult:
        errors: list[ConnectorError] = []
        try:
            urls, pages, feed = self._enumerate()
        except ValueError as error:
            return self._result((), False, (self._error(error),), 0)
        if feed:
            errors.append(self._error(ValueError("syndication feeds cannot prove a complete active-job manifest")))
        jobs: list[ConnectorJob] = []
        seen_ids: set[str] = set()
        for url in urls:
            try:
                response = self.client.get_text(url, max_bytes=self.max_bytes)
                if response.content_type not in {"text/html", "application/xhtml+xml", ""}:
                    raise ValueError("job detail response was not HTML")
                job = self._job(response.url, response.text)
                if job.external_job_id in seen_ids:
                    raise ValueError(f"duplicate native job ID: {job.external_job_id}")
                seen_ids.add(job.external_job_id)
                jobs.append(job)
            except (TypeError, ValueError) as error:
                errors.append(self._error(error, url))
        complete = not feed and not errors and len(jobs) == len(urls)
        return self._result(tuple(jobs), complete, tuple(errors), pages + len(urls))

    def _enumerate(self) -> tuple[list[str], int, bool]:
        queue = [self.structured.manifest_url]
        seen_manifests: set[str] = set()
        urls: list[str] = []
        seen_urls: set[str] = set()
        feed = False
        while queue:
            if len(seen_manifests) >= self.max_manifests:
                raise ValueError("manifest traversal exceeded the completeness ceiling")
            manifest = queue.pop(0)
            if manifest in seen_manifests:
                raise ValueError("manifest traversal contained a cycle or duplicate")
            seen_manifests.add(manifest)
            response = self.client.get_text(manifest, max_bytes=self.max_bytes)
            if response.content_type and response.content_type not in _XML_TYPES:
                raise ValueError("manifest response was not XML")
            root = self._xml(response.text)
            local = self._local(root.tag)
            if local == "sitemapindex":
                children = self._locations(root)
                if not children:
                    raise ValueError("sitemap index did not enumerate child manifests")
                queue.extend(self._same_origin(item) for item in children)
            elif local == "urlset":
                for item in self._locations(root):
                    normalized = self._same_origin(item)
                    if normalized in seen_urls:
                        raise ValueError("manifest contained duplicate job URLs")
                    seen_urls.add(normalized)
                    urls.append(normalized)
                    if len(urls) > self.max_jobs:
                        raise ValueError("job enumeration exceeded the completeness ceiling")
            elif local in {"rss", "feed"}:
                feed = True
                for item in self._feed_links(root):
                    normalized = self._same_origin(item)
                    if normalized in seen_urls:
                        raise ValueError("feed contained duplicate job URLs")
                    seen_urls.add(normalized)
                    urls.append(normalized)
                    if len(urls) > self.max_jobs:
                        raise ValueError("job enumeration exceeded the completeness ceiling")
            else:
                raise ValueError("manifest root must be sitemapindex, urlset, RSS, or Atom")
        return urls, len(seen_manifests), feed

    @staticmethod
    def _xml(text: str) -> ElementTree.Element:
        lowered = text.casefold()
        if "<!doctype" in lowered or "<!entity" in lowered:
            raise ValueError("XML declarations with DTD or entities are forbidden")
        try:
            return ElementTree.fromstring(text)
        except ElementTree.ParseError as error:
            raise ValueError("manifest was not well-formed XML") from error

    @staticmethod
    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].casefold()

    def _locations(self, root: ElementTree.Element) -> list[str]:
        locations: list[str] = []
        for entry in root:
            for item in entry:
                if self._local(item.tag) == "loc" and clean_text(item.text):
                    locations.append(clean_text(item.text))
                    break
        return locations

    def _feed_links(self, root: ElementTree.Element) -> list[str]:
        links: list[str] = []
        for item in root.iter():
            if self._local(item.tag) not in {"link", "guid"}:
                continue
            href = clean_text(item.attrib.get("href") or item.text)
            rel = clean_text(item.attrib.get("rel")).casefold()
            if href and rel in {"", "alternate"}:
                links.append(href)
        return links

    def _same_origin(self, url: str) -> str:
        normalized = _public_https_url(url)
        if urlsplit(normalized).hostname != self.host:
            raise ValueError("manifest enumerated a URL outside the exact official host")
        return normalized

    def _job(self, page_url: str, html: str) -> ConnectorJob:
        collector = _JsonLdCollector()
        collector.feed(html)
        postings: list[dict[str, object]] = []
        for document in collector.documents:
            try:
                payload = json.loads(document)
            except json.JSONDecodeError as error:
                raise ValueError("job page contained invalid JSON-LD") from error
            postings.extend(self._postings(payload))
        if len(postings) != 1:
            raise ValueError("job page must contain exactly one JobPosting JSON-LD object")
        record = postings[0]
        title = clean_text(record.get("title") or record.get("name"))
        if not title:
            raise ValueError("JobPosting is missing title")
        canonical = self._same_origin(clean_text(record.get("url") or page_url))
        opened = normalize_timestamp(record.get("datePosted"))
        if record.get("datePosted") and opened is None:
            raise ValueError("JobPosting datePosted is invalid")
        updated = normalize_timestamp(record.get("dateModified"))
        if record.get("dateModified") and updated is None:
            raise ValueError("JobPosting dateModified is invalid")
        identifier = record.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value") or identifier.get("name")
        external_id = clean_text(identifier) or hashlib.sha256(canonical.encode()).hexdigest()
        locations = self._locations_from_job(record)
        return ConnectorJob(
            self.source,
            external_id,
            title,
            canonical,
            location="; ".join(locations),
            description=clean_html(record.get("description")),
            source_opened_at=opened,
            source_updated_at=updated,
            metadata={
                "valid_through": normalize_timestamp(record.get("validThrough")),
                "employment_type": record.get("employmentType"),
                "job_location_type": clean_text(record.get("jobLocationType")),
                "all_locations": locations,
                "hiring_organization": self._organization(record.get("hiringOrganization")),
                "manifest_url": self.structured.manifest_url,
            },
        )

    def _postings(self, value: object) -> list[dict[str, object]]:
        if isinstance(value, list):
            return [item for child in value for item in self._postings(child)]
        if not isinstance(value, dict):
            return []
        graph = value.get("@graph")
        items = self._postings(graph) if graph is not None else []
        types = value.get("@type")
        kinds = {str(item).casefold() for item in (types if isinstance(types, list) else [types])}
        if "jobposting" in kinds:
            items.append(value)
        return items

    @staticmethod
    def _organization(value: object) -> str:
        return clean_text(value.get("name")) if isinstance(value, dict) else ""

    @staticmethod
    def _locations_from_job(record: dict[str, object]) -> list[str]:
        values = record.get("jobLocation") or record.get("applicantLocationRequirements") or []
        entries = values if isinstance(values, list) else [values]
        result: list[str] = []
        if clean_text(record.get("jobLocationType")).casefold() == "telecommute":
            result.append("Remote")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            address = entry.get("address") if isinstance(entry.get("address"), dict) else entry
            parts = [address.get(key) for key in ("addressLocality", "addressRegion", "addressCountry")]
            location = ", ".join(clean_text(part) for part in parts if clean_text(part))
            if location and location not in result:
                result.append(location)
        return result

    def _result(self, jobs: tuple[ConnectorJob, ...], complete: bool, errors: tuple[ConnectorError, ...], pages: int) -> ConnectorResult:
        return ConnectorResult(self.source, self.structured.key, jobs, complete, errors, pages)

    @staticmethod
    def _error(error: Exception, external_id: str | None = None) -> ConnectorError:
        return ConnectorError("invalid_structured_source", str(error), False, external_id)
