"""Build transparent work batches for verified career-source acquisition."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol


class DiscoveryPriorityRepository(Protocol):
    def list_discovery_priority_targets(
        self, *, limit: int, offset: int, include_synthetic: bool
    ) -> list[dict[str, Any]]: ...

    def discovery_priority_overview(self, *, include_synthetic: bool) -> dict[str, Any]: ...


def build_discovery_priority_report(
    repository: DiscoveryPriorityRepository,
    *,
    batch_size: int = 100,
    batch_number: int = 1,
    include_synthetic: bool = False,
) -> dict[str, Any]:
    """Return a ranked batch and the evidence behind every rank.

    H-1B priority is granted only by the repository's exact-review allowlist.
    The report never creates company/employer links and never infers a URL.
    """
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    if batch_number < 1:
        raise ValueError("batch_number must be at least 1")
    offset = (batch_number - 1) * batch_size
    targets = repository.list_discovery_priority_targets(
        limit=batch_size,
        offset=offset,
        include_synthetic=include_synthetic,
    )
    overview = repository.discovery_priority_overview(include_synthetic=include_synthetic)
    results = []
    for position, target in enumerate(targets, start=offset + 1):
        exact_h1b = bool(target["exact_reviewed_h1b"])
        sec_identified = bool(target["sec_identified"])
        reasons = [f"coverage:{target['coverage_disposition']}"]
        if exact_h1b:
            reasons.append(
                "exact_reviewed_h1b:"
                f"fy{target['h1b_fiscal_year']}:"
                f"{target['lca_worker_positions']}lca_positions:"
                f"{target['initial_approvals']}initial_approvals"
            )
        if sec_identified:
            reasons.append(f"sec_cik:{target['sec_cik']}")
        reasons.append(f"action:{target['next_action']}")
        results.append(
            {
                **target,
                "rank": position,
                "exact_reviewed_h1b": exact_h1b,
                "sec_identified": sec_identified,
                "priority_reasons": reasons,
            }
        )
    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "batch_number": batch_number,
        "batch_size": batch_size,
        "offset": offset,
        "returned": len(results),
        "overview": overview,
        "ranking_policy": {
            "bands": ["h1b_sec", "h1b", "sec", "general"],
            "h1b_requirement": (
                "confidence=1.0, positive activity, and allowlisted exact-review method"
            ),
            "url_policy": "No URL is inferred or guessed by this report.",
        },
        "targets": results,
    }
