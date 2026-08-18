"""Service boundary for strict, passive-only fingerprint normalization."""

from __future__ import annotations

from typing import Any

from fortune_intel.discovery.passive import classify_passive_ats_url

STRICT_PASSIVE_FAMILIES = frozenset(
    {
        "adp",
        "avature",
        "dayforce",
        "eightfold",
        "icims",
        "jobvite",
        "paycom",
        "paycor",
        "paylocity",
        "rippling",
        "successfactors",
        "taleo",
        "ukg",
    }
)


def _strict_family(url: str) -> str | None:
    fingerprint = classify_passive_ats_url(
        url,
        origin_page="stored passive fingerprint reclassification",
    )
    if fingerprint is None or fingerprint.family not in STRICT_PASSIVE_FAMILIES:
        return None
    return fingerprint.family


def reclassify_passive_fingerprints(
    repository: Any,
    *,
    actor: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Correct known-family metadata while leaving candidates and sources untouched."""

    return repository.reclassify_source_fingerprints(
        _strict_family,
        actor=actor,
        dry_run=dry_run,
    )
