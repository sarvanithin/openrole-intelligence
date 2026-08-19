"""Complete manifests from exact public UKG/UltiPro Recruiting job boards."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

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
from fortune_intel.connectors.ukg import classify_ukg_board_url

_HOSTS = frozenset({"recruiting.ultipro.com", "recruiting2.ultipro.com", "recruiting.ultipro.ca"})
_TENANT = re.compile(r"^[A-Za-z0-9]{2,64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SOURCE_SEPARATOR = "|"
_DETAIL_PREFIX = "var opportunity = new US.Opportunity.CandidateOpportunityDetail("


@dataclass(frozen=True, slots=True)
class UKGRecruitingPublicSource:
    """An exact public UKG host, tenant, and external job-board UUID."""

    host: str
    tenant: str
    board_id: str

    @property
    def key(self) -> str:
        return _SOURCE_SEPARATOR.join((self.host, self.tenant, self.board_id))

    @property
    def public_base_url(self) -> str:
        return f"https://{self.host}/{self.tenant}/JobBoard/{self.board_id}"

    @property
    def listing_url(self) -> str:
        return f"{self.public_base_url}/JobBoardView/LoadSearchResults"


def ukg_recruiting_public_source(
    host: str,
    tenant: str,
    board_id: str,
) -> UKGRecruitingPublicSource:
    normalized_host = host.strip().casefold().rstrip(".")
    normalized_tenant = tenant.strip()
    normalized_board = board_id.strip().casefold()
    if normalized_host not in _HOSTS:
        raise ValueError("host must be an exact public UKG Recruiting host")
    if _TENANT.fullmatch(normalized_tenant) is None:
        raise ValueError("tenant must be a safe UKG tenant identifier")
    if _UUID.fullmatch(normalized_board) is None:
        raise ValueError("board ID must be a safe UKG UUID")
    return UKGRecruitingPublicSource(normalized_host, normalized_tenant, normalized_board)


def parse_ukg_recruiting_public_source_key(value: str) -> UKGRecruitingPublicSource:
    parts = value.split(_SOURCE_SEPARATOR)
    if len(parts) != 3:
        raise ValueError("UKG source key must contain host, tenant, and board ID")
    return ukg_recruiting_public_source(*parts)


def ukg_recruiting_public_source_from_url(url: str) -> UKGRecruitingPublicSource:
    """Parse an exact observed UKG board or opportunity-detail URL."""

    value = url.strip()
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("UKG URL has an invalid port") from error
    if not value or len(value) > 4_096 or port is not None or "#" in value:
        raise ValueError("UKG URL must use an exact unambiguous public board URL")
    candidate = classify_ukg_board_url(url)
    if candidate is None or not candidate.board_id:
        raise ValueError("URL must contain an exact public UKG tenant and job-board UUID")
    return ukg_recruiting_public_source(
        candidate.host,
        candidate.tenant,
        candidate.board_id,
    )


class UKGRecruitingPublicConnector:
    """Fetch every public opportunity after explicit operator policy approval."""

    source = "ukg_recruiting_public"

    def __init__(
        self,
        source_key: str,
        *,
        page_size: int = 50,
        max_pages: int = 500,
        client: JsonHttpClient | None = None,
        detail_concurrency: int = 4,
        detail_client_factory: Callable[[], JsonHttpClient] | None = None,
    ) -> None:
        self.ukg = parse_ukg_recruiting_public_source_key(source_key)
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
                payload = self.client.post_json(
                    self.ukg.listing_url,
                    json_body=self._listing_body(offset),
                )
            except HttpFailure as error:
                errors.append(http_error(error, page=page_number))
                return self._incomplete(jobs, errors, pages)
            pages += 1
            try:
                total, summaries = self._parse_page(payload)
            except (TypeError, ValueError) as error:
                errors.append(record_error(error))
                return self._incomplete(jobs, errors, pages)
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
                return self._incomplete(jobs, errors, pages)
            if not summaries and offset < expected_total:
                errors.append(record_error(ValueError("pagination stopped before total")))
                return self._incomplete(jobs, errors, pages)
            for job, error in self._fetch_details(summaries, page_number):
                if error is not None:
                    errors.append(error)
                    continue
                assert job is not None
                if job.external_job_id in seen_ids:
                    errors.append(
                        record_error(
                            ValueError(f"duplicate native job ID: {job.external_job_id}"),
                            external_id=job.external_job_id,
                        )
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
                    self.ukg.key,
                    tuple(jobs),
                    not errors and len(jobs) == expected_total,
                    tuple(errors),
                    pages,
                )
        errors.append(record_error(ValueError(f"pagination exceeded {self.max_pages} pages")))
        return self._incomplete(jobs, errors, pages)

    def _incomplete(
        self,
        jobs: list[ConnectorJob],
        errors: list[ConnectorError],
        pages: int,
    ) -> ConnectorResult:
        return ConnectorResult(
            self.source,
            self.ukg.key,
            tuple(jobs),
            False,
            tuple(errors),
            pages,
        )

    def _listing_body(self, offset: int) -> dict[str, object]:
        return {
            "opportunitySearch": {
                "Top": self.page_size,
                "Skip": offset,
                "QueryString": "",
                "OrderBy": [
                    {
                        "Value": "postedDateDesc",
                        "PropertyName": "PostedDate",
                        "Ascending": False,
                    }
                ],
                "Filters": [],
                "Coordinates": None,
                "Extent": None,
                "ProximitySearchType": 0,
            }
        }

    @staticmethod
    def _parse_page(payload: object) -> tuple[int, list[dict[str, object]]]:
        if not isinstance(payload, dict):
            raise TypeError("job-list payload must be an object")
        summaries = payload.get("opportunities")
        if not isinstance(summaries, list) or not all(isinstance(item, dict) for item in summaries):
            raise TypeError("job-list payload must contain an opportunities object list")
        total = UKGRecruitingPublicConnector._nonnegative_integer(
            payload.get("totalCount"), "job total"
        )
        return total, summaries

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

    def _detail_url(self, identifier: str) -> str:
        return f"{self.ukg.public_base_url}/OpportunityDetail?{urlencode({'opportunityId': identifier})}"

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
        try:
            identifier = self._safe_uuid(summary.get("Id"), "opportunity ID")
            page = self._detail_client().get_text(self._detail_url(identifier))
        except HttpFailure as error:
            return None, ConnectorError(
                code=error.code,
                message=error.message,
                retryable=error.retryable,
                external_job_id=clean_text(summary.get("Id")) or None,
                page=page_number,
            )
        except (TypeError, ValueError) as error:
            return None, record_error(error)
        try:
            detail = self._detail_model(page)
            return self._parse_job(summary, detail, identifier), None
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            return None, record_error(error, external_id=identifier)

    @staticmethod
    def _detail_model(page: str) -> dict[str, object]:
        if len(page) > 2_000_000:
            raise ValueError("job-detail page exceeded the parser length limit")
        start = page.find(_DETAIL_PREFIX)
        if start < 0 or page.find(_DETAIL_PREFIX, start + 1) >= 0:
            raise ValueError("job-detail page must contain exactly one opportunity model")
        raw = page[start + len(_DETAIL_PREFIX) :]
        value, end = json.JSONDecoder().raw_decode(raw)
        if not isinstance(value, dict) or not raw[end:].lstrip().startswith(");"):
            raise TypeError("job-detail opportunity model must be one JSON object")
        return value

    def _parse_job(
        self,
        summary: dict[str, object],
        detail: dict[str, object],
        identifier: str,
    ) -> ConnectorJob:
        if self._safe_uuid(detail.get("Id"), "opportunity ID") != identifier:
            raise ValueError("job-detail opportunity ID did not match the summary")
        if clean_text(detail.get("Title")) != clean_text(summary.get("Title")):
            raise ValueError("job-detail title did not match the summary")
        opened = self._board_posted_date(detail)
        opened_at = normalize_timestamp(opened)
        summary_opened_at = normalize_timestamp(summary.get("PostedDate"))
        if opened_at is None or summary_opened_at is None:
            raise ValueError("job must contain a native board posting date")
        if summary_opened_at != opened_at:
            raise ValueError("job-detail posted date did not match the summary")
        updated = normalize_timestamp(detail.get("UpdatedDate"))
        locations, location_metadata = self._locations(detail.get("Locations"))
        return ConnectorJob(
            source=self.source,
            external_job_id=identifier,
            title=require_text(detail, "Title"),
            url=self._detail_url(identifier),
            location=locations,
            description=clean_html(detail.get("Description")),
            source_opened_at=opened_at,
            source_updated_at=updated,
            metadata={
                "source_opened_at_field": "JobBoardMemberships.ExternalPostedDate",
                "source_opened_at_available": True,
                "requisition_number": detail.get("RequisitionNumber"),
                "job_category": detail.get("JobCategoryName"),
                "full_time": detail.get("FullTime"),
                "job_location_type": detail.get("JobLocationType"),
                "pay_range_minimum": detail.get("CompensationAnnualMinimum")
                or detail.get("CompensationHourlyMinimum"),
                "pay_range_maximum": detail.get("CompensationAnnualMaximum")
                or detail.get("CompensationHourlyMaximum"),
                "pay_range_currency": detail.get("PayRangeCurrencyCode"),
                "additional_locations": location_metadata,
            },
        )

    def _board_posted_date(self, detail: dict[str, object]) -> object:
        memberships = detail.get("JobBoardMemberships")
        if not isinstance(memberships, list):
            raise TypeError("job-detail must contain a JobBoardMemberships list")
        matches = [
            item
            for item in memberships
            if isinstance(item, dict)
            and clean_text(item.get("JobBoardId")).casefold() == self.ukg.board_id
            and item.get("PublishedExternal") is True
        ]
        if len(matches) != 1:
            raise ValueError("job-detail must match exactly one external board membership")
        return matches[0].get("ExternalPostedDate")

    @staticmethod
    def _safe_uuid(value: object, label: str) -> str:
        identifier = clean_text(value).casefold()
        if _UUID.fullmatch(identifier) is None:
            raise ValueError(f"{label} must be a safe UKG UUID")
        return identifier

    @staticmethod
    def _locations(value: object) -> tuple[str, list[dict[str, str]]]:
        if not isinstance(value, list):
            raise TypeError("job locations must be a list")
        locations: list[str] = []
        metadata: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                raise TypeError("job location entries must be objects")
            address = item.get("Address")
            parts: list[str] = []
            if isinstance(address, dict):
                state = address.get("State")
                country = address.get("Country")
                parts = [
                    clean_text(address.get("City")),
                    clean_text(state.get("Code")) if isinstance(state, dict) else "",
                    clean_text(country.get("Code")) if isinstance(country, dict) else "",
                ]
            localized = clean_text(item.get("LocalizedDescription"))
            country_code = parts[2] if parts else ""
            location = localized or ", ".join(part for part in parts if part)
            if location and country_code and country_code.casefold() not in location.casefold():
                location = f"{location}, {country_code}"
            if location and location.casefold() not in {item.casefold() for item in locations}:
                locations.append(location)
            if location:
                metadata.append(
                    {
                        "location": location,
                        "city": parts[0] if parts else "",
                        "region": parts[1] if parts else "",
                        "country": country_code,
                    }
                )
        return " | ".join(locations), metadata
