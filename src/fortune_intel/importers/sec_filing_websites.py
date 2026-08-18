"""Extract canonical company websites from exact-CIK SEC filings.

Only an explicit first-party website declaration in the latest relevant annual
filing can become a candidate.  Search results, inferred domains, redirects,
investor-only sites, and third-party hosts are deliberately excluded.
"""

from __future__ import annotations

import threading
import json
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests

from fortune_intel.importers.sec_filing_extraction import (
    SecFilingWebsiteEvidence,
    extract_declared_company_websites,
)
from fortune_intel.importers.wikidata_websites import normalize_sec_cik
from fortune_intel.storage import JobRepository

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_ROOT = "https://www.sec.gov/Archives/edgar/data"
SEC_API_DOCUMENTATION_URL = (
    "https://www.sec.gov/search-filings/edgar-application-programming-interfaces"
)
_BASE_FORMS = {"10-K", "10-KT", "20-F", "40-F"}
_AMENDED_FORMS = {"10-K/A", "10-K-A", "10-KT/A", "20-F/A", "40-F/A"}
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_DOCUMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:html?|txt)$", re.I)
_READY_PREVIEW_LIMIT = 250


@dataclass(frozen=True, slots=True)
class SecFilingWebsite:
    sec_cik: str
    company_name: str
    website_url: str
    filing_form: str
    filing_date: str
    accession_number: str
    primary_document_url: str
    evidence_text: str


@dataclass(frozen=True, slots=True)
class SecFilingWebsiteQueryResult:
    candidates: tuple[SecFilingWebsite, ...]
    ciks_requested: int
    requests_made: int
    no_relevant_filing: int
    no_explicit_website: int
    conflicts: int
    request_failures: int
    invalid_payloads: int
    oversized_documents: int
    invalid_urls: int
    retryable_ciks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Filing:
    form: str
    filing_date: str
    accepted_at: str
    accession_number: str
    primary_document: str
    url: str


