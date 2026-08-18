"""Connector for Lever's public Postings API."""

from __future__ import annotations

from urllib.parse import quote

from fortune_intel.connectors.common import (
    clean_html,
    clean_text,
    http_error,
    normalize_timestamp,
    record_error,
    require_public_url,
    require_text,
)
from fortune_intel.connectors.http import HttpFailure, JsonHttpClient
from fortune_intel.connectors.models import ConnectorJob, ConnectorResult


class LeverConnector:
    source = "lever"

    def __init__(
        self,
        site: str,
        *,
        region: str = "global",
        page_size: int = 100,
        max_pages: int = 100,
        client: JsonHttpClient | None = None,
    ) -> None:
        self.site = clean_text(site)
        if not self.site:
            raise ValueError("site is required")
        if region not in {"global", "eu"}:
            raise ValueError("region must be global or eu")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        self.region = region
        self.page_size = page_size
        self.max_pages = max(1, max_pages)
        self.client = client or JsonHttpClient()

    def fetch(self) -> ConnectorResult:
        host = "api.eu.lever.co" if self.region == "eu" else "api.lever.co"
        url = f"https://{host}/v0/postings/{quote(self.site, safe='')}"
        jobs: list[ConnectorJob] = []
        errors = []
        seen_ids: set[str] = set()
        skip = 0
        pages = 0
        while pages < self.max_pages:
            page_number = pages + 1
            try:
                payload = self.client.get_json(
                    url,
                    params={"mode": "json", "skip": skip, "limit": self.page_size},
                )
            except HttpFailure as error:
                errors.append(http_error(error, page=page_number))
                return ConnectorResult(
                    self.source, self.site, tuple(jobs), False, tuple(errors), pages
                )
            pages += 1
            if not isinstance(payload, list):
                errors.append(record_error(ValueError("payload must be a job list")))
                return ConnectorResult(
                    self.source, self.site, tuple(jobs), False, tuple(errors), pages
                )

            for item in payload:
                external_id = clean_text(item.get("id")) if isinstance(item, dict) else None
                try:
                    job = self._parse_job(item)
                    if job.external_job_id in seen_ids:
                        raise ValueError(f"duplicate native job ID: {job.external_job_id}")
                    seen_ids.add(job.external_job_id)
                    jobs.append(job)
                except (TypeError, ValueError) as error:
                    errors.append(record_error(error, external_id=external_id))
            if len(payload) < self.page_size:
                return ConnectorResult(
                    self.source,
                    self.site,
                    tuple(jobs),
                    not errors,
                    tuple(errors),
                    pages,
                )
            skip += len(payload)

        errors.append(record_error(ValueError(f"pagination exceeded {self.max_pages} pages")))
        return ConnectorResult(self.source, self.site, tuple(jobs), False, tuple(errors), pages)

    def _parse_job(self, item: object) -> ConnectorJob:
        if not isinstance(item, dict):
            raise TypeError("job entry must be an object")
        categories = item.get("categories") or {}
        if not isinstance(categories, dict):
            categories = {}
        description = clean_text(item.get("descriptionPlain"))
        if not description:
            description = clean_html(item.get("description"))
        source_opened_at = normalize_timestamp(item.get("createdAt"))
        return ConnectorJob(
            source=self.source,
            external_job_id=require_text(item, "id"),
            title=require_text(item, "text"),
            url=require_public_url(item.get("hostedUrl")),
            location=clean_text(categories.get("location")),
            description=description,
            source_opened_at=source_opened_at,
            metadata={
                "source_opened_at_field": "createdAt",
                "source_opened_at_available": source_opened_at is not None,
                "all_locations": categories.get("allLocations") or [],
                "commitment": categories.get("commitment"),
                "department": categories.get("department"),
                "team": categories.get("team"),
                "workplace_type": item.get("workplaceType"),
                "apply_url": item.get("applyUrl"),
            },
        )
