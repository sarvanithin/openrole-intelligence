"""Connector for SmartRecruiters' public Posting API."""

from __future__ import annotations

from urllib.parse import quote

from fortune_intel.connectors.common import (
    clean_html,
    clean_text,
    http_error,
    location_from_parts,
    normalize_timestamp,
    record_error,
    require_public_url,
    require_text,
)
from fortune_intel.connectors.http import HttpFailure, JsonHttpClient
from fortune_intel.connectors.models import (
    ConnectorError,
    ConnectorJob,
    ConnectorResult,
)


class SmartRecruitersConnector:
    source = "smartrecruiters"

    def __init__(
        self,
        company_identifier: str,
        *,
        page_size: int = 100,
        max_pages: int = 100,
        client: JsonHttpClient | None = None,
    ) -> None:
        self.company_identifier = clean_text(company_identifier)
        if not self.company_identifier:
            raise ValueError("company_identifier is required")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        self.page_size = page_size
        self.max_pages = max(1, max_pages)
        self.client = client or JsonHttpClient()

    def fetch(self) -> ConnectorResult:
        encoded_company = quote(self.company_identifier, safe="")
        base = f"https://api.smartrecruiters.com/v1/companies/{encoded_company}/postings"
        jobs: list[ConnectorJob] = []
        errors = []
        seen_ids: set[str] = set()
        offset = 0
        pages = 0
        while pages < self.max_pages:
            page_number = pages + 1
            try:
                payload = self.client.get_json(
                    base, params={"limit": self.page_size, "offset": offset}
                )
            except HttpFailure as error:
                errors.append(http_error(error, page=page_number))
                return ConnectorResult(
                    self.source,
                    self.company_identifier,
                    tuple(jobs),
                    False,
                    tuple(errors),
                    pages,
                )
            pages += 1
            if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
                errors.append(record_error(ValueError("payload must contain a content list")))
                return ConnectorResult(
                    self.source,
                    self.company_identifier,
                    tuple(jobs),
                    False,
                    tuple(errors),
                    pages,
                )
            content = payload["content"]
            for summary in content:
                external_id = clean_text(summary.get("id")) if isinstance(summary, dict) else None
                if not external_id:
                    errors.append(record_error(ValueError("missing required field: id")))
                    continue
                if external_id in seen_ids:
                    errors.append(
                        record_error(
                            ValueError(f"duplicate native job ID: {external_id}"),
                            external_id=external_id,
                        )
                    )
                    continue
                seen_ids.add(external_id)
                detail_url = f"{base}/{quote(external_id, safe='')}"
                try:
                    detail = self.client.get_json(detail_url)
                except HttpFailure as error:
                    errors.append(
                        ConnectorError(
                            code=error.code,
                            message=error.message,
                            retryable=error.retryable,
                            external_job_id=external_id,
                            page=page_number,
                        )
                    )
                    continue
                try:
                    jobs.append(self._parse_job(summary, detail))
                except (TypeError, ValueError) as error:
                    errors.append(record_error(error, external_id=external_id))

            total = self._integer(payload.get("totalFound"), default=offset + len(content))
            offset += len(content)
            if offset >= total:
                return ConnectorResult(
                    self.source,
                    self.company_identifier,
                    tuple(jobs),
                    not errors,
                    tuple(errors),
                    pages,
                )
            if not content:
                errors.append(record_error(ValueError("pagination stopped before totalFound")))
                return ConnectorResult(
                    self.source,
                    self.company_identifier,
                    tuple(jobs),
                    False,
                    tuple(errors),
                    pages,
                )

        errors.append(record_error(ValueError(f"pagination exceeded {self.max_pages} pages")))
        return ConnectorResult(
            self.source,
            self.company_identifier,
            tuple(jobs),
            False,
            tuple(errors),
            pages,
        )

    def _parse_job(self, summary: object, detail: object) -> ConnectorJob:
        if not isinstance(summary, dict) or not isinstance(detail, dict):
            raise TypeError("job summary and detail must be objects")
        location = detail.get("location") or summary.get("location") or {}
        if not isinstance(location, dict):
            location = {}
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        if not isinstance(sections, dict):
            sections = {}
        description_parts = []
        for name in ("jobDescription", "qualifications", "additionalInformation"):
            section = sections.get(name) or {}
            if isinstance(section, dict) and section.get("text"):
                description_parts.append(clean_html(section["text"]))
        url = detail.get("jobAdUrl") or detail.get("applyUrl")
        source_opened_at = normalize_timestamp(
            detail.get("releasedDate") or summary.get("releasedDate")
        )
        return ConnectorJob(
            source=self.source,
            external_job_id=require_text(detail, "id"),
            title=require_text(detail, "name"),
            url=require_public_url(url),
            location=location_from_parts(
                location.get("city"),
                location.get("region"),
                location.get("country"),
                remote=bool(location.get("remote")),
            ),
            description="\n\n".join(part for part in description_parts if part),
            source_opened_at=source_opened_at,
            source_updated_at=normalize_timestamp(detail.get("lastActivityOn")),
            metadata={
                "source_opened_at_field": "releasedDate",
                "source_opened_at_available": source_opened_at is not None,
                "uuid": detail.get("uuid"),
                "job_id": detail.get("jobId"),
                "job_ad_id": detail.get("jobAdId"),
                "apply_url": detail.get("applyUrl"),
                "department": self._label(detail.get("department")),
                "employment_type": self._label(detail.get("typeOfEmployment")),
                "experience_level": self._label(detail.get("experienceLevel")),
                "location_country": location.get("country"),
                "location_region": location.get("region"),
            },
        )

    @staticmethod
    def _integer(value: object, *, default: int) -> int:
        try:
            return max(0, int(str(value)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _label(value: object) -> str | None:
        return clean_text(value.get("label")) if isinstance(value, dict) else None
