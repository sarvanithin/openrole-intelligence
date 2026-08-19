"""Complete manifests from ADP Workforce Now public career centers."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlencode, urlsplit

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

_HOST = "workforcenow.adp.com"
_PORTAL_PATH = "/mascsr/default/mdf/recruitment/recruitment.html"
_API_PATH = "/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CAREER_CENTER_ID = re.compile(r"^[0-9]{1,20}_[0-9]{1,20}$")
_LOCALE = re.compile(r"^[a-z]{2}_[A-Z]{2}$")
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SOURCE_SEPARATOR = "|"


@dataclass(frozen=True, slots=True)
class ADPWorkforceNowSource:
    """An exact public ADP client, career center, and locale."""

    client_id: str
    career_center_id: str
    locale: str

    @property
    def key(self) -> str:
        return _SOURCE_SEPARATOR.join((self.client_id, self.career_center_id, self.locale))

    @property
    def public_base_url(self) -> str:
        return f"https://{_HOST}{_PORTAL_PATH}?{urlencode(self.public_params)}"

    @property
    def public_params(self) -> dict[str, str]:
        return {
            "cid": self.client_id,
            "ccId": self.career_center_id,
            "lang": self.locale,
        }

    @property
    def api_base_url(self) -> str:
        return f"https://{_HOST}{_API_PATH}"


def adp_workforce_now_source(
    client_id: str,
    career_center_id: str,
    locale: str,
) -> ADPWorkforceNowSource:
    """Validate identifiers observed in an exact Workforce Now public URL."""

    normalized_client = client_id.strip().casefold()
    normalized_center = career_center_id.strip()
    normalized_locale = locale.strip()
    if _UUID.fullmatch(normalized_client) is None:
        raise ValueError("client ID must be a lowercase-safe ADP UUID")
    if _CAREER_CENTER_ID.fullmatch(normalized_center) is None:
        raise ValueError("career-center ID must be a safe ADP numeric identifier")
    if _LOCALE.fullmatch(normalized_locale) is None:
        raise ValueError("locale must be an ADP language_COUNTRY value")
    return ADPWorkforceNowSource(
        normalized_client,
        normalized_center,
        normalized_locale,
    )


def parse_adp_workforce_now_source_key(value: str) -> ADPWorkforceNowSource:
    parts = value.split(_SOURCE_SEPARATOR)
    if len(parts) != 3:
        raise ValueError("ADP source key must contain client ID, career-center ID, and locale")
    return adp_workforce_now_source(*parts)


def adp_workforce_now_source_from_url(url: str) -> ADPWorkforceNowSource:
    """Parse only an exact anonymous public career-center page URL."""

    value = url.strip()
    if len(value) > 4_096:
        raise ValueError("ADP URL exceeds the parser length limit")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("ADP URL has an invalid port") from error
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold().rstrip(".") != _HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != _PORTAL_PATH
        or parsed.fragment
        or "#" in value
    ):
        raise ValueError("URL must be an exact HTTPS Workforce Now public career-center page")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=64)
    except ValueError as error:
        raise ValueError("ADP URL query exceeds the field limit") from error
    client_id = _single_query_value(query, "cid")
    career_center_id = _single_query_value(query, "ccId")
    locale_values = [value for field in ("lang", "locale") for value in query.get(field, [])]
    if len(locale_values) != 1:
        raise ValueError("ADP URL must contain exactly one lang or locale value")
    return adp_workforce_now_source(client_id, career_center_id, locale_values[0])


def _single_query_value(query: dict[str, list[str]], field: str) -> str:
    values = query.get(field, [])
    if len(values) != 1:
        raise ValueError(f"ADP URL must contain exactly one {field} value")
    return values[0]


class ADPWorkforceNowConnector:
    """Fetch every summary and detail exposed by a public Workforce Now site."""

    source = "adp_workforce_now"

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
        self.adp = parse_adp_workforce_now_source_key(source_key)
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
                return self._incomplete(jobs, errors, pages)
            pages += 1
            try:
                total, summaries = self._parse_page(payload, offset)
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
                    self.adp.key,
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
            self.adp.key,
            tuple(jobs),
            False,
            tuple(errors),
            pages,
        )

    def _list_url(self, offset: int) -> str:
        params = {
            "cid": self.adp.client_id,
            "ccId": self.adp.career_center_id,
            "locale": self.adp.locale,
            "$skip": str(offset),
            "$top": str(self.page_size),
            "userQuery": "",
            "lang": self.adp.locale,
        }
        return f"{self.adp.api_base_url}?{urlencode(params)}"

    def _detail_url(self, identifier: str) -> str:
        params = {
            "cid": self.adp.client_id,
            "ccId": self.adp.career_center_id,
            "locale": self.adp.locale,
        }
        return f"{self.adp.api_base_url}/{quote(identifier, safe='')}?{urlencode(params)}"

    def _parse_page(
        self,
        payload: object,
        requested_offset: int,
    ) -> tuple[int, list[dict[str, object]]]:
        if not isinstance(payload, dict):
            raise TypeError("job-list payload must be an object")
        summaries = payload.get("jobRequisitions")
        meta = payload.get("meta")
        if not isinstance(summaries, list) or not all(isinstance(item, dict) for item in summaries):
            raise TypeError("job-list payload must contain a jobRequisitions object list")
        if not isinstance(meta, dict):
            raise TypeError("job-list payload must contain a meta object")
        start = self._nonnegative_integer(meta.get("startSequence"), "start sequence")
        total = self._nonnegative_integer(meta.get("totalNumber"), "job total")
        if start != requested_offset:
            raise ValueError(f"job-list offset was {start}; expected {requested_offset}")
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
            identifier = self._external_job_id(summary)
            item_id = self._safe_identifier(summary.get("itemID"), "item ID")
        except (TypeError, ValueError) as error:
            return None, record_error(error)
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
            return self._parse_job(summary, payload, identifier, item_id), None
        except (TypeError, ValueError) as error:
            return None, record_error(error, external_id=identifier)

    def _parse_job(
        self,
        summary: dict[str, object],
        payload: object,
        identifier: str,
        item_id: str,
    ) -> ConnectorJob:
        if not isinstance(payload, dict):
            raise TypeError("job-detail payload must be an object")
        if self._external_job_id(payload) != identifier:
            raise ValueError("job-detail external ID did not match the requested job")
        if self._safe_identifier(payload.get("itemID"), "item ID") != item_id:
            raise ValueError("job-detail item ID did not match the summary")
        opened_value = payload.get("postDate") or summary.get("postDate")
        opened_field = "postDate"
        source_opened_at = normalize_timestamp(opened_value)
        params = {**self.adp.public_params, "jobId": identifier}
        return ConnectorJob(
            source=self.source,
            external_job_id=identifier,
            title=require_text(payload, "requisitionTitle"),
            url=f"https://{_HOST}{_PORTAL_PATH}?{urlencode(params)}",
            location=self._locations(payload, summary),
            description=clean_html(payload.get("requisitionDescription")),
            source_opened_at=source_opened_at,
            source_updated_at=None,
            metadata={
                "source_opened_at_field": opened_field,
                "source_opened_at_available": source_opened_at is not None,
                "listing_post_date": summary.get("postDate"),
                "item_id": item_id,
                "client_requisition_id": payload.get("clientRequisitionID"),
                "work_level": self._short_name(payload.get("workLevelCode")),
                "sponsored_visa_types": self._code_values(payload.get("sponsoredVisaTypeCodes")),
            },
        )

    @classmethod
    def _external_job_id(cls, record: dict[str, object]) -> str:
        group = record.get("customFieldGroup")
        if not isinstance(group, dict):
            raise TypeError("job must contain a customFieldGroup object")
        fields = group.get("stringFields")
        if not isinstance(fields, list):
            raise TypeError("job must contain a stringFields list")
        values: list[str] = []
        for field in fields:
            if not isinstance(field, dict):
                raise TypeError("stringFields entries must be objects")
            code = field.get("nameCode")
            if isinstance(code, dict) and clean_text(code.get("codeValue")) == "ExternalJobID":
                values.append(cls._safe_identifier(field.get("stringValue"), "external job ID"))
        if len(values) != 1:
            raise ValueError("job must contain exactly one safe ExternalJobID")
        return values[0]

    @staticmethod
    def _safe_identifier(value: object, label: str) -> str:
        identifier = clean_text(value)
        if _JOB_ID.fullmatch(identifier) is None:
            raise ValueError(f"{label} must be a safe ADP identifier")
        return identifier

    @classmethod
    def _locations(
        cls,
        detail: dict[str, object],
        summary: dict[str, object],
    ) -> str:
        locations: list[str] = []
        values = detail.get("requisitionLocations") or summary.get("requisitionLocations") or []
        if not isinstance(values, list):
            raise TypeError("requisitionLocations must be a list")
        for location in values:
            if not isinstance(location, dict):
                raise TypeError("requisitionLocations entries must be objects")
            name = cls._short_name(location.get("nameCode"))
            address = location.get("address")
            if not name and isinstance(address, dict):
                name = ", ".join(
                    value
                    for value in (
                        clean_text(address.get("cityName")),
                        cls._short_name(address.get("countrySubdivisionLevel1")),
                        clean_text(address.get("postalCode")),
                    )
                    if value
                )
            if name and name.casefold() not in {value.casefold() for value in locations}:
                locations.append(name)
        return ", ".join(locations)

    @staticmethod
    def _short_name(value: object) -> str:
        if not isinstance(value, dict):
            return ""
        return clean_text(value.get("shortName") or value.get("codeValue"))

    @staticmethod
    def _code_values(value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        codes: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            name_code = item.get("nameCode")
            code = clean_text(item.get("codeValue") or item.get("shortName"))
            if not code and isinstance(name_code, dict):
                code = clean_text(name_code.get("codeValue") or name_code.get("shortName"))
            if code:
                codes.append(code)
        return tuple(codes)
