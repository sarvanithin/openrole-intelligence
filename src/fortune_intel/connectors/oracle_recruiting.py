"""Deterministic connector for public Oracle Recruiting Candidate Experience sites."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from fortune_intel.connectors.common import (
    clean_html,
    clean_text,
    http_error,
    normalize_timestamp,
    record_error,
    require_text,
)
from fortune_intel.connectors.http import HttpFailure, JsonHttpClient
from fortune_intel.connectors.models import ConnectorError, ConnectorJob, ConnectorResult

_ORACLE_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+oraclecloud\.com$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LANGUAGE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
_SOURCE_SEPARATOR = "|"
_LIST_RESOURCE = "recruitingCEJobRequisitions"
_DETAIL_RESOURCE = "recruitingCEJobRequisitionDetails"


@dataclass(frozen=True, slots=True)
class OracleRecruitingSource:
    """A fixed Oracle SaaS host, locale, and external career-site number."""

    host: str
    language: str
    site: str

    @property
    def key(self) -> str:
        return _SOURCE_SEPARATOR.join((self.host, self.language, self.site))

    @property
    def public_base_url(self) -> str:
        language = quote(self.language, safe="")
        site = quote(self.site, safe="")
        return f"https://{self.host}/hcmUI/CandidateExperience/{language}/sites/{site}"

    @property
    def api_base_url(self) -> str:
        return f"https://{self.host}/hcmRestApi/resources/latest"


def oracle_recruiting_source(
    host: str,
    language: str,
    site: str,
) -> OracleRecruitingSource:
    """Validate a source derived from an explicit public Candidate Experience URL."""

    normalized_host = host.strip().casefold().rstrip(".")
    raw_language = language.strip().replace("_", "-")
    language_parts = raw_language.split("-", 1)
    normalized_language = language_parts[0].casefold()
    if len(language_parts) == 2:
        normalized_language += f"-{language_parts[1].upper()}"
    normalized_site = site.strip()
    if len(normalized_host) > 253 or _ORACLE_HOST.fullmatch(normalized_host) is None:
        raise ValueError("host must be an Oracle Cloud HCM tenant host")
    if _LANGUAGE.fullmatch(normalized_language) is None:
        raise ValueError("language must be a safe Candidate Experience locale")
    if _IDENTIFIER.fullmatch(normalized_site) is None:
        raise ValueError("site must be a safe Oracle career-site identifier")
    return OracleRecruitingSource(normalized_host, normalized_language, normalized_site)


def parse_oracle_recruiting_source_key(value: str) -> OracleRecruitingSource:
    parts = value.split(_SOURCE_SEPARATOR)
    if len(parts) != 3:
        raise ValueError("Oracle Recruiting source key must contain host, language, and site")
    return oracle_recruiting_source(*parts)


class OracleRecruitingConnector:
    """Fetch complete public manifests and exact employer-published opening dates."""

    source = "oracle_recruiting"

    def __init__(
        self,
        source_key: str,
        *,
        page_size: int = 25,
        max_pages: int = 500,
        client: JsonHttpClient | None = None,
        detail_concurrency: int = 4,
        detail_client_factory: Callable[[], JsonHttpClient] | None = None,
    ) -> None:
        self.oracle = parse_oracle_recruiting_source_key(source_key)
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if not 1 <= detail_concurrency <= 8:
            raise ValueError("detail_concurrency must be between 1 and 8")
        self.page_size = page_size
        self.max_pages = max(1, max_pages)
        self.client = client or JsonHttpClient()
        self.detail_concurrency = detail_concurrency
        self._detail_client_factory = detail_client_factory or (
            JsonHttpClient if client is None else lambda: self.client
        )
        self._detail_clients = threading.local()

    def fetch(self) -> ConnectorResult:
        jobs: list[ConnectorJob] = []
        errors: list[ConnectorError] = []
        seen_ids: set[str] = set()
        expected_total: int | None = None
        offset = 0
        pages = 0

        while pages < self.max_pages:
            page_number = pages + 1
            try:
                payload = self.client.get_json(self._list_url(offset))
            except HttpFailure as error:
                errors.append(http_error(error, page=page_number))
                return self._result(jobs, errors, pages)
            pages += 1
            try:
                total, summaries = self._parse_page(payload, offset)
            except (TypeError, ValueError) as error:
                errors.append(record_error(error))
                return self._result(jobs, errors, pages)

            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                errors.append(
                    record_error(
                        ValueError(
                            f"job total changed during pagination: {expected_total} to {total}"
                        )
                    )
                )
                return self._result(jobs, errors, pages)
            if not summaries and offset < expected_total:
                errors.append(record_error(ValueError("pagination stopped before total")))
                return self._result(jobs, errors, pages)

            for job, error in self._fetch_details(summaries, page_number):
                if error is not None:
                    errors.append(error)
                    continue
                assert job is not None
                if job.external_job_id in seen_ids:
                    errors.append(
                        record_error(ValueError(f"duplicate native job ID: {job.external_job_id}"))
                    )
                    continue
                seen_ids.add(job.external_job_id)
                jobs.append(job)

            offset += len(summaries)
            if offset >= expected_total:
                if offset != expected_total:
                    errors.append(
                        record_error(
                            ValueError(
                                f"pagination returned {offset} summaries for total {expected_total}"
                            )
                        )
                    )
                return ConnectorResult(
                    self.source,
                    self.oracle.key,
                    tuple(jobs),
                    not errors and len(jobs) == expected_total,
                    tuple(errors),
                    pages,
                )

        errors.append(record_error(ValueError(f"pagination exceeded {self.max_pages} pages")))
        return self._result(jobs, errors, pages)

    def _result(
        self,
        jobs: list[ConnectorJob],
        errors: list[ConnectorError],
        pages: int,
    ) -> ConnectorResult:
        return ConnectorResult(
            self.source,
            self.oracle.key,
            tuple(jobs),
            False,
            tuple(errors),
            pages,
        )

    def _list_url(self, offset: int) -> str:
        finder = f"findReqs;siteNumber={self.oracle.site},limit={self.page_size},offset={offset}"
        return self._api_url(
            _LIST_RESOURCE,
            {
                "onlyData": "true",
                "expand": (
                    "requisitionList.workLocation,requisitionList.otherWorkLocations,"
                    "requisitionList.secondaryLocations"
                ),
                "finder": finder,
            },
        )

    def _detail_url(self, identifier: str) -> str:
        finder = f"ById;Id={identifier},siteNumber={self.oracle.site}"
        return self._api_url(
            _DETAIL_RESOURCE,
            {
                "onlyData": "true",
                "expand": "workLocation,otherWorkLocations,secondaryLocations",
                "finder": finder,
            },
        )

    def _api_url(self, resource: str, params: dict[str, str]) -> str:
        query = urlencode(params, safe=",;=")
        return f"{self.oracle.api_base_url}/{resource}?{query}"

    def _parse_page(
        self,
        payload: object,
        requested_offset: int,
    ) -> tuple[int, list[dict[str, object]]]:
        search = self._single_item(payload, "job-list")
        if clean_text(search.get("SiteNumber")) != self.oracle.site:
            raise ValueError("job-list site number did not match the configured source")
        offset = self._nonnegative_integer(search.get("Offset"), "job-list offset")
        total = self._nonnegative_integer(search.get("TotalJobsCount"), "job-list total")
        if offset != requested_offset:
            raise ValueError(f"job-list offset was {offset}; expected {requested_offset}")
        summaries = search.get("requisitionList")
        if not isinstance(summaries, list) or not all(isinstance(item, dict) for item in summaries):
            raise TypeError("job-list payload must contain a requisitionList object list")
        return total, summaries

    @staticmethod
    def _single_item(payload: object, label: str) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise TypeError(f"{label} payload must be an object")
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise TypeError(f"{label} payload must contain exactly one item")
        return items[0]

    @staticmethod
    def _nonnegative_integer(value: object, label: str) -> int:
        if isinstance(value, bool):
            raise TypeError(f"{label} must be a non-negative integer")
        try:
            result = int(str(value))
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} must be a non-negative integer") from error
        if result < 0:
            raise ValueError(f"{label} must be a non-negative integer")
        return result

    def _detail_client(self) -> JsonHttpClient:
        client = getattr(self._detail_clients, "client", None)
        if client is None:
            client = self._detail_client_factory()
            self._detail_clients.client = client
        return client

    def _fetch_details(
        self,
        summaries: list[dict[str, object]],
        page_number: int,
    ) -> list[tuple[ConnectorJob | None, ConnectorError | None]]:
        with ThreadPoolExecutor(max_workers=self.detail_concurrency) as executor:
            return list(
                executor.map(
                    lambda summary: self._fetch_detail(summary, page_number),
                    summaries,
                )
            )

    def _fetch_detail(
        self,
        summary: dict[str, object],
        page_number: int,
    ) -> tuple[ConnectorJob | None, ConnectorError | None]:
        identifier = clean_text(summary.get("Id"))
        if _IDENTIFIER.fullmatch(identifier) is None:
            return None, record_error(ValueError("job ID must be a safe Oracle identifier"))
        try:
            payload = self._detail_client().get_json(self._detail_url(identifier))
        except HttpFailure as error:
            return None, ConnectorError(
                code=error.code,
                message=error.message,
                retryable=error.retryable,
                external_job_id=identifier,
                page=page_number,
            )
        try:
            return self._parse_job(summary, payload, identifier), None
        except (TypeError, ValueError) as error:
            return None, record_error(error)

    def _parse_job(
        self,
        summary: dict[str, object],
        payload: object,
        identifier: str,
    ) -> ConnectorJob:
        detail = self._single_item(payload, "job-detail")
        if require_text(detail, "Id") != identifier:
            raise ValueError("job-detail ID did not match the requested job")
        opened_value = detail.get("ExternalPostedStartDate") or summary.get("PostedDate")
        opened_field = (
            "ExternalPostedStartDate" if detail.get("ExternalPostedStartDate") else "PostedDate"
        )
        source_opened_at = normalize_timestamp(opened_value)
        locations = [clean_text(detail.get("PrimaryLocation") or summary.get("PrimaryLocation"))]
        for field in ("workLocation", "otherWorkLocations", "secondaryLocations"):
            values = detail.get(field) or summary.get(field) or []
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, dict):
                        locations.append(clean_text(value.get("Name")))
        location = ", ".join(dict.fromkeys(value for value in locations if value))
        return ConnectorJob(
            source=self.source,
            external_job_id=identifier,
            title=require_text(detail, "Title"),
            url=f"{self.oracle.public_base_url}/job/{quote(identifier, safe='')}",
            location=location,
            description=clean_html(
                detail.get("ExternalDescriptionStr") or detail.get("ShortDescriptionStr")
            ),
            source_opened_at=source_opened_at,
            source_updated_at=None,
            metadata={
                "source_opened_at_field": opened_field,
                "source_opened_at_available": source_opened_at is not None,
                "listing_posted_date": summary.get("PostedDate"),
                "posting_end_date": detail.get("ExternalPostedEndDate")
                or summary.get("PostingEndDate"),
                "requisition_id": detail.get("RequisitionId"),
                "primary_location_country": detail.get("PrimaryLocationCountry")
                or summary.get("PrimaryLocationCountry"),
                "job_schedule": detail.get("JobSchedule"),
                "workplace_type": detail.get("WorkplaceType"),
            },
        )
