"""Primary-company URL provenance checks shared by activation workflows."""

from __future__ import annotations

from collections.abc import Mapping

from fortune_intel.storage import JobRepository


def verified_company_seed_evidence(
    repository: JobRepository,
    company: Mapping[str, object],
) -> tuple[str, str, list[dict[str, object]]]:
    """Return only company URLs backed by a recognized primary-source event."""

    website = str(company.get("website_url") or "")
    career = str(company.get("career_url") or "")
    website_verified = False
    career_verified = False
    evidence: list[dict[str, object]] = []
    for event in repository.company_coverage_events(int(company["id"])):
        reason = str(event.get("reason") or "")
        exact_cik_url = "Canonical company URL imported by exact SEC CIK" in reason
        website_event = reason.startswith(
            ("Canonical website seed verified from ", "SEC Submissions JSON ")
        ) or (exact_cik_url and "P856" in reason)
        career_event = (
            (exact_cik_url and "P10311" in reason)
            or (
                reason.startswith("Canonical website seed verified from ")
                and "reviewed career URL " in reason
            )
            or reason.startswith(
                "Canonical career seed verified by direct public career-page exact company identity at "
            )
        )
        if website and website_event:
            website_verified = True
        if career and career_event and career in reason:
            career_verified = True
        if website_event or career_event:
            evidence.append(
                {
                    "event_id": int(event["id"]),
                    "reason": reason,
                    "actor": str(event.get("actor") or ""),
                    "occurred_at": str(event.get("occurred_at") or ""),
                }
            )
    return (
        website if website_verified else "",
        career if career_verified else "",
        evidence[:10],
    )


def candidate_has_primary_provenance(
    candidate: Mapping[str, object],
    verified_urls: set[str],
) -> bool:
    """Require either an explicit primary-source review or a verified crawl seed."""

    evidence = candidate.get("evidence")
    if not isinstance(evidence, Mapping):
        return False
    if evidence.get("activation_allowed") is False:
        return False
    if str(evidence.get("verification_status") or "").casefold() == "unverified":
        return False
    if evidence.get("review_method") == "primary_source_exact_ats_url":
        return bool(evidence.get("source_url"))
    seeds = evidence.get("seed_urls_checked")
    return bool(
        isinstance(seeds, list)
        and verified_urls
        and any(str(seed) in verified_urls for seed in seeds)
    )
