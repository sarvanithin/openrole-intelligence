"""Strict complete manifests from authorized public iCIMS career portals."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from defusedxml import ElementTree

from fortune_intel.connectors.common import (
    clean_html,
    clean_text,
    http_error,
    normalize_timestamp,
    record_error,
)
from fortune_intel.connectors.http import HttpFailure, JsonHttpClient
from fortune_intel.connectors.icims import ICIMSSource, icims_source
from fortune_intel.connectors.models import ConnectorError, ConnectorJob, ConnectorResult

_JOB_PATH = re.compile(r"^/jobs/([1-9][0-9]{0,19})/[A-Za-z0-9%._~-]+/job/?$")
_PAGE_HEADING = re.compile(r"^Search Results\s+Page ([1-9][0-9]*) of ([1-9][0-9]*)$")


def icims_public_source_from_url(url: str) -> ICIMSSource:
    """Accept only an exact unfiltered public iCIMS search URL."""

    value = url.strip()
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("iCIMS URL has an invalid port") from error
    if (
        not value
        or len(value) > 4_096
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path.rstrip("/") != "/jobs/search"
        or parsed.fragment
        or "#" in value
    ):
        raise ValueError("iCIMS source must be an exact HTTPS public search URL")
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    if set(query) - {"ss", "in_iframe"} or any(len(values) != 1 for values in query.values()):
        raise ValueError("iCIMS source search URL must not contain filters")
    if "ss" in query and query["ss"] != ["1"]:
        raise ValueError("iCIMS search flag must be ss=1")
    if "in_iframe" in query and query["in_iframe"] != ["1"]:
        raise ValueError("iCIMS iframe flag must be in_iframe=1")
    return icims_source(parsed.hostname or "")


class ICIMSPublicConnector:
    """Fetch every job from an exact authorized iCIMS portal."""

    source = "icims_public"

    def __init__(
        self,
        source_key: str,
        *,
        max_pages: int = 500,
        detail_concurrency: int = 4,
        client: JsonHttpClient | None = None,
        detail_client_factory: Callable[[], JsonHttpClient] | None = None,
    ) -> None:
        self.icims = icims_source(source_key)
        if not 1 <= max_pages <= 500:
            raise ValueError("max_pages must be between 1 and 500")
        if not 1 <= detail_concurrency <= 8:
            raise ValueError("detail_concurrency must be between 1 and 8")
        self.max_pages = max_pages
        self.detail_concurrency = detail_concurrency
        self.client = client or JsonHttpClient()
        self._detail_client_factory = detail_client_factory or (
            JsonHttpClient if client is None else lambda: self.client
        )
        self._detail_clients = threading.local()

    def fetch(self) -> ConnectorResult:
        jobs: list[ConnectorJob] = []
        errors: list[ConnectorError] = []
        seen: set[str] = set()
        expected_pages: int | None = None
        try:
            manifest = self._manifest()
        except HttpFailure as error:
            return self._result(jobs, [http_error(error)], 0, False)
        except (ElementTree.ParseError, TypeError, ValueError) as error:
            return self._result(jobs, [record_error(error)], 0, False)
        if not manifest:
            return self._result(jobs, errors, 0, True)
        for page_index in range(self.max_pages):
            try:
                html = self.client.get_text(self._listing_url(page_index))
                current, total_pages, summaries = self._parse_listing(html)
            except HttpFailure as error:
                errors.append(http_error(error, page=page_index + 1))
                return self._result(jobs, errors, page_index, False)
            except (TypeError, ValueError) as error:
                errors.append(record_error(error))
                return self._result(jobs, errors, page_index + 1, False)
            if current != page_index + 1:
                errors.append(record_error(ValueError("listing returned an unexpected page")))
                return self._result(jobs, errors, page_index + 1, False)
            if expected_pages is None:
                expected_pages = total_pages
            elif total_pages != expected_pages:
                errors.append(
                    record_error(ValueError("listing page total changed during pagination"))
                )
                return self._result(jobs, errors, page_index + 1, False)
            if not summaries:
                errors.append(record_error(ValueError("listing page contained no job cards")))
                return self._result(jobs, errors, page_index + 1, False)
            try:
                for summary in summaries:
                    manifest_entry = manifest.get(summary["id"])
                    if manifest_entry is None or manifest_entry[0] != summary["url"]:
                        raise ValueError("listing job did not match the robots sitemap manifest")
                    summary["updated"] = manifest_entry[1] or ""
            except ValueError as error:
                errors.append(record_error(error))
                return self._result(jobs, errors, page_index + 1, False)
            for job, error in self._fetch_details(summaries, page_index + 1):
                if error:
                    errors.append(error)
                    continue
                assert job is not None
                if job.external_job_id in seen:
                    errors.append(
                        record_error(
                            ValueError(f"duplicate native job ID: {job.external_job_id}"),
                            external_id=job.external_job_id,
                        )
                    )
                    continue
                seen.add(job.external_job_id)
                jobs.append(job)
            if page_index + 1 == expected_pages:
                if seen != set(manifest):
                    errors.append(
                        record_error(
                            ValueError(
                                "paginated job IDs did not match the robots sitemap manifest"
                            )
                        )
                    )
                return self._result(jobs, errors, page_index + 1, not errors)
        errors.append(record_error(ValueError(f"pagination exceeded {self.max_pages} pages")))
        return self._result(jobs, errors, self.max_pages, False)

    def _result(
        self, jobs: list[ConnectorJob], errors: list[ConnectorError], pages: int, complete: bool
    ) -> ConnectorResult:
        return ConnectorResult(
            self.source, self.icims.key, tuple(jobs), complete, tuple(errors), pages
        )

    def _listing_url(self, page_index: int) -> str:
        return f"{self.icims.public_base_url}?ss=1&pr={page_index}&in_iframe=1"

    def _manifest(self) -> dict[str, tuple[str, str | None]]:
        robots = self.client.get_text(f"https://{self.icims.host}/robots.txt", max_bytes=200_000)
        sitemap_values = [
            line.split(":", 1)[1].strip()
            for line in robots.splitlines()
            if line.casefold().startswith("sitemap:")
        ]
        if len(sitemap_values) != 1:
            raise ValueError("robots.txt must declare exactly one iCIMS sitemap")
        sitemap = sitemap_values[0]
        parsed = urlsplit(sitemap)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").casefold().rstrip(".") != self.icims.host
            or parsed.path != "/sitemap.xml"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("robots.txt sitemap must be the exact same-host /sitemap.xml")
        xml = self.client.get_text(sitemap, max_bytes=5_000_000)
        root = ElementTree.fromstring(xml)
        if root.tag != "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset":
            raise ValueError("iCIMS sitemap must contain one standard URL set")
        manifest: dict[str, tuple[str, str | None]] = {}
        for node in root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url"):
            locs = node.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
            if len(locs) != 1 or not clean_text(locs[0].text):
                raise ValueError("iCIMS sitemap entry must contain one location")
            url = clean_text(locs[0].text)
            if url.rstrip("/") in {
                self.icims.public_base_url,
                f"https://{self.icims.host}/jobs/intro",
            }:
                continue
            identifier, canonical = self._job_identity(url)
            if identifier in manifest:
                raise ValueError(f"duplicate native job ID in sitemap: {identifier}")
            modified_nodes = node.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod")
            modified = None
            if modified_nodes:
                if len(modified_nodes) != 1:
                    raise ValueError("iCIMS sitemap entry contains duplicate lastmod fields")
                modified = normalize_timestamp(modified_nodes[0].text)
                if modified is None:
                    raise ValueError("iCIMS sitemap entry contains an invalid lastmod timestamp")
            manifest[identifier] = (canonical, modified)
        return manifest

    def _parse_listing(self, html: str) -> tuple[int, int, list[dict[str, str]]]:
        soup = BeautifulSoup(html, "html.parser")
        wrapper = soup.select_one(".iCIMS_MainWrapper.iCIMS_ListingsPage")
        cards = soup.select("ul.iCIMS_JobsTable > li.iCIMS_JobCardItem")
        headings = soup.select("h2.iCIMS_SubHeader_Jobs")
        matches = []
        for heading in headings:
            match = _PAGE_HEADING.fullmatch(clean_text(heading.get_text(" ")))
            if match:
                matches.append(match)
        if wrapper is None or len(matches) != 1:
            raise ValueError("listing must contain one iCIMS page-count heading")
        current, total = map(int, matches[0].groups())
        summaries: list[dict[str, str]] = []
        for card in cards:
            anchors = card.select(".title a.iCIMS_Anchor[href]")
            titles = card.select(".title h3")
            if len(anchors) != 1 or len(titles) != 1:
                raise ValueError("iCIMS job card must contain one title link")
            href = str(anchors[0].get("href") or "")
            identifier, canonical = self._job_identity(href)
            title = clean_text(titles[0].get_text(" "))
            if not title:
                raise ValueError("iCIMS job card is missing a title")
            summaries.append({"id": identifier, "title": title, "url": canonical})
        return current, total, summaries

    def _job_identity(self, url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("iCIMS job URL has an invalid port") from error
        match = _JOB_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").casefold().rstrip(".") != self.icims.host
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.fragment
            or match is None
        ):
            raise ValueError("iCIMS job URL must remain on the exact source host")
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if set(query) - {"in_iframe"} or any(len(v) != 1 for v in query.values()):
            raise ValueError("iCIMS job URL contains unsupported query state")
        if "in_iframe" in query and query["in_iframe"] != ["1"]:
            raise ValueError("iCIMS job iframe flag must be in_iframe=1")
        canonical = f"https://{self.icims.host}{parsed.path.rstrip('/')}"
        return match.group(1), canonical

    def _detail_client(self) -> JsonHttpClient:
        client = getattr(self._detail_clients, "client", None)
        if client is None:
            client = self._detail_client_factory()
            self._detail_clients.client = client
        return client

    def _fetch_details(
        self, summaries: list[dict[str, str]], page: int
    ) -> list[tuple[ConnectorJob | None, ConnectorError | None]]:
        with ThreadPoolExecutor(max_workers=self.detail_concurrency) as executor:
            return list(executor.map(lambda summary: self._fetch_detail(summary, page), summaries))

    def _fetch_detail(
        self, summary: dict[str, str], page: int
    ) -> tuple[ConnectorJob | None, ConnectorError | None]:
        try:
            html = self._detail_client().get_text(f"{summary['url']}?in_iframe=1")
            return self._parse_detail(html, summary), None
        except HttpFailure as error:
            return None, ConnectorError(
                error.code, error.message, error.retryable, summary["id"], page
            )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            return None, record_error(error, external_id=summary["id"])

    def _parse_detail(self, html: str, summary: dict[str, str]) -> ConnectorJob:
        soup = BeautifulSoup(html, "html.parser")
        if soup.select_one(".iCIMS_MainWrapper.iCIMS_JobPage") is None:
            raise ValueError("detail is not an iCIMS job page")
        objects = []
        for script in soup.select('script[type="application/ld+json"]'):
            value = json.loads(script.get_text())
            if isinstance(value, dict) and value.get("@type") == "JobPosting":
                objects.append(value)
        if len(objects) != 1:
            raise ValueError("detail must contain exactly one iCIMS JobPosting object")
        payload = objects[0]
        identifier, canonical = self._job_identity(clean_text(payload.get("url")))
        title = clean_text(payload.get("title"))
        if identifier != summary["id"] or canonical != summary["url"]:
            raise ValueError("detail job identity did not match the listing")
        if title != summary["title"]:
            raise ValueError("detail title did not match the listing")
        opened = normalize_timestamp(payload.get("datePosted"))
        if opened is None:
            raise ValueError("detail is missing a valid native posting date")
        location, metadata = self._locations(payload.get("jobLocation"))
        return ConnectorJob(
            self.source,
            identifier,
            title,
            canonical,
            location=location,
            description=clean_html(payload.get("description")),
            source_opened_at=opened,
            source_updated_at=summary.get("updated") or None,
            metadata={
                "source_opened_at_field": "JobPosting.datePosted",
                "source_opened_at_available": True,
                "employment_type": payload.get("employmentType"),
                "occupational_category": payload.get("occupationalCategory"),
                "additional_locations": metadata,
            },
        )

    @staticmethod
    def _locations(value: object) -> tuple[str, list[dict[str, str]]]:
        entries = value if isinstance(value, list) else [value]
        locations: list[str] = []
        metadata: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("address"), dict):
                raise TypeError("jobLocation entries must contain an address object")
            address = entry["address"]
            city = clean_text(address.get("addressLocality"))
            region = clean_text(address.get("addressRegion"))
            country = clean_text(address.get("addressCountry"))
            location = ", ".join(part for part in (city, region, country) if part)
            if not location:
                raise ValueError("jobLocation address is empty")
            if location.casefold() not in {item.casefold() for item in locations}:
                locations.append(location)
                metadata.append(
                    {"location": location, "city": city, "region": region, "country": country}
                )
        return " | ".join(locations), metadata
