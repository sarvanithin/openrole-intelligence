"""Connector for the public, server-rendered Apple U.S. job search."""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from fortune_intel.connectors.common import (
    clean_text,
    http_error,
    normalize_timestamp,
    record_error,
    require_text,
)
from fortune_intel.connectors.http import HttpFailure, JsonHttpClient
from fortune_intel.connectors.models import ConnectorJob, ConnectorResult

_SEARCH_URL = "https://jobs.apple.com/en-us/search"
_HYDRATION = re.compile(
    r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\(("(?:\\.|[^"\\])*")\)',
    re.DOTALL,
)
_USA_LOCATION = "united-states-USA"


class AppleJobsConnector:
    """Read every page of Apple's public, U.S.-filtered search results.

    Apple's first-party search HTML includes server-rendered hydration data.  It
    contains the page's native IDs, complete public summary, source posting
    timestamp, and locations, so this connector does not rely on browser
    automation or an undocumented authenticated API.
    """

    source = "apple_jobs"

    def __init__(
        self,
        board: str = "us",
        *,
        max_pages: int = 300,
        client: JsonHttpClient | None = None,
    ) -> None:
        if clean_text(board).casefold() != "us":
            raise ValueError("Apple Jobs supports only the public U.S. board")
        if not 1 <= max_pages <= 500:
            raise ValueError("max_pages must be between 1 and 500")
        self.board = "us"
        self.max_pages = max_pages
        self.client = client or JsonHttpClient()

    def fetch(self) -> ConnectorResult:
        jobs: list[ConnectorJob] = []
        errors = []
        seen: set[str] = set()
        records_seen = 0
        expected_total: int | None = None
        expected_pages: int | None = None
        for page in range(1, self.max_pages + 1):
            try:
                payload = self._page_payload(page)
                page_jobs, total = self._page(payload)
                if expected_total is None:
                    expected_total = total
                    expected_pages = (total + 19) // 20
                elif total != expected_total:
                    raise ValueError("server-reported job total changed during pagination")
                if expected_pages is None or page > expected_pages:
                    raise ValueError("search returned a page after its declared total")
                if not page_jobs:
                    if total == 0 and page == 1:
                        return ConnectorResult(self.source, self.board, (), True, (), 1)
                    raise ValueError("search ended before its declared total")
                if len(page_jobs) > 20:
                    raise ValueError("search returned more than the public page size")
            except HttpFailure as error:
                errors.append(http_error(error, page=page))
                return ConnectorResult(
                    self.source, self.board, tuple(jobs), False, tuple(errors), page - 1
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                errors.append(record_error(error))
                return ConnectorResult(
                    self.source, self.board, tuple(jobs), False, tuple(errors), page
                )

            for item in page_jobs:
                external_id = clean_text(item.get("positionId")) if isinstance(item, dict) else None
                try:
                    job = self._parse_job(item)
                    if job.external_job_id in seen:
                        raise ValueError(f"duplicate native job ID: {job.external_job_id}")
                    seen.add(job.external_job_id)
                    jobs.append(job)
                except (TypeError, ValueError) as error:
                    errors.append(record_error(error, external_id=external_id))
            records_seen += len(page_jobs)
            if expected_pages == page:
                if records_seen != expected_total:
                    errors.append(
                        record_error(ValueError("paginated job count did not match declared total"))
                    )
                return ConnectorResult(
                    self.source, self.board, tuple(jobs), not errors, tuple(errors), page
                )

        errors.append(record_error(ValueError(f"pagination exceeded {self.max_pages} pages")))
        return ConnectorResult(
            self.source, self.board, tuple(jobs), False, tuple(errors), self.max_pages
        )

    def _page_payload(self, page: int) -> object:
        params = {"location": _USA_LOCATION}
        if page > 1:
            params["page"] = str(page)
        html = self.client.get_text(f"{_SEARCH_URL}?{urlencode(params)}", max_bytes=5_000_000)
        match = _HYDRATION.search(html)
        if match is None:
            raise ValueError("Apple search page is missing hydration data")
        payload = json.loads(json.loads(match.group(1)))
        if not isinstance(payload, dict):
            raise TypeError("Apple hydration payload must be an object")
        return payload

    @staticmethod
    def _page(payload: object) -> tuple[list[dict[str, object]], int]:
        if not isinstance(payload, dict):
            raise TypeError("Apple hydration payload must be an object")
        loader_data = payload.get("loaderData")
        if not isinstance(loader_data, dict):
            raise TypeError("Apple hydration payload is missing loader data")
        search = loader_data.get("search")
        if not isinstance(search, dict):
            raise TypeError("Apple hydration payload is missing search data")
        total = search.get("totalRecords")
        records = search.get("searchResults")
        if not isinstance(total, int) or total < 0:
            raise ValueError("Apple search total must be a non-negative integer")
        if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
            raise TypeError("Apple search results must be an object list")
        return records, total

    def _parse_job(self, item: object) -> ConnectorJob:
        if not isinstance(item, dict):
            raise TypeError("Apple search job must be an object")
        position_id = require_text(item, "positionId")
        title = require_text(item, "postingTitle")
        locations = item.get("locations")
        if (
            not isinstance(locations, list)
            or not locations
            or not all(isinstance(value, dict) for value in locations)
        ):
            raise ValueError("Apple job must contain one or more locations")
        rendered_locations = []
        is_us = False
        for location in locations:
            country_id = clean_text(location.get("countryID")).casefold()
            country = clean_text(location.get("countryName"))
            if country_id == "iso-country-usa" or country.casefold().startswith("united states"):
                is_us = True
            rendered = ", ".join(
                part
                for part in (
                    clean_text(location.get("city")),
                    clean_text(location.get("stateProvince")),
                    country,
                )
                if part
            )
            if rendered and rendered.casefold() not in {
                value.casefold() for value in rendered_locations
            }:
                rendered_locations.append(rendered)
        if not is_us:
            raise ValueError("U.S.-filtered Apple search returned a non-U.S. job")
        # Retail roles can be emitted once per store while sharing a requisition
        # ID.  The public location ID makes each rendered card stable and avoids
        # silently discarding legitimate location-specific openings.
        location_ids = sorted(require_text(location, "postLocationId") for location in locations)
        identifier = f"{position_id}:{'|'.join(location_ids)}"
        opened_at = normalize_timestamp(item.get("postDateInGMT"))
        if opened_at is None:
            raise ValueError("Apple job is missing a valid native posting timestamp")
        slug = clean_text(item.get("transformedPostingTitle"))
        suffix = f"/{slug}" if slug else ""
        return ConnectorJob(
            self.source,
            identifier,
            title,
            f"https://jobs.apple.com/en-us/details/{position_id}{suffix}",
            location=" | ".join(rendered_locations),
            description=clean_text(item.get("jobSummary")),
            source_opened_at=opened_at,
            metadata={
                "source_opened_at_field": "postDateInGMT",
                "source_opened_at_available": True,
                "apple_posting_date": clean_text(item.get("postingDate")),
                "job_type": clean_text(item.get("type")),
                "team": item.get("team") or {},
                "locations": locations,
            },
        )
