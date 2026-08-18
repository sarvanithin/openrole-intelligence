"""Acquire company websites by exact SEC CIK from Wikidata.

This importer deliberately does not search by company name.  It joins the SEC
CIK stored on a company to Wikidata's Central Index Key property (P5531), then
reads the truthy official jobs URL (P10311) and official website (P856).
Ambiguous identity or URL results are reported and never selected heuristically.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import requests

from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIDATA_ENTITY_PREFIX = "http://www.wikidata.org/entity/"
WIKIDATA_ENTITY_HTTPS_PREFIX = "https://www.wikidata.org/entity/"
WIKIDATA_CIK_PROPERTY_URL = "https://www.wikidata.org/wiki/Property:P5531"
WIKIDATA_WEBSITE_PROPERTY_URL = "https://www.wikidata.org/wiki/Property:P856"
WIKIDATA_JOBS_PROPERTY_URL = "https://www.wikidata.org/wiki/Property:P10311"
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class WikidataWebsite:
    sec_cik: str
    item_url: str
    website_url: str = ""
    career_url: str = ""


@dataclass(frozen=True, slots=True)
class WikidataQueryResult:
    candidates: tuple[WikidataWebsite, ...]
    pages_requested: int
    invalid_bindings: int


class _Response(Protocol):
    status_code: int
    headers: Mapping[str, str]
    text: str

    def json(self) -> Any: ...


class _Session(Protocol):
    def post(
        self,
        url: str,
        *,
        data: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: tuple[float, float],
    ) -> _Response: ...


def normalize_sec_cik(value: str | int) -> str:
    cik = str(value).strip()
    if not cik.isdigit() or len(cik) > 10:
        raise ValueError("SEC CIK must contain at most 10 digits")
    return cik.zfill(10)


def _chunks(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _sparql_query(ciks: Sequence[str]) -> str:
    values = " ".join(f'"{cik}"' for cik in ciks)
    return f"""PREFIX wdt: <http://www.wikidata.org/prop/direct/>
