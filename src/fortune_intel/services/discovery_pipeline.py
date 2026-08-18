"""Persist bounded career-source discoveries as reviewable candidates."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fortune_intel.discovery import (
    AtsSourceCandidate,
    CareerSourceDiscovery,
    DiscoveryReport,
    PassiveSourceFingerprint,
    classify_ats_url,
)
from fortune_intel.storage import JobRepository


_BLOCKED_DISPOSITIONS = frozenset(
    {"robots_denied", "unsafe_redirect", "fetch_failed", "rejected_start_url"}
)


def _seed_urls(company: dict[str, Any]) -> tuple[str, ...]:
    """Return distinct operator-verified seeds without inventing paths or hosts."""

    seeds: list[str] = []
    identities: set[str] = set()
    for field in ("career_url", "website_url"):
        value = str(company.get(field) or "").strip()
        if not value:
            continue
        # A trailing slash does not identify a different crawl seed. No other
        # normalization is done here: validation belongs to discovery itself.
        identity = value.rstrip("/")
        if identity in identities:
            continue
        identities.add(identity)
        seeds.append(value)
    return tuple(seeds)


def _discover_seed(
    start_url: str,
    factory: Callable[[], CareerSourceDiscovery],
) -> DiscoveryReport:
    direct = classify_ats_url(
        start_url,
        origin="operator-provided verified company URL",
    )
    if direct is not None:
        return DiscoveryReport(
            start_url=start_url,
            disposition="candidates_found",
            candidates=(direct,),
            evidence=(
                "verified seed has an exact supported ATS URL shape; external host was not fetched",
                "discovery candidates require connector probing and policy approval",
            ),
            pages_checked=(),
        )
    try:
        return factory().discover(start_url)
    except Exception as error:
        # One malformed or unexpectedly failing site must not abort a batch of
        # otherwise independent companies. Do not persist the exception text;
        # third-party clients may include credentials or response bodies in it.
        return DiscoveryReport(
            start_url=start_url,
            disposition="fetch_failed",
            candidates=(),
            evidence=(f"discovery raised {type(error).__name__}; seed failed closed",),
            pages_checked=(),
        )


def _discover_one(
    company: dict[str, Any],
    factory: Callable[[], CareerSourceDiscovery],
) -> tuple[dict[str, Any], tuple[DiscoveryReport, ...]]:
    return company, tuple(_discover_seed(seed, factory) for seed in _seed_urls(company))


def _candidate_key(candidate: AtsSourceCandidate) -> tuple[str, str, str]:
    return (
        candidate.connector_kind,
        candidate.board_token.casefold(),
        candidate.normalized_base_url.casefold(),
    )


def _fingerprint_key(fingerprint: PassiveSourceFingerprint) -> tuple[str, str]:
    return fingerprint.family, fingerprint.observed_url


def _unique_strings(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def discover_company_sources(
    repository: JobRepository,
    companies: Iterable[dict[str, Any]],
    *,
    actor: str,
    concurrency: int = 4,
    discovery_factory: Callable[[], CareerSourceDiscovery] = CareerSourceDiscovery,
) -> list[dict[str, object]]:
    """Discover and persist candidates; never approve or enable them."""
    if not actor.strip():
        raise ValueError("actor is required for auditable discovery")
    targets = [company for company in companies if _seed_urls(company)]
    workers = max(1, min(concurrency, 8))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        discovered = list(
            executor.map(lambda company: _discover_one(company, discovery_factory), targets)
        )
    results: list[dict[str, object]] = []
    for company, reports in discovered:
        company_id = int(company["id"])
        pages_checked = _unique_strings(page for report in reports for page in report.pages_checked)
        discovery_evidence = _unique_strings(
            evidence for report in reports for evidence in report.evidence
        )
        candidates: dict[tuple[str, str, str], AtsSourceCandidate] = {}
        candidate_evidence: dict[tuple[str, str, str], list[str]] = {}
        candidate_urls: dict[tuple[str, str, str], list[str]] = {}
        fingerprints: dict[tuple[str, str], PassiveSourceFingerprint] = {}
        for report in reports:
            for candidate in report.candidates:
                key = _candidate_key(candidate)
                existing = candidates.get(key)
                if existing is None or candidate.confidence > existing.confidence:
                    candidates[key] = candidate
                candidate_evidence.setdefault(key, []).extend(candidate.evidence)
                candidate_urls.setdefault(key, []).append(candidate.candidate_url)
            for fingerprint in report.fingerprints:
                fingerprints[_fingerprint_key(fingerprint)] = fingerprint

        candidate_ids: list[int] = []
        for key in sorted(candidates):
            candidate = candidates[key]
            candidate_ids.append(
                repository.upsert_source_candidate(
                    company_id,
                    candidate_url=candidate.normalized_base_url,
                    kind=candidate.connector_kind,
                    confidence=candidate.confidence,
                    evidence={
                        "board_token": candidate.board_token,
                        "candidate_url": candidate.candidate_url,
                        "candidate_urls": _unique_strings(candidate_urls[key]),
                        "candidate_evidence": _unique_strings(candidate_evidence[key]),
                        "discovery_evidence": discovery_evidence,
                        "pages_checked": pages_checked,
                        "seed_urls_checked": [report.start_url for report in reports],
                    },
                    # Robots was checked for crawled company pages, not for the
                    # external ATS endpoint itself. Keep that policy unknown
                    # until the review/probe workflow verifies the source.
                    robots_status="unknown",
                    terms_status="review_required",
                )
            )
        fingerprint_ids: list[int] = []
        for key in sorted(fingerprints):
            fingerprint = fingerprints[key]
            fingerprint_ids.append(
                repository.upsert_source_fingerprint(
                    company_id,
                    observed_url=fingerprint.observed_url,
                    family=fingerprint.family,
                    evidence={
                        "host": fingerprint.host,
                        "origin_page": fingerprint.origin_page,
                        "fingerprint_evidence": list(fingerprint.evidence),
                        "seed_urls_checked": [report.start_url for report in reports],
                    },
                    actor=actor,
                )
            )
        if candidate_ids:
            disposition = "candidate"
            reason = f"Discovered {len(candidate_ids)} supported ATS candidate(s); review required"
        elif any(report.disposition in _BLOCKED_DISPOSITIONS for report in reports):
            disposition = "blocked"
            blocked = _unique_strings(
                report.disposition
                for report in reports
                if report.disposition in _BLOCKED_DISPOSITIONS
            )
            reason = f"Discovery blocked for one or more verified seeds: {', '.join(blocked)}"
        else:
            disposition = "unsupported"
            reason = "No supported deterministic ATS source was found in the bounded crawl"
            if fingerprint_ids:
                reason += f"; retained {len(fingerprint_ids)} passive fingerprint(s)"
        current = repository.get_company_coverage(company_id)
        if current is None or current["disposition"] != "supported":
            repository.set_company_disposition(
                company_id,
                disposition,
                reason=reason,
                actor=actor,
            )
        repository.mark_company_discovered(company_id)
        results.append(
            {
                "company_id": company_id,
                "company_name": company["name"],
                "disposition": disposition,
                "candidate_ids": candidate_ids,
                "fingerprint_ids": fingerprint_ids,
                "pages_checked": len(pages_checked),
                "seed_urls_checked": len(reports),
            }
        )
    return results
