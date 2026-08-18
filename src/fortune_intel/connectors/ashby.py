"""Connector for Ashby's public Job Postings API."""

from __future__ import annotations

from urllib.parse import quote, urlsplit

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


class AshbyConnector:
    source = "ashby"

    def __init__(self, board_name: str, *, client: JsonHttpClient | None = None) -> None:
        self.board_name = clean_text(board_name)
        if not self.board_name:
            raise ValueError("board_name is required")
        self.client = client or JsonHttpClient()

    def fetch(self) -> ConnectorResult:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(self.board_name, safe='')}"
        try:
            payload = self.client.get_json(url, params={"includeCompensation": "true"})
        except HttpFailure as error:
            return ConnectorResult(
                self.source, self.board_name, (), False, (http_error(error, page=1),), 0
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            error = record_error(ValueError("payload must contain a jobs list"))
            return ConnectorResult(self.source, self.board_name, (), False, (error,), 1)

        jobs: list[ConnectorJob] = []
        errors = []
        seen_ids: set[str] = set()
        for item in payload["jobs"]:
            try:
                job = self._parse_job(item, api_version=clean_text(payload.get("apiVersion")))
                if job.external_job_id in seen_ids:
                    raise ValueError(f"duplicate job URL identity: {job.external_job_id}")
                seen_ids.add(job.external_job_id)
                jobs.append(job)
            except (TypeError, ValueError) as error:
                errors.append(record_error(error))
        return ConnectorResult(
            self.source, self.board_name, tuple(jobs), not errors, tuple(errors), 1
        )

    def _parse_job(self, item: object, *, api_version: str) -> ConnectorJob:
        if not isinstance(item, dict):
            raise TypeError("job entry must be an object")
        job_url = require_public_url(item.get("jobUrl"))
        # Ashby's documented public payload has no ID field. Its canonical jobUrl
        # ends in the native posting token, which is the strongest source identity.
        external_id = urlsplit(job_url).path.rstrip("/").rsplit("/", 1)[-1]
        if not external_id:
            raise ValueError("jobUrl does not contain a posting identity")
        description = clean_text(item.get("descriptionPlain")) or clean_html(
            item.get("descriptionHtml")
        )
        source_opened_at = normalize_timestamp(item.get("publishedAt"))
        return ConnectorJob(
            source=self.source,
            external_job_id=external_id,
            title=require_text(item, "title"),
            url=job_url,
            location=clean_text(item.get("location")),
            description=description,
            source_opened_at=source_opened_at,
            metadata={
                "source_opened_at_field": "publishedAt",
                "source_opened_at_available": source_opened_at is not None,
                "department": item.get("department"),
                "team": item.get("team"),
                "employment_type": item.get("employmentType"),
                "is_remote": item.get("isRemote"),
                "workplace_type": item.get("workplaceType"),
                "secondary_locations": item.get("secondaryLocations") or [],
                "apply_url": item.get("applyUrl"),
                "compensation": item.get("compensation"),
                "api_version": api_version,
            },
        )
