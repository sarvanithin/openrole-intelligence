"""Safely turn recorded search results into primary ATS candidates.

Search is a *lead acquisition* mechanism only.  This module never derives a
URL from a company name, follows a search-result redirect, or saves a result
which has not been re-fetched from the public ATS and matched to the exact
company identity.  It intentionally creates a reviewable candidate, never a
live source.

The search provider is deliberately outside this module.  A caller supplies a
recorded JSONL result export from a provider it is permitted to use.  Keeping
the provider adapter separate makes the raw result, query, rank, and retrieval
time durable audit evidence instead of silently scraping an undocumented search
endpoint.
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping

from fortune_intel.discovery.ats import AtsSourceCandidate, classify_ats_url
from fortune_intel.services.licensed_lead_verification import (
    LeadPage,
    _identity_surface,
    fetch_lead_page,
)
from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url

_COMPANY_WORD = re.compile(r"[a-z0-9]+")
_CAREER_QUERY_WORD = re.compile(r"\b(career|careers|job|jobs|employment|hiring)\b", re.I)
_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,79}$", re.I)
_MAX_RESULTS_PER_RUN = 2_000
_MAX_CONCURRENCY = 20


@dataclass(frozen=True, slots=True)
class RecordedSearchResult:
    """One result returned by a permitted search provider.

    ``result_url`` must be the final public ATS URL shown by the provider.  It
    may not be a provider redirect or a URL constructed by this application.
    """

    company_id: int
    company_name: str
    query: str
    provider: str
    result_url: str
    retrieved_at: str
    rank: int | None = None


def _required(row: Mapping[str, object], field: str, *, line: int) -> str:
    value = str(row.get(field, "")).strip()
    if not value:
        raise ValueError(f"line {line}: {field} is required")
    return value


def _timestamp(value: str, *, line: int) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"line {line}: retrieved_at must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"line {line}: retrieved_at must include a timezone")
    return parsed.isoformat()


def recorded_search_result(row: Mapping[str, object], *, line: int) -> RecordedSearchResult:
    """Validate provenance before any network call or database write."""

    try:
        company_id = int(_required(row, "company_id", line=line))
    except ValueError as error:
        raise ValueError(f"line {line}: company_id must be a positive integer") from error
    if company_id < 1:
        raise ValueError(f"line {line}: company_id must be a positive integer")
    company_name = _required(row, "company_name", line=line)
    query = _required(row, "query", line=line)
    provider = _required(row, "provider", line=line).casefold()
    if _PROVIDER.fullmatch(provider) is None:
        raise ValueError(f"line {line}: provider must be a compact provider identifier")
    company_words = {word for word in _COMPANY_WORD.findall(company_name.casefold()) if len(word) > 2}
    query_words = set(_COMPANY_WORD.findall(query.casefold()))
    if not company_words.intersection(query_words) or _CAREER_QUERY_WORD.search(query) is None:
        raise ValueError(
            f"line {line}: query must include the company name and a careers/jobs term"
        )
    result_url = normalize_public_url(_required(row, "result_url", line=line), field="result_url")
    if not result_url.startswith("https://"):
        raise ValueError(f"line {line}: result_url must use HTTPS")
    raw_rank = str(row.get("rank", "")).strip()
    rank = None
    if raw_rank:
        try:
            rank = int(raw_rank)
        except ValueError as error:
            raise ValueError(f"line {line}: rank must be a positive integer") from error
        if rank < 1:
            raise ValueError(f"line {line}: rank must be a positive integer")
    return RecordedSearchResult(
        company_id=company_id,
        company_name=company_name,
        query=query,
        provider=provider,
        result_url=result_url,
        retrieved_at=_timestamp(_required(row, "retrieved_at", line=line), line=line),
        rank=rank,
    )


def load_recorded_search_results(path: str | Path) -> list[RecordedSearchResult]:
    """Read a bounded, auditable JSONL result export.

    The complete file is validated before caller processing begins; malformed
    provenance therefore cannot produce a partial, misleading harvest.
    """

    entries: list[RecordedSearchResult] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError(f"line {line_number}: invalid JSON") from error
            if not isinstance(item, dict):
                raise ValueError(f"line {line_number}: each JSONL entry must be an object")
            entries.append(recorded_search_result(item, line=line_number))
    return entries


def _company_has_any_career_artifact(repository: JobRepository, company_id: int) -> bool:
    """Do not compete with a previously discovered career portal."""

    with repository.connect() as connection:
        return bool(
            connection.execute(
                """SELECT EXISTS(
                    SELECT 1 FROM career_sources WHERE company_id = ?
                    UNION ALL SELECT 1 FROM career_source_candidates WHERE company_id = ?
                    UNION ALL SELECT 1 FROM career_source_fingerprints WHERE company_id = ?
                ) AS found""",
                (company_id, company_id, company_id),
            ).fetchone()["found"]
        )


def harvest_verified_search_ats_results(
    repository: JobRepository,
    results: Iterable[RecordedSearchResult],
    *,
    actor: str,
    policy_urls: Mapping[str, str],
    policy_approved_at: str,
    concurrency: int = 4,
    page_fetcher: Callable[[str], LeadPage] = fetch_lead_page,
) -> dict[str, int]:
    """Verify recorded results and retain only exact, policy-supported ATS boards.

    This operation intentionally has no source activation path.  A returned
    candidate still needs the existing exhaustive-manifest approval workflow.
    """

    if not actor.strip():
        raise ValueError("actor is required")
    if not policy_approved_at.strip():
        raise ValueError("policy_approved_at is required")
    validated = list(results)
    if len(validated) > _MAX_RESULTS_PER_RUN:
        raise ValueError(f"at most {_MAX_RESULTS_PER_RUN} recorded results may be harvested per run")
    if not 1 <= concurrency <= _MAX_CONCURRENCY:
        raise ValueError(f"concurrency must be between 1 and {_MAX_CONCURRENCY}")
    seen: set[tuple[int, str]] = set()
    with repository.connect() as connection:
        for item in validated:
            key = (item.company_id, item.result_url)
            if key in seen:
                raise ValueError(f"duplicate result_url for company_id {item.company_id}")
            seen.add(key)
            company = connection.execute(
                "SELECT name FROM companies WHERE id = ?", (item.company_id,)
            ).fetchone()
            if company is None or str(company["name"]) != item.company_name:
                raise ValueError("exact company identity mismatch: company_id and company_name must match")

    report = {
        "input": len(validated),
        "missing_career_url": 0,
        "not_supported_ats": 0,
        "redirected": 0,
        "identity_rejected": 0,
        "verified_candidates": 0,
        "skipped_existing_career_artifact": 0,
    }
    ready: list[tuple[RecordedSearchResult, AtsSourceCandidate]] = []
    for item in validated:
        if _company_has_any_career_artifact(repository, item.company_id):
            report["skipped_existing_career_artifact"] += 1
            continue
        report["missing_career_url"] += 1
        found = classify_ats_url(item.result_url, origin="recorded permitted search result")
        if found is None or found.connector_kind not in policy_urls:
            report["not_supported_ats"] += 1
            continue
        ready.append((item, found))

    # Network work is deliberately bounded.  Database mutations remain below in
    # the calling thread, so each accepted record is written atomically and the
    # result is deterministic regardless of fetch completion order.
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        pages = list(executor.map(lambda pair: page_fetcher(pair[1].normalized_base_url), ready))
    for (item, found), page in zip(ready, pages, strict=True):
        if page.final_url != found.normalized_base_url:
            report["redirected"] += 1
            continue
        identity = _identity_surface(item.company_name, page)
        if identity is None:
            report["identity_rejected"] += 1
            continue
        surface, title = identity
        repository.upsert_source_candidate(
            item.company_id,
            candidate_url=found.normalized_base_url,
            kind=found.connector_kind,
            confidence=0.98,
            evidence={
                "review_method": "recorded_search_result_direct_primary_ats_identity",
                "verification_status": "verified",
                "search_result": {
                    "provider": item.provider,
                    "query": item.query,
                    "rank": item.rank,
                    "result_url": item.result_url,
                    "retrieved_at": item.retrieved_at,
                },
                "identity_check": {
                    "method": "direct_ats_html_exact_normalized_company_name",
                    "surface": surface,
                    "title": title,
                    "status": page.status,
                    "content_type": page.content_type,
                    "body_sha256": hashlib.sha256(page.body).hexdigest(),
                },
                "board_token": found.board_token,
            },
            terms_url=policy_urls[found.connector_kind],
            terms_status="permitted",
            terms_reviewed_at=policy_approved_at,
            discovered_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        )
        coverage = repository.get_company_coverage(item.company_id)
        if coverage is not None and str(coverage["disposition"]) != "supported":
            repository.set_company_disposition(
                item.company_id,
                "candidate",
                reason="Recorded search result verified on the exact public ATS board",
                actor=actor,
            )
        report["verified_candidates"] += 1
    return report
