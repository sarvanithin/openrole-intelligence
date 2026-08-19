"""Probe and activate batches of operator-reviewed ATS candidates."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fortune_intel.services.source_approval import (
    CompleteEmptyObservationPending,
    approve_source_candidate,
)
from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url

SUPPORTED_KINDS = (
    "adp_workforce_now",
    "amazon_jobs",
    "apple_jobs",
    "ashby",
    "greenhouse",
    "icims_public",
    "lever",
    "oracle_recruiting",
    "official_structured",
    "smartrecruiters",
    "ukg_recruiting_public",
    "workday",
)


def _pending_candidates(
    repository: JobRepository,
    *,
    kinds: tuple[str, ...],
    limit: int,
    candidate_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    if not kinds or candidate_ids == set():
        return []
    kind_placeholders = ", ".join("?" for _ in kinds)
    id_filter = ""
    parameters: list[object] = list(kinds)
    if candidate_ids is not None:
        id_placeholders = ", ".join("?" for _ in candidate_ids)
        id_filter = f" AND id IN ({id_placeholders})"
        parameters.extend(sorted(candidate_ids))
    with repository.connect() as connection:
        rows = connection.execute(
            f"""SELECT id, kind FROM career_source_candidates
            WHERE status = 'discovered' AND kind IN ({kind_placeholders}) {id_filter}
            ORDER BY confidence DESC, id ASC LIMIT ?""",
            (*parameters, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def approve_discovered_sources(
    repository: JobRepository,
    *,
    policy_urls: dict[str, str],
    policy_approved_at: str,
    actor: str,
    limit: int = 500,
    concurrency: int = 4,
    sync_interval_minutes: int = 60,
    candidate_ids: set[int] | None = None,
) -> dict[str, object]:
    """Probe candidates and activate complete manifests after safety checks.

    Supplying a policy URL is an explicit operator review decision. Probe
    failures remain discovered so they can be corrected or retried later.
    """
    if not actor.strip():
        raise ValueError("actor is required")
    if not 1 <= limit <= 10_000:
        raise ValueError("limit must be between 1 and 10000")
    if not 1 <= concurrency <= 8:
        raise ValueError("concurrency must be between 1 and 8")
    if candidate_ids is not None and any(candidate_id < 1 for candidate_id in candidate_ids):
        raise ValueError("candidate IDs must be positive")
    normalized_policies = {
        kind.casefold(): normalize_public_url(url, field=f"{kind} policy URL")
        for kind, url in policy_urls.items()
    }
    unknown = set(normalized_policies) - set(SUPPORTED_KINDS)
    if unknown:
        raise ValueError(f"unsupported policy kind(s): {', '.join(sorted(unknown))}")
    candidates = _pending_candidates(
        repository,
        kinds=tuple(sorted(normalized_policies)),
        limit=limit,
        candidate_ids=candidate_ids,
    )

    def activate(candidate: dict[str, Any]) -> dict[str, object]:
        candidate_id = int(candidate["id"])
        kind = str(candidate["kind"])
        try:
            source_id = approve_source_candidate(
                repository,
                candidate_id,
                terms_url=normalized_policies[kind],
                policy_approved_at=policy_approved_at,
                actor=actor,
                sync_interval_minutes=sync_interval_minutes,
            )
        except CompleteEmptyObservationPending as error:
            return {
                "candidate_id": candidate_id,
                "kind": kind,
                "status": "empty_pending_verification",
                "error": str(error),
            }
        except (RuntimeError, ValueError) as error:
            return {
                "candidate_id": candidate_id,
                "kind": kind,
                "status": "probe_failed",
                "error": str(error),
            }
        return {
            "candidate_id": candidate_id,
            "kind": kind,
            "status": "activated",
            "source_id": source_id,
        }

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(activate, candidates))
    statuses = Counter(str(result["status"]) for result in results)
    return {
        "candidates_selected": len(candidates),
        "activated": statuses["activated"],
        "empty_pending_verification": statuses["empty_pending_verification"],
        "probe_failed": statuses["probe_failed"],
        "results": results,
    }