SELECT DISTINCT ?cik ?item ?website ?career WHERE {{
  VALUES ?cik {{ {values} }}
  ?item wdt:P5531 ?cik.
  OPTIONAL {{ ?item wdt:P856 ?website. }}
  OPTIONAL {{ ?item wdt:P10311 ?career. }}
  FILTER(BOUND(?website) || BOUND(?career))
}}
ORDER BY ?cik ?item ?career ?website
"""


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


class WikidataWebsiteClient:
    """Small, sequential WDQS client with VALUES-page batching and backoff."""

    def __init__(
        self,
        *,
        user_agent: str,
        session: _Session | None = None,
        endpoint: str = WIKIDATA_SPARQL_URL,
        batch_size: int = 100,
        max_retries: int = 4,
        page_delay_seconds: float = 1.0,
        max_retry_delay_seconds: float = 60.0,
        timeout: tuple[float, float] = (5.0, 30.0),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        agent = user_agent.strip()
        if not agent or agent.casefold().startswith(("python-requests", "curl/")):
            raise ValueError("user_agent must identify the application and operator contact")
        if not 1 <= batch_size <= 250:
            raise ValueError("batch_size must be between 1 and 250")
        if not 0 <= max_retries <= 8:
            raise ValueError("max_retries must be between 0 and 8")
        if not 0 <= page_delay_seconds <= 60:
            raise ValueError("page_delay_seconds must be between 0 and 60")
        if not 1 <= max_retry_delay_seconds <= 900:
            raise ValueError("max_retry_delay_seconds must be between 1 and 900")
        self.user_agent = agent
        self.session = session or requests.Session()
        self.endpoint = endpoint
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.page_delay_seconds = page_delay_seconds
        self.max_retry_delay_seconds = max_retry_delay_seconds
        self.timeout = timeout
        self.sleep = sleep

    def _request(self, ciks: Sequence[str]) -> Any:
        response: _Response | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(
                    self.endpoint,
                    data={"query": _sparql_query(ciks)},
                    headers={
                        "Accept": "application/sparql-results+json",
                        "Accept-Encoding": "gzip",
                        "User-Agent": self.user_agent,
                    },
                    timeout=self.timeout,
                )
            except requests.RequestException as error:
                if attempt == self.max_retries:
                    raise RuntimeError("Wikidata request failed after retries") from error
                self.sleep(_retry_delay(None, attempt, self.max_retry_delay_seconds))
                continue
            if response.status_code == 200:
                try:
                    return response.json()
                except (TypeError, ValueError) as error:
                    raise RuntimeError("Wikidata returned invalid JSON") from error
            if response.status_code not in _RETRYABLE_STATUSES or attempt == self.max_retries:
                detail = response.text.strip().replace("\n", " ")[:200]
                raise RuntimeError(
                    f"Wikidata request failed with HTTP {response.status_code}: {detail}"
                )
            self.sleep(_retry_delay(response, attempt, self.max_retry_delay_seconds))
        raise AssertionError("retry loop exited unexpectedly")

    def query(self, ciks: Iterable[str | int]) -> WikidataQueryResult:
        normalized = sorted({normalize_sec_cik(cik) for cik in ciks})
        candidates: set[WikidataWebsite] = set()
        invalid = 0
        pages = math.ceil(len(normalized) / self.batch_size) if normalized else 0
        for page_number, page in enumerate(_chunks(normalized, self.batch_size), start=1):
            payload = self._request(page)
            try:
                bindings = payload["results"]["bindings"]
            except (KeyError, TypeError) as error:
                raise RuntimeError("Wikidata response did not contain SPARQL bindings") from error
            if not isinstance(bindings, list):
                raise RuntimeError("Wikidata SPARQL bindings must be a list")
            requested = set(page)
            for binding in bindings:
                candidate = _parse_binding(binding, requested)
                if candidate is None:
                    invalid += 1
                else:
                    candidates.add(candidate)
            if page_number < pages and self.page_delay_seconds:
                self.sleep(self.page_delay_seconds)
        return WikidataQueryResult(
            candidates=tuple(
                sorted(
                    candidates,
                    key=lambda row: (
                        row.sec_cik,
                        row.item_url,
                        row.career_url,
                        row.website_url,
                    ),
                )
            ),
            pages_requested=pages,
            invalid_bindings=invalid,
        )


def _binding_value(binding: object, name: str) -> str:
    if not isinstance(binding, dict):
        return ""
    field = binding.get(name)
    if not isinstance(field, dict):
        return ""
    value = field.get("value")
    return value.strip() if isinstance(value, str) else ""


def _parse_binding(binding: object, requested_ciks: set[str]) -> WikidataWebsite | None:
    try:
        cik = normalize_sec_cik(_binding_value(binding, "cik"))
        item = _binding_value(binding, "item")
    except ValueError:
        return None
    website_value = _binding_value(binding, "website")
    career_value = _binding_value(binding, "career")
    if not website_value and not career_value:
        return None
    try:
        website = normalize_public_url(website_value, field="website_url") if website_value else ""
        career = normalize_public_url(career_value, field="career_url") if career_value else ""
    except ValueError:
        return None
    if cik not in requested_ciks:
        return None
    if item.startswith(WIKIDATA_ENTITY_PREFIX):
        item = f"{WIKIDATA_ENTITY_HTTPS_PREFIX}{item.removeprefix(WIKIDATA_ENTITY_PREFIX)}"
    if not item.startswith(WIKIDATA_ENTITY_HTTPS_PREFIX):
        return None
    entity_id = item.removeprefix(WIKIDATA_ENTITY_HTTPS_PREFIX)
    if not entity_id.startswith("Q") or not entity_id[1:].isdigit():
        return None
    return WikidataWebsite(cik, item, website, career)


def import_wikidata_company_websites(
    repository: JobRepository,
    client: WikidataWebsiteClient,
    *,
    actor: str,
    limit: int | None = None,
    dry_run: bool = False,
    after_company_id: int = 0,
    missing_websites_only: bool = False,
) -> dict[str, object]:
    """Import only unambiguous P5531/P856 matches and report every skip class."""
    if not actor.strip():
        raise ValueError("actor is required for auditable website imports")
    if limit is not None and not 1 <= limit <= 100_000:
        raise ValueError("limit must be between 1 and 100000")
    if after_company_id < 0:
        raise ValueError("after_company_id must be non-negative")
    company_universe = sorted(
        repository.list_companies(include_synthetic=False), key=lambda row: int(row["id"])
    )
    cik_companies = [
        company for company in company_universe if str(company.get("sec_cik") or "").strip()
    ]
    eligible = [
        company
        for company in cik_companies
        if int(company["id"]) > after_company_id
        and (not missing_websites_only or not str(company.get("website_url") or "").strip())
    ]
    companies = eligible
    if limit is not None:
        companies = companies[:limit]
    universe_by_cik: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for company in cik_companies:
        universe_by_cik[normalize_sec_cik(str(company["sec_cik"]))].append(company)
    target_count_by_cik: dict[str, int] = defaultdict(int)
    for company in companies:
        target_count_by_cik[normalize_sec_cik(str(company["sec_cik"]))] += 1
    by_cik: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cik in target_count_by_cik:
        by_cik[cik].extend(universe_by_cik[cik])

    result = client.query(by_cik)
    candidates_by_cik: dict[str, list[WikidataWebsite]] = defaultdict(list)
    for candidate in result.candidates:
        candidates_by_cik[candidate.sec_cik].append(candidate)

    stats = {
        "after_company_id": after_company_id,
        "first_company_id_processed": int(companies[0]["id"]) if companies else None,
        "last_company_id_processed": int(companies[-1]["id"]) if companies else None,
        "safe_resume_after_company_id": (
            int(companies[-1]["id"]) if companies else after_company_id
        ),
        "eligible_after_cursor": len(eligible),
        "has_more": len(companies) < len(eligible),
        "companies_considered": len(companies),
        "ciks_queried": len(by_cik),
        "pages_requested": result.pages_requested,
        "invalid_bindings": result.invalid_bindings,
        "websites_imported": 0,
        "websites_ready": 0,
        "career_urls_imported": 0,
        "career_urls_ready": 0,
        "already_present": 0,
        "no_wikidata_match": 0,
        "ambiguous_company_cik": 0,
        "ambiguous_wikidata_result": 0,
        "ambiguous_wikidata_website": 0,
        "ambiguous_wikidata_career_url": 0,
        "existing_website_conflict": 0,
        "existing_career_url_conflict": 0,
    }
    ready_candidates: list[dict[str, object]] = []
    ready_candidate_count = 0
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    for cik, matched_companies in by_cik.items():
        if len(matched_companies) != 1:
            stats["ambiguous_company_cik"] += target_count_by_cik[cik]
            continue
        rows = candidates_by_cik.get(cik, [])
        if not rows:
            stats["no_wikidata_match"] += 1
            continue
        items = {row.item_url for row in rows}
        if len(items) != 1:
            stats["ambiguous_wikidata_result"] += 1
            continue
        websites = {row.website_url for row in rows if row.website_url}
        career_urls = {row.career_url for row in rows if row.career_url}
        company = matched_companies[0]
        website = ""
        career_url = ""
        if len(websites) > 1:
            stats["ambiguous_wikidata_website"] += 1
        elif websites:
            candidate = next(iter(websites))
            existing = str(company.get("website_url") or "")
            if existing:
                if normalize_public_url(existing, field="website_url") == candidate:
                    stats["already_present"] += 1
                else:
                    stats["existing_website_conflict"] += 1
            else:
                website = candidate
                stats["websites_ready"] += 1
        if len(career_urls) > 1:
            stats["ambiguous_wikidata_career_url"] += 1
        elif career_urls:
            candidate = next(iter(career_urls))
            existing = str(company.get("career_url") or "")
            if existing:
                if normalize_public_url(existing, field="career_url") == candidate:
                    stats["already_present"] += 1
                else:
                    stats["existing_career_url_conflict"] += 1
            else:
                career_url = candidate
                stats["career_urls_ready"] += 1
        if not website and not career_url:
            continue
        ready_candidate_count += 1
        if len(ready_candidates) < 250:
            ready_candidates.append(
                {
                    "company_id": int(company["id"]),
                    "company_name": company["name"],
                    "sec_cik": cik,
                    "wikidata_entity_url": next(iter(items)),
                    "website_url": website,
                    "career_url": career_url,
                    "identity_evidence": f"exact Wikidata P5531 SEC CIK {cik}",
                }
            )
        if dry_run:
            continue
        company_id = repository.upsert_company(
            str(company["name"]), website_url=website, career_url=career_url
        )
        coverage = repository.get_company_coverage(company_id)
        entity = next(iter(items))
        properties = []
        if career_url:
            properties.append(f"P10311 ({career_url})")
            stats["career_urls_imported"] += 1
        if website:
            properties.append(f"P856 ({website})")
            stats["websites_imported"] += 1
        repository.set_company_disposition(
            company_id,
            str(coverage["disposition"]),
            reason=(
                f"Canonical company URL imported by exact SEC CIK {cik}: "
                f"Wikidata {entity} P5531 -> {', '.join(properties)}"
            ),
            actor=actor.strip(),
            reviewed_at=now,
        )
    stats["ready_candidates"] = ready_candidates
    stats["ready_candidates_truncated"] = ready_candidate_count > len(ready_candidates)
    return stats
