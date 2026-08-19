"""Durably drain verified-career-seed checks outside the acquisition scheduler."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

from fortune_intel.services.bulk_source_approval import approve_discovered_sources
from fortune_intel.services.discovery_pipeline import discover_company_sources
from fortune_intel.services.registry_career_portal_verification import (
    LeadPage,
    _default_resolver,
    fetch_lead_page,
    promote_verified_registry_career_portals,
)
from fortune_intel.storage import JobRepository

_AUTO_APPROVAL_KINDS = frozenset(
    {
        "amazon_jobs",
        "apple_jobs",
        "ashby",
        "greenhouse",
        "lever",
        "oracle_recruiting",
        "smartrecruiters",
        "workday",
    }
)


def run_registry_career_portal_verifier(
    repository: JobRepository,
    *,
    actor: str,
    batch_size: int = 200,
    concurrency: int = 8,
    shard_count: int = 1,
    shard_index: int = 0,
    max_batches: int = 100,
    pace_seconds: float = 0.5,
    resolver: Callable[[str], Iterable[str]] = _default_resolver,
    page_fetcher: Callable[[str], LeadPage] = fetch_lead_page,
    sleep: Callable[[float], None] = time.sleep,
    verifier: Callable[..., dict[str, int]] = promote_verified_registry_career_portals,
    discovery_runner: Callable[..., list[dict[str, object]]] = discover_company_sources,
    discovery_concurrency: int = 4,
    policy_urls: dict[str, str] | None = None,
    policy_approved_at: str = "",
    approval_concurrency: int = 4,
    sync_interval_minutes: int = 60,
    approval_runner: Callable[..., dict[str, object]] = approve_discovered_sources,
) -> dict[str, object]:
    """Process finite durable batches until the registry is drained or stalls.

    Each verifier call commits its terminal status before the next batch starts.
    A process restart therefore resumes from the database's remaining
    ``verification_status=unverified`` rows; no in-memory checkpoint is trusted.
    Newly verified company IDs are handed directly to the bounded discovery
    pipeline. Newly discovered standard ATS candidates can then use the
    existing complete-manifest approval gate, but only when the caller supplies
    an explicit policy URL and timestamp for that connector.
    """

    if not actor.strip():
        raise ValueError("actor is required")
    if not 1 <= batch_size <= 1_000:
        raise ValueError("batch_size must be between 1 and 1000")
    if not 1 <= concurrency <= 8:
        raise ValueError("concurrency must be between 1 and 8")
    if not 1 <= shard_count <= 32:
        raise ValueError("shard_count must be between 1 and 32")
    if not 0 <= shard_index < shard_count:
        raise ValueError("shard_index must be between 0 and shard_count - 1")
    if not 1 <= max_batches <= 1_000:
        raise ValueError("max_batches must be between 1 and 1000")
    if not 0 <= pace_seconds <= 60:
        raise ValueError("pace_seconds must be between 0 and 60")
    if not 1 <= discovery_concurrency <= 4:
        raise ValueError("discovery_concurrency must be between 1 and 4")
    if not 1 <= approval_concurrency <= 4:
        raise ValueError("approval_concurrency must be between 1 and 4")
    if not 15 <= sync_interval_minutes <= 10_080:
        raise ValueError("sync_interval_minutes must be between 15 and 10080")
    configured_policies = {
        str(kind).strip().casefold(): str(url).strip()
        for kind, url in (policy_urls or {}).items()
        if str(kind).strip().casefold() in _AUTO_APPROVAL_KINDS and str(url).strip()
    }
    if configured_policies and not policy_approved_at.strip():
        raise ValueError("policy_approved_at is required when approval policies are configured")

    totals = {"scanned": 0, "verified": 0, "rejected": 0, "skipped": 0}
    handoff_totals = {"companies": 0, "candidates": 0, "fingerprints": 0}
    approval_totals = {
        "candidates_selected": 0,
        "activated": 0,
        "empty_pending_verification": 0,
        "probe_failed": 0,
    }
    batches: list[dict[str, object]] = []
    status = "batch_limit_reached"
    for batch_number in range(1, max_batches + 1):
        report = verifier(
            repository,
            actor=actor,
            limit=batch_size,
            concurrency=concurrency,
            shard_count=shard_count,
            shard_index=shard_index,
            resolver=resolver,
            page_fetcher=page_fetcher,
        )
        batch: dict[str, object] = {"batch": batch_number, **report}
        verified_ids = tuple(
            int(company_id)
            for company_id in getattr(report, "verified_company_ids", ())
            if isinstance(company_id, int) and company_id > 0
        )
        if verified_ids:
            companies = _companies_by_id(repository, verified_ids)
            discoveries = discovery_runner(
                repository,
                companies,
                actor=f"{actor}:registry-seed-discovery",
                concurrency=discovery_concurrency,
            )
            handoff = {
                "company_ids": list(verified_ids),
                "companies": len(discoveries),
                "candidates": sum(len(item["candidate_ids"]) for item in discoveries),
                "fingerprints": sum(len(item["fingerprint_ids"]) for item in discoveries),
            }
            batch["discovery_handoff"] = handoff
            for key in handoff_totals:
                handoff_totals[key] += int(handoff[key])
            candidate_ids = {
                int(candidate_id)
                for item in discoveries
                for candidate_id in item["candidate_ids"]
                if isinstance(candidate_id, int) and candidate_id > 0
            }
            if configured_policies and candidate_ids:
                approval = approval_runner(
                    repository,
                    policy_urls=configured_policies,
                    policy_approved_at=policy_approved_at,
                    actor=f"{actor}:registry-seed-manifest-approval",
                    limit=len(candidate_ids),
                    concurrency=approval_concurrency,
                    sync_interval_minutes=sync_interval_minutes,
                    candidate_ids=candidate_ids,
                )
                batch["manifest_approval"] = approval
                for key in approval_totals:
                    approval_totals[key] += int(approval.get(key, 0))
        batches.append(batch)
        for key in totals:
            totals[key] += report[key]
        if report["scanned"] == 0:
            status = "drained"
            break
        terminal = report["verified"] + report["rejected"]
        if terminal == 0:
            # Do not hot-loop malformed or otherwise non-actionable rows.  The
            # report preserves the exact count for an operator to inspect.
            status = "no_progress"
            break
        if batch_number < max_batches and pace_seconds:
            sleep(pace_seconds)
    return {
        "status": status,
        "actor": actor,
        "batch_size": batch_size,
        "concurrency": concurrency,
        "shard_count": shard_count,
        "shard_index": shard_index,
        "pace_seconds": pace_seconds,
        "batches_completed": len(batches),
        "totals": totals,
        "batches": batches,
        "discovery_handoff": handoff_totals,
        "manifest_approval": approval_totals,
        "activation": "only_via_complete_manifest_gate" if configured_policies else "not_performed",
    }


def _companies_by_id(
    repository: JobRepository, company_ids: tuple[int, ...]
) -> list[dict[str, Any]]:
    """Read only IDs just returned by the verifier, preserving that order."""

    unique_ids = tuple(dict.fromkeys(company_ids))
    placeholders = ", ".join("?" for _ in unique_ids)
    with repository.connect() as connection:
        rows = connection.execute(
            f"SELECT id, name, website_url, career_url FROM companies WHERE id IN ({placeholders})",
            unique_ids,
        ).fetchall()
    by_id = {int(row["id"]): dict(row) for row in rows}
    return [by_id[company_id] for company_id in unique_ids if company_id in by_id]
