"""Connector for Workday-hosted public external career sites.

Workday documents that an external career site has a job-listing page and a
job-detail page, and that the detail can expose the posted date, requisition
number, location, and description. This connector reads those same anonymous
career-site responses; it never uses tenant web-service credentials.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

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

_MYWORKDAYJOBS_HOST_PATTERN = re.compile(
    r"^(?P<tenant>[a-z0-9](?:[a-z0-9-]{0,62}))\.wd[0-9]+\.myworkdayjobs\.com$"
)
_MYWORKDAYSITE_HOST_PATTERN = re.compile(r"^wd[0-9]+\.myworkdaysite\.com$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SOURCE_SEPARATOR = "|"
_STALE_SOURCE_STATUSES = {404, 410, 422}


@dataclass(frozen=True, slots=True)
class WorkdaySource:
    """The fixed public host, tenant, and external-site identifiers."""

    host: str
    tenant: str
    site: str

    @property
    def key(self) -> str:
        return _SOURCE_SEPARATOR.join((self.host, self.tenant, self.site))

    @property
    def uses_recruiting_path(self) -> bool:
        return _MYWORKDAYSITE_HOST_PATTERN.fullmatch(self.host) is not None

    @property
    def public_base_url(self) -> str:
        if self.uses_recruiting_path:
            tenant = quote(self.tenant, safe="")
            site = quote(self.site, safe="")
            return f"https://{self.host}/recruiting/{tenant}/{site}"
        return f"https://{self.host}/{quote(self.site, safe='')}"

    @property
    def public_job_path_prefix(self) -> str:
        site = quote(self.site, safe="")
        if self.uses_recruiting_path:
            tenant = quote(self.tenant, safe="")
            return f"/recruiting/{tenant}/{site}/job/"
        return f"/{site}/job/"

    @property
    def cxs_base_url(self) -> str:
        tenant = quote(self.tenant, safe="")
        site = quote(self.site, safe="")
        return f"https://{self.host}/wday/cxs/{tenant}/{site}"


def workday_source(host: str, tenant: str, site: str) -> WorkdaySource:
    """Validate identifiers and construct a fixed-host Workday source."""

    normalized_host = host.strip().casefold().rstrip(".")
    normalized_tenant = tenant.strip()
    normalized_site = site.strip()
    jobs_match = _MYWORKDAYJOBS_HOST_PATTERN.fullmatch(normalized_host)
    site_match = _MYWORKDAYSITE_HOST_PATTERN.fullmatch(normalized_host)
    if jobs_match is None and site_match is None:
        raise ValueError("host must be a Workday public career-site host")
    if not _IDENTIFIER_PATTERN.fullmatch(normalized_tenant):
        raise ValueError("tenant must be a safe Workday identifier")
    if not _IDENTIFIER_PATTERN.fullmatch(normalized_site):
        raise ValueError("site must be a safe Workday identifier")
    if jobs_match is not None and normalized_tenant.casefold() != jobs_match.group("tenant"):
        raise ValueError("tenant must match the Workday host tenant")
    return WorkdaySource(normalized_host, normalized_tenant, normalized_site)


def parse_workday_source_key(value: str) -> WorkdaySource:
    parts = value.split(_SOURCE_SEPARATOR)
    if len(parts) != 3:
        raise ValueError("Workday source key must contain host, tenant, and site")
    return workday_source(*parts)


class WorkdayConnector:
    source = "workday"

    def __init__(
        self,
        source_key: str,
        *,
        page_size: int = 20,
        max_pages: int = 500,
        client: JsonHttpClient | None = None,
        detail_concurrency: int = 4,
        detail_client_factory: Callable[[], JsonHttpClient] | None = None,
    ) -> None:
        self.workday = parse_workday_source_key(source_key)
        if not 1 <= page_size <= 20:
            raise ValueError("page_size must be between 1 and 20")
        self.page_size = page_size
        self.max_pages = max(1, max_pages)
        self.client = client or JsonHttpClient()
        if not 1 <= detail_concurrency <= 8:
            raise ValueError("detail_concurrency must be between 1 and 8")
        self.detail_concurrency = detail_concurrency
        self._detail_client_factory = detail_client_factory or (
            JsonHttpClient if client is None else lambda: self.client
        )
        self._detail_clients = threading.local()

    def fetch(self) -> ConnectorResult:
        jobs: list[ConnectorJob] = []
        errors: list[ConnectorError] = []
        seen_ids: set[str] = set()
        listing: list[tuple[dict[str, object], int]] = []
        non_actionable_placeholders = 0
        offset = 0
        pages = 0
        expected_total: int | None = None
        list_url = f"{self.workday.cxs_base_url}/jobs"

        while pages < self.max_pages:
            page_number = pages + 1
            try:
                payload = self.client.post_json(
                    list_url,
                    json_body={
                        "appliedFacets": {},
                        "limit": self.page_size,
                        "offset": offset,
                        "searchText": "",
                    },
                )
            except HttpFailure as error:
                if error.status_code in _STALE_SOURCE_STATUSES:
                    errors.append(
                        ConnectorError(
                            code="source_configuration_error",
                            message=(
                                "Configured Workday tenant/site listing endpoint returned "
                                f"HTTP {error.status_code}; re-verify the exact external "
                                "career site from an official company page"
                            ),
                            retryable=False,
                            page=page_number,
                        )
                    )
                else:
                    errors.append(http_error(error, page=page_number))
                return self._result(jobs, errors, pages)
            pages += 1
            try:
                total, postings = self._parse_page(payload)
            except (TypeError, ValueError) as error:
                errors.append(record_error(error))
                return self._result(jobs, errors, pages)

            if expected_total is None:
                expected_total = total
            elif total not in {0, expected_total}:
                errors.append(
                    record_error(
                        ValueError(
                            f"job total changed during pagination: {expected_total} to {total}"
                        )
                    )
                )
                return self._result(jobs, errors, pages)

            if not postings and offset < expected_total:
                errors.append(record_error(ValueError("pagination stopped before total")))
                return self._result(jobs, errors, pages)

            for posting in postings:
                if self._is_non_actionable_placeholder(posting):
                    non_actionable_placeholders += 1
                else:
                    listing.append((posting, page_number))

            offset += len(postings)
            if offset >= expected_total:
                if offset != expected_total:
                    errors.append(
                        record_error(
                            ValueError(
                                f"pagination returned {offset} summaries for total {expected_total}"
                            )
                        )
                    )
                break
        else:
            errors.append(record_error(ValueError(f"pagination exceeded {self.max_pages} pages")))

        if errors:
            return self._result(jobs, errors, pages)
        for job, error in self._fetch_posting_details(listing):
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
        return ConnectorResult(
            self.source,
            self.workday.key,
            tuple(jobs),
            not errors and len(jobs) + non_actionable_placeholders == expected_total,
            tuple(errors),
            pages,
        )

    def _result(
        self,
        jobs: list[ConnectorJob],
        errors: list[ConnectorError],
        pages: int,
    ) -> ConnectorResult:
        return ConnectorResult(
            self.source,
            self.workday.key,
            tuple(jobs),
            False,
            tuple(errors),
            pages,
        )

    @staticmethod
    def _parse_page(payload: object) -> tuple[int, list[dict[str, object]]]:
        if not isinstance(payload, dict):
            raise TypeError("job-list payload must be an object")
        postings = payload.get("jobPostings")
        if not isinstance(postings, list) or not all(isinstance(item, dict) for item in postings):
            raise TypeError("job-list payload must contain a jobPostings object list")
        total_value = payload.get("total")
        if isinstance(total_value, bool):
            raise TypeError("job-list total must be a non-negative integer")
        try:
            total = int(str(total_value))
        except (TypeError, ValueError) as error:
            raise ValueError("job-list total must be a non-negative integer") from error
        if total < 0:
            raise ValueError("job-list total must be a non-negative integer")
        return total, postings

    @staticmethod
    def _detail_path(value: str) -> str:
        parsed = urlsplit(value)
        if (
            not value.startswith("/job/")
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or any(segment in {".", ".."} for segment in parsed.path.split("/"))
        ):
            raise ValueError("externalPath must be a relative Workday /job/ path")
        return parsed.path

    @staticmethod
    def _is_non_actionable_placeholder(summary: dict[str, object]) -> bool:
        """Recognize Workday's requisition-only rows without inventing a job URL."""

        if set(summary) != {"bulletFields"}:
            return False
        bullet_fields = summary.get("bulletFields")
        return bool(
            isinstance(bullet_fields, list)
            and bullet_fields
            and all(clean_text(value) for value in bullet_fields)
        )

    def _detail_client(self) -> JsonHttpClient:
        client = getattr(self._detail_clients, "client", None)
        if client is None:
            client = self._detail_client_factory()
            self._detail_clients.client = client
        return client

    def _fetch_posting_details(
        self,
        postings: list[tuple[dict[str, object], int]],
    ) -> list[tuple[ConnectorJob | None, ConnectorError | None]]:
        with ThreadPoolExecutor(max_workers=self.detail_concurrency) as executor:
            return list(
                executor.map(
                    lambda item: self._fetch_posting_detail(*item),
                    postings,
                )
            )

    def _fetch_posting_detail(
        self,
        summary: dict[str, object],
        page_number: int,
    ) -> tuple[ConnectorJob | None, ConnectorError | None]:
        path = clean_text(summary.get("externalPath"))
        try:
            detail_path = self._detail_path(path)
        except ValueError as error:
            return None, record_error(error)
        detail_url = f"{self.workday.cxs_base_url}{detail_path}"
        try:
            detail = self._detail_client().get_json(detail_url)
        except HttpFailure as error:
            return None, ConnectorError(
                code=error.code,
                message=error.message,
                retryable=error.retryable,
                page=page_number,
            )
        try:
            return self._parse_job(summary, detail), None
        except (TypeError, ValueError) as error:
            return None, record_error(error)

    def _parse_job(self, summary: dict[str, object], payload: object) -> ConnectorJob:
        if not isinstance(payload, dict) or not isinstance(payload.get("jobPostingInfo"), dict):
            raise TypeError("job-detail payload must contain a jobPostingInfo object")
        info = payload["jobPostingInfo"]
        external_url = self._public_job_url(info.get("externalUrl"))
        source_opened_at = normalize_timestamp(info.get("startDate"))
        locations = [clean_text(info.get("location"))]
        additional = info.get("additionalLocations") or []
        if isinstance(additional, list):
            locations.extend(clean_text(value) for value in additional)
        location = ", ".join(dict.fromkeys(value for value in locations if value))
        return ConnectorJob(
            source=self.source,
            external_job_id=require_text(info, "id"),
            title=require_text(info, "title"),
            url=external_url,
            location=location,
            description=clean_html(info.get("jobDescription")),
            source_opened_at=source_opened_at,
            source_updated_at=None,
            metadata={
                "source_opened_at_field": "startDate",
                "source_opened_at_available": source_opened_at is not None,
                "job_requisition_id": info.get("jobReqId"),
                "job_posting_id": info.get("jobPostingId"),
                "job_posting_site_id": info.get("jobPostingSiteId"),
                "time_type": info.get("timeType"),
                "remote_type": info.get("remoteType"),
                "posted_on_label": info.get("postedOn") or summary.get("postedOn"),
                "can_apply": info.get("canApply"),
                "hiring_organization": self._hiring_organization(payload),
            },
        )

    def _public_job_url(self, value: object) -> str:
        url = clean_text(value)
        parsed = urlsplit(url)
        decoded_segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
        unsafe_path = any(
            segment in {".", ".."} or "/" in segment or "\\" in segment
            for segment in decoded_segments
        )
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").casefold().rstrip(".") != self.workday.host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or not parsed.path.startswith(self.workday.public_job_path_prefix)
            or unsafe_path
            or (
                self.workday.uses_recruiting_path
                and (
                    parsed.port is not None
                    or parsed.query
                    or parsed.fragment
                    or "?" in url
                    or "#" in url
                    or "" in parsed.path.split("/")[1:-1]
                )
            )
        ):
            raise ValueError("externalUrl must be an HTTPS job URL on the configured Workday site")
        return url

    @staticmethod
    def _hiring_organization(payload: dict[str, object]) -> str | None:
        value = payload.get("hiringOrganization")
        if not isinstance(value, dict):
            return None
        return clean_text(value.get("name")) or None
