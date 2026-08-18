"""Connector for Greenhouse's public Job Board API."""

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


class GreenhouseConnector:
    source = "greenhouse"

    def __init__(self, board_token: str, *, client: JsonHttpClient | None = None) -> None:
        self.board_token = clean_text(board_token)
        if not self.board_token:
            raise ValueError("board_token is required")
        self.client = client or JsonHttpClient()

    def fetch(self) -> ConnectorResult:
        url = f"https://boards-api.greenhouse.io/v1/boards/{quote(self.board_token, safe='')}/jobs"
        try:
            payload = self.client.get_json(url, params={"content": "true"})
        except HttpFailure as error:
            return ConnectorResult(
                self.source,
                self.board_token,
                (),
                False,
                (http_error(error, page=1),),
                0,
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            error = record_error(ValueError("payload must contain a jobs list"))
            return ConnectorResult(self.source, self.board_token, (), False, (error,), 1)

        jobs: list[ConnectorJob] = []
        errors = []
        seen_ids: set[str] = set()
        for item in payload["jobs"]:
            external_id = clean_text(item.get("id")) if isinstance(item, dict) else None
            try:
                if not isinstance(item, dict):
                    raise TypeError("job entry must be an object")
                location = item.get("location") or {}
                job = ConnectorJob(
                    source=self.source,
                    external_job_id=require_text(item, "id"),
                    title=require_text(item, "title"),
                    url=require_public_url(item.get("absolute_url")),
                    location=(
                        clean_text(location.get("name")) if isinstance(location, dict) else ""
                    ),
                    description=clean_html(item.get("content")),
                    source_updated_at=normalize_timestamp(item.get("updated_at")),
                    metadata={
                        # Greenhouse's public board API exposes an update date,
                        # not a reliable opening/publish date.
                        "source_opened_at_field": None,
                        "source_opened_at_available": False,
                        "internal_job_id": item.get("internal_job_id"),
                        "requisition_id": item.get("requisition_id"),
                        "language": item.get("language"),
                    },
                )
                if job.external_job_id in seen_ids:
                    raise ValueError(f"duplicate native job ID: {job.external_job_id}")
                seen_ids.add(job.external_job_id)
                jobs.append(job)
            except (TypeError, ValueError) as error:
                errors.append(record_error(error, external_id=external_id))
        meta = payload.get("meta") or {}
        if isinstance(meta, dict) and meta.get("total") is not None:
            try:
                expected_total = int(str(meta["total"]))
            except ValueError:
                errors.append(record_error(ValueError("meta.total must be an integer")))
            else:
                if expected_total != len(payload["jobs"]):
                    errors.append(record_error(ValueError("jobs list does not match meta.total")))
        return ConnectorResult(
            self.source, self.board_token, tuple(jobs), not errors, tuple(errors), 1
        )
