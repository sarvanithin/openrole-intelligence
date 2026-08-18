"""Freeze prioritized, exact-evidence acquisition work into immutable plans."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime

from fortune_intel.importers.wikidata_websites import normalize_sec_cik
from fortune_intel.services.source_provenance import (
    candidate_has_primary_provenance,
    verified_company_seed_evidence,
)
from fortune_intel.storage import AcquisitionTaskSeed, JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url

ACQUISITION_STAGES = ("website", "discovery", "activation")
ACQUISITION_SCOPES = ("all", *ACQUISITION_STAGES)
SUPPORTED_ACTIVATION_KINDS = frozenset(
    {
        "adp_workforce_now",
        "ashby",
        "greenhouse",
        "icims_public",
        "lever",
        "oracle_recruiting",
        "smartrecruiters",
        "ukg_recruiting_public",
        "workday",
    }
)


def _valid_cik(value: object) -> str:
    try:
        return normalize_sec_cik(str(value or ""))
    except ValueError:
        return ""


def _company_snapshot(
    company: Mapping[str, object], coverage: Mapping[str, object]
) -> dict[str, object]:
    return {
        "id": int(company["id"]),
        "name": str(company["name"]),
        "sec_cik": str(company.get("sec_cik") or ""),
        "ticker": str(company.get("ticker") or ""),
        "website_url": str(company.get("website_url") or ""),
        "career_url": str(company.get("career_url") or ""),
        "coverage_disposition": str(company.get("coverage_disposition") or "unreviewed"),
        "coverage_reason": str(coverage.get("reason") or ""),
        "last_discovered_at": str(coverage.get("last_discovered_at") or ""),
    }


def _priority_by_company(repository: JobRepository) -> dict[int, tuple[int, list[str]]]:
    with repository.connect() as connection:
        rows = connection.execute(
            """SELECT c.id,
                EXISTS (
                    SELECT 1 FROM sponsorship_facts h
                    WHERE h.company_id = c.id AND h.entity_match_confidence = 1.0
                      AND h.match_method IN (
                          'reviewed_legal_name_domain', 'reviewed_exact_legal_name'
                      )
                      AND (h.lca_worker_positions > 0 OR h.initial_approvals > 0)
                ) AS exact_h1b,
                EXISTS (
                    SELECT 1 FROM career_sources s
                    WHERE s.company_id = c.id AND s.last_success_at IS NOT NULL
                      AND (s.enabled = 0 OR s.consecutive_failures > 0)
                ) AS stale_success,
                CASE WHEN c.sec_cik IS NOT NULL AND c.sec_cik != '' THEN 1 ELSE 0 END
                    AS sec_identified
            FROM companies c"""
        ).fetchall()
    priorities: dict[int, tuple[int, list[str]]] = {}
    for row in rows:
        exact_h1b = bool(row["exact_h1b"])
        stale_success = bool(row["stale_success"])
        sec_identified = bool(row["sec_identified"])
        if exact_h1b and stale_success:
            rank = 0
        elif exact_h1b:
            rank = 1
        elif stale_success:
            rank = 2
        elif sec_identified:
            rank = 3
        else:
            rank = 4
        reasons = []
        if exact_h1b:
            reasons.append("exact_reviewed_h1b")
        if stale_success:
            reasons.append("stale_previously_successful_source")
        if sec_identified:
            reasons.append("exact_sec_cik")
        priorities[int(row["id"])] = (rank, reasons or ["general_missing_coverage"])
    return priorities


def create_acquisition_plan(
    repository: JobRepository,
    *,
    name: str,
    scope: str,
    actor: str,
    company_ids: set[int] | None = None,
    policy_urls: Mapping[str, str] | None = None,
    policy_approved_at: str = "",
    sync_interval_minutes: int = 60,
    created_at: str | datetime | None = None,
) -> str:
    """Freeze currently eligible exact-identity and verified-seed work."""

    normalized_scope = scope.strip().casefold()
    if normalized_scope not in ACQUISITION_SCOPES:
        raise ValueError("scope must be all, website, discovery, or activation")
    if not 15 <= sync_interval_minutes <= 10_080:
        raise ValueError("sync_interval_minutes must be between 15 and 10080")
    normalized_policies = {
        str(kind).strip().casefold(): normalize_public_url(url, field=f"{kind} policy URL")
        for kind, url in (policy_urls or {}).items()
    }
    unknown = set(normalized_policies) - SUPPORTED_ACTIVATION_KINDS
    if unknown:
        raise ValueError(f"unsupported policy kind(s): {', '.join(sorted(unknown))}")
    if normalized_scope in {"all", "activation"} and normalized_policies:
        try:
            reviewed_at = datetime.fromisoformat(policy_approved_at)
        except ValueError as error:
            raise ValueError("policy_approved_at must be an ISO-8601 timestamp") from error
        if reviewed_at.tzinfo is None:
            raise ValueError("policy_approved_at must include a timezone")
    all_companies = sorted(
        repository.list_companies(include_synthetic=False), key=lambda item: int(item["id"])
    )
    cik_counts = Counter(_valid_cik(company.get("sec_cik")) for company in all_companies)
    companies = all_companies
    if company_ids is not None:
        companies = [company for company in companies if int(company["id"]) in company_ids]
    with repository.connect() as connection:
        coverage_by_company = {
            int(row["company_id"]): dict(row)
            for row in connection.execute("SELECT * FROM company_coverage")
        }
    priorities = _priority_by_company(repository)
    tasks: list[AcquisitionTaskSeed] = []
    for company in companies:
        company_id = int(company["id"])
        rank, reasons = priorities.get(company_id, (4, ["general_missing_coverage"]))
        priority = {"priority_rank": rank, "priority_reasons": reasons}
        snapshot = _company_snapshot(company, coverage_by_company.get(company_id, {}))
        cik = _valid_cik(company.get("sec_cik"))
        if normalized_scope in {"all", "website"} and not snapshot["website_url"]:
            identity_ready = bool(cik and cik_counts[cik] == 1)
            identity_reason = (
                "exact_sec_cik"
                if identity_ready
                else "missing_sec_cik"
                if not cik
                else "ambiguous_sec_cik"
            )
            tasks.append(
                AcquisitionTaskSeed(
                    company_id,
                    str(company["name"]),
                    "website",
                    company_snapshot=snapshot,
                    stage_snapshot={
                        "identity_method": (
                            "exact_sec_cik" if identity_ready else "identity_unavailable"
                        ),
                        "identity_reason": identity_reason,
                        "sec_cik": cik,
                        "wikidata_properties": ["P5531", "P856", "P10311"],
                        **priority,
                    },
                )
            )
        website, career, evidence = verified_company_seed_evidence(repository, company)
        if normalized_scope in {"all", "discovery"} and (
            snapshot["coverage_disposition"] != "supported" and (website or career)
        ):
            tasks.append(
                AcquisitionTaskSeed(
                    company_id,
                    str(company["name"]),
                    "discovery",
                    company_snapshot=snapshot,
                    stage_snapshot={
                        "website_url": website,
                        "career_url": career,
                        "verification_events": evidence,
                        **priority,
                    },
                )
            )
        if normalized_scope in {"all", "activation"} and normalized_policies:
            verified_urls = {url for url in (website, career) if url}
            eligible = [
                candidate
                for candidate in repository.list_source_candidates(company_id)
                if candidate["status"] == "discovered"
                and candidate["kind"] in normalized_policies
                and candidate_has_primary_provenance(candidate, verified_urls)
            ]
            if eligible:
                candidate = min(
                    eligible,
                    key=lambda item: (-float(item["confidence"]), int(item["id"])),
                )
                tasks.append(
                    AcquisitionTaskSeed(
                        company_id,
                        str(company["name"]),
                        "activation",
                        company_snapshot=snapshot,
                        stage_snapshot={
                            "candidate_id": int(candidate["id"]),
                            "candidate_url": str(candidate["candidate_url"]),
                            "kind": str(candidate["kind"]),
                            "policy_url": normalized_policies[str(candidate["kind"])],
                            "policy_approved_at": policy_approved_at,
                            "sync_interval_minutes": sync_interval_minutes,
                            "verification_events": evidence,
                            **priority,
                        },
                    )
                )
    return repository.create_acquisition_plan(name, tasks, actor=actor, created_at=created_at)
