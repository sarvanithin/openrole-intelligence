"""Acquire company website seeds from SEC Submissions JSON by exact CIK.

The SEC endpoint is addressed only with a CIK already stored on the company.
There is no company-name search, domain construction, redirect following, or URL
guessing in this importer.
"""

from __future__ import annotations

import time
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import requests

from fortune_intel.importers.wikidata_websites import normalize_sec_cik
from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_API_DOCUMENTATION_URL = (
    "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"
)
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class SecWebsite:
    sec_cik: str
    company_name: str
    source_url: str
    website_url: str = ""
    investor_website_url: str = ""


@dataclass(frozen=True, slots=True)
class SecWebsiteQueryResult:
    candidates: tuple[SecWebsite, ...]
    ciks_requested: int
    requests_made: int
    not_found: int
    request_failures: int
    invalid_payloads: int
    invalid_urls: int


class _Response(Protocol):
    status_code: int
    headers: Mapping[str, str]
    text: str

    def json(self) -> Any: ...


class _Session(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: tuple[float, float],
        allow_redirects: bool,
    ) -> _Response: ...


def _contactable_user_agent(value: str) -> str:
    agent = value.strip()
    if not agent or ("@" not in agent and "http://" not in agent and "https://" not in agent):
        raise ValueError("user_agent must identify the application and an operator contact")
    if agent.casefold().startswith(("python-requests", "curl/")):
        raise ValueError("user_agent must identify the application and an operator contact")
    return agent


def _retry_delay(response: _Response | None, attempt: int, maximum: float) -> float:
    retry_after = response.headers.get("Retry-After", "") if response is not None else ""
    if retry_after:
        try:
            return min(maximum, max(0.0, float(retry_after)))
        except ValueError:
            try:
                target = parsedate_to_datetime(retry_after)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=UTC)
                return min(maximum, max(0.0, (target - datetime.now(UTC)).total_seconds()))
            except (TypeError, ValueError):
                pass
    return min(maximum, 2**attempt)


