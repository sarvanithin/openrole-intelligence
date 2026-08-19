"""Connector for Amazon Jobs' public, paginated U.S. search manifest."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urljoin

from fortune_intel.connectors.common import (
    clean_html,
    clean_text,
    http_error,
    record_error,
    require_public_url,
    require_text,
)
from fortune_intel.connectors.http import HttpFailure, JsonHttpClient
from fortune_intel.connectors.models import ConnectorJob, ConnectorResult

_SEARCH_URL = "https://www.amazon.jobs/en/search.json"
_PUBLIC_ROOT = "https://www.amazon.jobs"


class AmazonJobsConnector:
    """Read Amazon's public U.S. board without browser automation.

    The board's documented search response includes a server-reported hit count,
    paginated summaries, complete descriptions, stable requisition IDs, and the
    public job path. The connector asks for the explicit ``USA`` country facet
    and rejects any response that leaks a non-U.S. record into that manifest.
    """

    source = "amazon_jobs"

    def __init__(
        self,
        board: str = "us",
        *,
        page_size: int = 100,
        max_pages: int = 100,
        client: JsonHttpClient | None = None,
    ) -> None:
        if clean_text(board).casefold() != "us":
            raise ValueError("Amazon Jobs supports only the public U.S. board")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if not 1 <= max_pages <= 100:
            raise ValueError("max_pages must be between 1 and 100")
        self.board = "us"
        self.page_size = page_size
        self.max_pages = max_pages
        self.client = client or JsonHttpClient()

    def fetch(self) -> ConnectorResult:
        jobs: list[ConnectorJob] = []
        errors = []
        seen_ids: set[str] = set()
        expected_hits: int | None = None
        offset = 0
        pages = 0
        while pages < self.max_pages:
            page_number = pages + 1
            try:
                payload = self.client.get_json(
                    _SEARCH_URL,
                    params={"country": "USA", "offset": offset, "result_limit": self.page_size},
                )
            except HttpFailure as error:
                errors.append(http_error(error, page=page_number))
                return ConnectorResult(
                    self.source, self.board, tuple(jobs), False, tuple(errors), pages
                )
            pages += 1
            try:
                page_jobs, hits = self._page(payload)
                if expected_hits is None:
                    expected_hits = hits
                elif expected_hits != hits:
                    raise ValueError("server-reported hit count changed during pagination")
                if offset >= hits and page_jobs:
                    raise ValueError("search returned records after its declared hit count")
                if offset < hits and not page_jobs:
                    raise ValueError("search ended before its declared hit count")
                if len(page_jobs) > min(self.page_size, max(0, hits - offset)):
                    raise ValueError("search page exceeds the declared remaining hit count")
            except (TypeError, ValueError) as error:
                errors.append(record_error(error, page=page_number))
                return ConnectorResult(
                    self.source, self.board, tuple(jobs), False, tuple(errors), pages
                )

            for item in page_jobs:
                external_id = clean_text(item.get("id_icims")) if isinstance(item, dict) else None
                try:
                    job = self._parse_job(item)
                    if job.external_job_id in seen_ids:
                        raise ValueError(f"duplicate native job ID: {job.external_job_id}")
                    seen_ids.add(job.external_job_id)
                    jobs.append(job)
                except (TypeError, ValueError) as error:
                    errors.append(record_error(error, external_id=external_id))
            offset += len(page_jobs)
            if expected_hits is not None and offset == expected_hits:
                return ConnectorResult(
                    self.source, self.board, tuple(jobs), not errors, tuple(errors), pages
                )

        errors.append(record_error(ValueError(f"pagination exceeded {self.max_pages} pages")))
        return ConnectorResult(self.source, self.board, tuple(jobs), False, tuple(errors), pages)

    @staticmethod
    def _page(payload: object) -> tuple[list[dict[str, object]], int]:
        if not isinstance(payload, dict):
            raise TypeError("payload must be an object")
        hits = payload.get("hits")
        if not isinstance(hits, int) or hits < 0:
            raise ValueError("payload hits must be a non-negative integer")
        raw_jobs = payload.get("jobs")
        if not isinstance(raw_jobs, list) or not all(isinstance(item, dict) for item in raw_jobs):
            raise ValueError("payload jobs must be an object list")
        return raw_jobs, hits

    def _parse_job(self, item: object) -> ConnectorJob:
        if not isinstance(item, dict):
            raise TypeError("job entry must be an object")
        country = clean_text(item.get("country_code")).upper()
        if country != "USA":
            raise ValueError("U.S. board returned a non-U.S. job")
        job_path = require_text(item, "job_path")
        if not job_path.startswith("/"):
            raise ValueError("job_path must be an absolute Amazon Jobs path")
        opened_at = self._posted_at(item.get("posted_date"))
        return ConnectorJob(
            source=self.source,
            external_job_id=require_text(item, "id_icims"),
            title=require_text(item, "title"),
            url=require_public_url(urljoin(_PUBLIC_ROOT, job_path)),
            location=clean_text(item.get("location")),
            description=clean_html(item.get("description")),
            source_opened_at=opened_at,
            metadata={
                "source_opened_at_field": "posted_date",
                "source_opened_at_available": opened_at is not None,
                "amazon_job_uuid": clean_text(item.get("id")),
                "country_code": country,
                "locations": item.get("locations") or [],
                "job_category": item.get("job_category"),
                "job_family": item.get("job_family"),
                "job_schedule_type": item.get("job_schedule_type"),
                "apply_url": item.get("url_next_step"),
            },
        )

    @staticmethod
    def _posted_at(value: object) -> str | None:
        text = clean_text(value)
        if not text:
            return None
        try:
            return datetime.strptime(text, "%B %d, %Y").replace(tzinfo=UTC).isoformat()
        except ValueError:
            return None