class _Response(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def iter_content(self, chunk_size: int) -> Iterable[bytes]: ...

    def close(self) -> None: ...


class _Session(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: tuple[float, float],
        allow_redirects: bool,
        stream: bool,
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


def _latest_filing(payload: object, expected_cik: str) -> tuple[str, str, _Filing | None]:
    if not isinstance(payload, dict):
        return "", "invalid", None
    try:
        actual_cik = normalize_sec_cik(str(payload.get("cik") or ""))
    except ValueError:
        return "", "invalid", None
    name = payload.get("name")
    recent = (
        payload.get("filings", {}).get("recent")
        if isinstance(payload.get("filings"), dict)
        else None
    )
    if actual_cik != expected_cik or not isinstance(name, str) or not name.strip():
        return "", "invalid", None
    if not isinstance(recent, dict):
        return "", "invalid", None
    fields = ("form", "filingDate", "acceptanceDateTime", "accessionNumber", "primaryDocument")
    values = [recent.get(field) for field in fields]
    if any(not isinstance(value, list) for value in values):
        return "", "invalid", None
    lengths = {len(value) for value in values if isinstance(value, list)}
    if len(lengths) != 1:
        return "", "invalid", None
    filings: list[_Filing] = []
    for form, date, accepted, accession, document in zip(*values, strict=True):
        if not all(isinstance(value, str) for value in (form, date, accepted, accession, document)):
            continue
        if form not in _BASE_FORMS | _AMENDED_FORMS:
            continue
        if not _ACCESSION_RE.fullmatch(accession) or not _DOCUMENT_RE.fullmatch(document):
            continue
        compact = accession.replace("-", "")
        url = f"{SEC_ARCHIVES_ROOT}/{int(expected_cik)}/{compact}/{document}"
        filings.append(_Filing(form, date, accepted, accession, document, url))
    if not filings:
        return name.strip(), "ok", None
    base_filings = [filing for filing in filings if filing.form in _BASE_FORMS]
    eligible = base_filings or filings
    return name.strip(), "ok", max(eligible, key=lambda item: (item.accepted_at, item.filing_date))


class SecFilingWebsiteClient:
    """Bounded SEC client with one global fair-access request limiter."""

    def __init__(
        self,
        *,
        user_agent: str,
        session: _Session | None = None,
        requests_per_second: float = 5.0,
        max_retries: int = 4,
        max_html_bytes: int = 15_000_000,
        timeout: tuple[float, float] = (5.0, 30.0),
        sleep: Callable[[float], None] = time.sleep,
        concurrency: int = 1,
        session_factory: Callable[[], _Session] | None = None,
    ) -> None:
        if not 0.1 <= requests_per_second <= 10:
            raise ValueError("requests_per_second must be between 0.1 and 10")
        if not 0 <= max_retries <= 8:
            raise ValueError("max_retries must be between 0 and 8")
        if not 100_000 <= max_html_bytes <= 25_000_000:
            raise ValueError("max_html_bytes must be between 100000 and 25000000")
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
        self.max_html_bytes = max_html_bytes
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
        with self._rate_lock:
            now = time.monotonic()
            scheduled = max(now, self._next_request_at)
            self._next_request_at = scheduled + self.minimum_interval
        if scheduled > now:
            self.sleep(scheduled - now)

    def _fetch(self, url: str, *, accept: str, limit: int) -> tuple[str, bytes | None]:
        response: _Response | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_for_request_slot()
            try:
                response = self._request_session().get(
                    url,
                    headers={
                        "Accept": accept,
                        "Accept-Encoding": "gzip",
                        "User-Agent": self.user_agent,
                    },
                    timeout=self.timeout,
                    allow_redirects=False,
                    stream=True,
                )
                with self._request_lock:
                    self.requests_made += 1
            except requests.RequestException:
                if attempt == self.max_retries:
                    return "retryable_failure", None
                self.sleep(_retry_delay(None, attempt, 60.0))
                continue
            try:
                if response.status_code == 200:
                    content_type = response.headers.get("Content-Type", "").casefold()
                    valid_type = (
                        "json" in content_type
                        if accept == "application/json"
                        else content_type.startswith(
                            ("text/html", "application/xhtml+xml", "text/plain")
                        )
                    )
                    if not valid_type:
                        return "invalid_content_type", None
                    declared = response.headers.get("Content-Length", "")
                    if declared.isdigit() and int(declared) > limit:
                        return "oversized", None
                    body = bytearray()
                    for chunk in response.iter_content(chunk_size=65_536):
                        body.extend(chunk)
                        if len(body) > limit:
                            return "oversized", None
                    return "ok", bytes(body)
                if response.status_code not in _RETRYABLE_STATUSES:
                    return "http_failure", None
                if attempt == self.max_retries:
                    return "retryable_failure", None
            except requests.RequestException:
                if attempt == self.max_retries:
                    return "retryable_failure", None
            finally:
                response.close()
            self.sleep(_retry_delay(response, attempt, 60.0))
        raise AssertionError("retry loop exited unexpectedly")

    def _query_one(self, cik: str) -> tuple[str, SecFilingWebsite | None]:
        status, body = self._fetch(
            SEC_SUBMISSIONS_URL.format(cik=cik), accept="application/json", limit=10_000_000
        )
        if status != "ok" or body is None:
            if status == "invalid_content_type":
                return "invalid_payloads", None
            if status == "retryable_failure":
                return "retryable_request_failures", None
            return "request_failures", None
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "invalid_payloads", None
        company_name, payload_status, filing = _latest_filing(payload, cik)
        if payload_status != "ok":
            return "invalid_payloads", None
        if filing is None:
            return "no_relevant_filing", None
        status, html = self._fetch(filing.url, accept="text/html", limit=self.max_html_bytes)
        if status == "oversized":
            return "oversized_documents", None
        if status == "invalid_content_type":
            return "invalid_payloads", None
        if status == "retryable_failure":
            return "retryable_request_failures", None
        if status != "ok" or html is None:
            return "request_failures", None
        evidence = extract_declared_company_websites(html, company_name=company_name)
        if not evidence:
            return "no_explicit_website", None
        by_host: dict[str, list[SecFilingWebsiteEvidence]] = defaultdict(list)
        for item in evidence:
            host = (urlsplit(item.website_url).hostname or "").casefold().removeprefix("www.")
            by_host[host].append(item)
        if len(by_host) != 1:
            return "conflicts", None
        selected = sorted(next(iter(by_host.values())), key=lambda item: item.website_url)[0]
        return "candidate", SecFilingWebsite(
            cik,
            company_name,
            selected.website_url,
            filing.form,
            filing.filing_date,
            filing.accession_number,
            filing.url,
            selected.evidence_text,
        )

    def query(self, ciks: Iterable[str | int]) -> SecFilingWebsiteQueryResult:
        normalized = sorted({normalize_sec_cik(cik) for cik in ciks})
        initial_requests = self.requests_made
        if self.concurrency == 1:
            outcomes = [self._query_one(cik) for cik in normalized]
        else:
            with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
                outcomes = list(executor.map(self._query_one, normalized))
        counters = defaultdict(int)
        candidates = []
        retryable_ciks = []
        for cik, (status, candidate) in zip(normalized, outcomes, strict=True):
            counters[status] += 1
            if candidate is not None:
                candidates.append(candidate)
            elif status == "retryable_request_failures":
                retryable_ciks.append(cik)
        return SecFilingWebsiteQueryResult(
            candidates=tuple(candidates),
            ciks_requested=len(normalized),
            requests_made=self.requests_made - initial_requests,
            no_relevant_filing=counters["no_relevant_filing"],
            no_explicit_website=counters["no_explicit_website"],
            conflicts=counters["conflicts"],
            request_failures=(
                counters["request_failures"] + counters["retryable_request_failures"]
            ),
            invalid_payloads=counters["invalid_payloads"],
            oversized_documents=counters["oversized_documents"],
            invalid_urls=counters["invalid_urls"],
            retryable_ciks=tuple(retryable_ciks),
        )


def import_sec_filing_company_websites(
    repository: JobRepository,
    client: SecFilingWebsiteClient,
    *,
    actor: str,
    limit: int | None = None,
    after_cik: str | int | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Import a deterministic, resumable batch of exact-CIK filing evidence."""
    if not actor.strip():
        raise ValueError("actor is required for auditable website imports")
    if limit is not None and not 1 <= limit <= 100_000:
        raise ValueError("limit must be between 1 and 100000")
    cursor = normalize_sec_cik(after_cik) if after_cik is not None else ""
    companies = sorted(
        (
            company
            for company in repository.list_companies(include_synthetic=False)
            if str(company.get("sec_cik") or "").strip()
            and not str(company.get("website_url") or "").strip()
            and str(company["sec_cik"]) > cursor
        ),
        key=lambda company: (str(company["sec_cik"]), int(company["id"])),
    )
    if limit is not None:
        companies = companies[:limit]
    by_cik: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for company in companies:
        by_cik[normalize_sec_cik(str(company["sec_cik"]))].append(company)
    result = client.query(by_cik)
    candidates = {candidate.sec_cik: candidate for candidate in result.candidates}
    processed_ciks = sorted(by_cik)
    safe_resume = processed_ciks[-1] if processed_ciks else cursor
    if result.retryable_ciks:
        first_retry = processed_ciks.index(result.retryable_ciks[0])
        safe_resume = processed_ciks[first_retry - 1] if first_retry else cursor
    stats = {
        "companies_considered": len(companies),
        "ciks_queried": result.ciks_requested,
        "first_cik_processed": processed_ciks[0] if processed_ciks else "",
        "last_cik_processed": processed_ciks[-1] if processed_ciks else "",
        "safe_resume_after_cik": safe_resume,
        "retryable_ciks": list(result.retryable_ciks),
        "requests_made": result.requests_made,
        "websites_ready": 0,
        "websites_imported": 0,
        "unresolved": 0,
        "ambiguous_company_cik": 0,
        "no_relevant_filing": result.no_relevant_filing,
        "no_explicit_website": result.no_explicit_website,
        "conflicts": result.conflicts,
        "request_failures": result.request_failures,
        "invalid_payloads": result.invalid_payloads,
        "oversized_documents": result.oversized_documents,
        "invalid_urls": result.invalid_urls,
    }
    ready_candidates: list[dict[str, object]] = []
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    for cik, matched in by_cik.items():
        if len(matched) != 1:
            stats["ambiguous_company_cik"] += len(matched)
            continue
        candidate = candidates.get(cik)
        if candidate is None:
            stats["unresolved"] += 1
            continue
        stats["websites_ready"] += 1
        company = matched[0]
        if len(ready_candidates) < _READY_PREVIEW_LIMIT:
            ready_candidates.append(
                {
                    "company_id": int(company["id"]),
                    "company_name": str(company["name"]),
                    "sec_cik": cik,
                    "website_url": candidate.website_url,
                    "filing_form": candidate.filing_form,
                    "filing_date": candidate.filing_date,
                    "accession_number": candidate.accession_number,
                    "source_url": candidate.primary_document_url,
                    "evidence_text": candidate.evidence_text,
                }
            )
        if dry_run:
            continue
        company_id = repository.upsert_company(
            str(company["name"]), website_url=candidate.website_url
        )
        coverage = repository.get_company_coverage(company_id)
        repository.set_company_disposition(
            company_id,
            str(coverage["disposition"]),
            reason=(
                "Canonical website seed verified from SEC filing "
                f"by exact CIK {cik}; form {candidate.filing_form}; "
                f"accession {candidate.accession_number}; selected {candidate.website_url}; "
                f"explicit evidence: {candidate.evidence_text}; source {candidate.primary_document_url}"
            ),
            actor=actor.strip(),
            reviewed_at=now,
        )
        stats["websites_imported"] += 1
    stats["ready_candidates"] = ready_candidates
    stats["ready_candidates_truncated"] = max(
        0, int(stats["websites_ready"]) - len(ready_candidates)
    )
    return stats