class SecSubmissionsWebsiteClient:
    """Sequential SEC client capped below the SEC's ten-request/second limit."""

    def __init__(
        self,
        *,
        user_agent: str,
        session: _Session | None = None,
        requests_per_second: float = 5.0,
        max_retries: int = 4,
        max_retry_delay_seconds: float = 60.0,
        timeout: tuple[float, float] = (5.0, 30.0),
        sleep: Callable[[float], None] = time.sleep,
        concurrency: int = 1,
        session_factory: Callable[[], _Session] | None = None,
    ) -> None:
        if not 0.1 <= requests_per_second <= 10:
            raise ValueError("requests_per_second must be between 0.1 and 10")
        if not 0 <= max_retries <= 8:
            raise ValueError("max_retries must be between 0 and 8")
        if not 1 <= max_retry_delay_seconds <= 900:
            raise ValueError("max_retry_delay_seconds must be between 1 and 900")
        if not 1 <= concurrency <= 8:
            raise ValueError("concurrency must be between 1 and 8")
        if concurrency > 1 and session is not None:
            raise ValueError("an injected shared session requires concurrency=1")
        self.user_agent = _contactable_user_agent(user_agent)
        self.session = session or requests.Session()
        self.concurrency = concurrency
        self._session_factory = session_factory or requests.Session
        self._thread_sessions = threading.local()
        self.minimum_interval = 1 / requests_per_second
        self.max_retries = max_retries
        self.max_retry_delay_seconds = max_retry_delay_seconds
        self.timeout = timeout
        self.sleep = sleep
        self.requests_made = 0
        self._request_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._next_request_at = 0.0

    def _request_session(self) -> _Session:
        if self.concurrency == 1:
            return self.session
        session = getattr(self._thread_sessions, "session", None)
        if session is None:
            session = self._session_factory()
            self._thread_sessions.session = session
        return session

    def _wait_for_request_slot(self) -> None:
        if self.concurrency == 1:
            return
        with self._rate_lock:
            now = time.monotonic()
            scheduled = max(now, self._next_request_at)
            self._next_request_at = scheduled + self.minimum_interval
        delay = scheduled - now
        if delay > 0:
            self.sleep(delay)

    def _request(self, cik: str) -> tuple[str, Any | None]:
        url = SEC_SUBMISSIONS_URL.format(cik=cik)
        response: _Response | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_for_request_slot()
            try:
                response = self._request_session().get(
                    url,
                    headers={"Accept": "application/json", "User-Agent": self.user_agent},
                    timeout=self.timeout,
                    allow_redirects=False,
                )
                with self._request_lock:
                    self.requests_made += 1
            except requests.RequestException:
                if attempt == self.max_retries:
                    return "failed", None
                self.sleep(
                    max(
                        self.minimum_interval,
                        _retry_delay(None, attempt, self.max_retry_delay_seconds),
                    )
                )
                continue
            if response.status_code == 200:
                try:
                    return "ok", response.json()
                except (TypeError, ValueError):
                    return "invalid", None
            if response.status_code == 404:
                return "not_found", None
            if response.status_code not in _RETRYABLE_STATUSES or attempt == self.max_retries:
                return "failed", None
            self.sleep(
                max(
                    self.minimum_interval,
                    _retry_delay(response, attempt, self.max_retry_delay_seconds),
                )
            )
        raise AssertionError("retry loop exited unexpectedly")

    def query(self, ciks: Iterable[str | int]) -> SecWebsiteQueryResult:
        normalized = sorted({normalize_sec_cik(cik) for cik in ciks})
        if self.concurrency > 1:
            return self._query_concurrently(normalized)
        candidates: list[SecWebsite] = []
        not_found = failures = invalid_payloads = invalid_urls = 0
        initial_requests = self.requests_made
        for index, cik in enumerate(normalized):
            status, payload = self._request(cik)
            if status == "not_found":
                not_found += 1
            elif status == "failed":
                failures += 1
            elif status == "invalid":
                invalid_payloads += 1
            else:
                parsed, bad_urls = _parse_payload(payload, expected_cik=cik)
                invalid_urls += bad_urls
                if parsed is None:
                    invalid_payloads += 1
                elif not bad_urls or parsed.website_url or parsed.investor_website_url:
                    candidates.append(parsed)
            if index + 1 < len(normalized):
                self.sleep(self.minimum_interval)
        return SecWebsiteQueryResult(
            candidates=tuple(candidates),
            ciks_requested=len(normalized),
            requests_made=self.requests_made - initial_requests,
            not_found=not_found,
            request_failures=failures,
            invalid_payloads=invalid_payloads,
            invalid_urls=invalid_urls,
        )

    def _query_concurrently(self, normalized: list[str]) -> SecWebsiteQueryResult:
        initial_requests = self.requests_made
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            outcomes = list(executor.map(self._query_one, normalized))
        candidates = tuple(
            outcome[1] for outcome in outcomes if outcome[0] == "candidate" and outcome[1]
        )
        statuses = [outcome[0] for outcome in outcomes]
        return SecWebsiteQueryResult(
            candidates=candidates,
            ciks_requested=len(normalized),
            requests_made=self.requests_made - initial_requests,
            not_found=statuses.count("not_found"),
            request_failures=statuses.count("failed"),
            invalid_payloads=statuses.count("invalid"),
            invalid_urls=sum(outcome[2] for outcome in outcomes),
        )

    def _query_one(self, cik: str) -> tuple[str, SecWebsite | None, int]:
        status, payload = self._request(cik)
        if status != "ok":
            return status, None, 0
        parsed, invalid_urls = _parse_payload(payload, expected_cik=cik)
        if parsed is None:
            return "invalid", None, invalid_urls
        if invalid_urls and not parsed.website_url and not parsed.investor_website_url:
            return "empty", None, invalid_urls
        return "candidate", parsed, invalid_urls


def _parse_url(payload: Mapping[str, object], field: str) -> tuple[str, bool]:
    value = payload.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        return "", False
    if not isinstance(value, str):
        return "", True
    try:
        return normalize_public_url(value, field=f"SEC {field}"), False
    except ValueError:
        return "", True


def _parse_payload(payload: object, *, expected_cik: str) -> tuple[SecWebsite | None, int]:
    if not isinstance(payload, dict):
        return None, 0
    try:
        actual_cik = normalize_sec_cik(str(payload.get("cik") or ""))
    except ValueError:
        return None, 0
    if actual_cik != expected_cik:
        return None, 0
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, 0
    website, website_invalid = _parse_url(payload, "website")
    investor, investor_invalid = _parse_url(payload, "investorWebsite")
    return (
        SecWebsite(
            sec_cik=expected_cik,
            company_name=name.strip(),
            source_url=SEC_SUBMISSIONS_URL.format(cik=expected_cik),
            website_url=website,
            investor_website_url=investor,
        ),
        int(website_invalid) + int(investor_invalid),
    )


def import_sec_company_websites(
    repository: JobRepository,
    client: SecSubmissionsWebsiteClient,
    *,
    actor: str,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Import SEC-declared URLs only for exact-CIK companies missing a website."""
    if not actor.strip():
        raise ValueError("actor is required for auditable website imports")
    if limit is not None and not 1 <= limit <= 100_000:
        raise ValueError("limit must be between 1 and 100000")
    companies = [
        company
        for company in repository.list_companies(include_synthetic=False)
        if str(company.get("sec_cik") or "").strip()
        and not str(company.get("website_url") or "").strip()
    ]
    if limit is not None:
        companies = companies[:limit]
    by_cik: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for company in companies:
        by_cik[normalize_sec_cik(str(company["sec_cik"]))].append(company)

    result = client.query(by_cik)
    candidates = {candidate.sec_cik: candidate for candidate in result.candidates}
    stats = {
        "companies_considered": len(companies),
        "ciks_queried": result.ciks_requested,
        "requests_made": result.requests_made,
        "websites_ready": 0,
        "websites_imported": 0,
        "sec_website_used": 0,
        "sec_investor_website_fallback_used": 0,
        "no_sec_url": 0,
        "unresolved_query": 0,
        "ambiguous_company_cik": 0,
        "not_found": result.not_found,
        "request_failures": result.request_failures,
        "invalid_payloads": result.invalid_payloads,
        "invalid_urls": result.invalid_urls,
    }
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    for cik, matched_companies in by_cik.items():
        if len(matched_companies) != 1:
            stats["ambiguous_company_cik"] += len(matched_companies)
            continue
        candidate = candidates.get(cik)
        if candidate is None:
            stats["unresolved_query"] += 1
            continue
        field = "website"
        website = candidate.website_url
        if not website:
            field = "investorWebsite"
            website = candidate.investor_website_url
        if not website:
            stats["no_sec_url"] += 1
            continue
        stats["websites_ready"] += 1
        if dry_run:
            continue
        company = matched_companies[0]
        company_id = repository.upsert_company(str(company["name"]), website_url=website)
        coverage = repository.get_company_coverage(company_id)
        label = "official website" if field == "website" else "investor website fallback"
        repository.set_company_disposition(
            company_id,
            str(coverage["disposition"]),
            reason=(
                f"SEC Submissions JSON {label} imported by exact CIK {cik}: "
                f"top-level {field} ({website}); source {candidate.source_url}"
            ),
            actor=actor.strip(),
            reviewed_at=now,
        )
        stats["websites_imported"] += 1
        stats[
            "sec_website_used" if field == "website" else "sec_investor_website_fallback_used"
        ] += 1
    return stats
