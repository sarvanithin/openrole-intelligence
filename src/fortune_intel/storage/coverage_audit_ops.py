"""Strict, auditable per-company coverage checklist queries."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

COVERAGE_AUDIT_GATES = (
    "identity_verified",
    "portal_seed_verified",
    "ats_candidate_discovered",
    "complete_manifest_approved",
    "successful_platform_ingestion",
    "opening_date_provenance_complete",
    "fresh",
)

COVERAGE_AUDIT_DEFINITION = {
    "identity_verified": "A valid exact SEC CIK is stored for the company identity.",
    "portal_seed_verified": (
        "An official jobs URL was joined by exact CIK, or an approved ATS candidate was "
        "reached from an independently verified official website."
    ),
    "ats_candidate_discovered": (
        "A supported ATS candidate has non-empty discovery evidence and was not rejected."
    ),
    "complete_manifest_approved": (
        "Every enabled policy-approved source passed a recorded complete-manifest probe or "
        "a complete successful ingestion."
    ),
    "successful_platform_ingestion": (
        "Every enabled policy-approved source completed a successful manifest ingestion; "
        "the public active-job count includes definite U.S. locations only."
    ),
    "opening_date_provenance_complete": (
        "Every active public U.S. job has an opening date supplied by the source; "
        "first-seen fallback dates do not pass this gate."
    ),
    "fresh": (
        "Every enabled policy-approved source succeeded within twice its configured interval, "
        "with a minimum tolerance of 120 minutes."
    ),
    "covered": "All seven gates pass. A directory listing or supported disposition is not enough.",
}


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    return current.astimezone(UTC)


def _valid_cik(value: object) -> bool:
    cik = str(value or "")
    return len(cik) == 10 and cik.isdigit() and cik != "0000000000"


def _timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def coverage_audit_summary(records: list[dict[str, Any]]) -> dict[str, object]:
    """Return gate totals without weakening the strict per-company definition."""
    return {
        "companies": len(records),
        "covered": sum(bool(record["covered"]) for record in records),
        "gates": {
            gate: sum(bool(record[gate]) for record in records) for gate in COVERAGE_AUDIT_GATES
        },
        "next_actions": dict(Counter(str(record["next_action"]) for record in records)),
    }


class CoverageAuditOperationsMixin:
    def company_coverage_audit(
        self,
        *,
        include_synthetic: bool = False,
        query: str = "",
        audit_status: str = "all",
        as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Build the strict checklist for every matching company.

        ``audit_status`` filters on this checklist's final result, not the looser
        operational ``company_coverage.disposition`` field.
        """
        if audit_status not in {"all", "covered", "incomplete"}:
            raise ValueError("audit_status must be all, covered, or incomplete")
        observed_at = _as_utc(as_of)
        with self.connect() as connection:
            rows = connection.execute(
                """
                WITH event_flags AS (
                    SELECT company_id,
                        MAX(reason LIKE '%Canonical company URL imported by exact SEC CIK%'
                            AND reason LIKE '%P10311%') AS exact_cik_career_provenance,
                        MAX(reason LIKE '%Canonical company URL imported by exact SEC CIK%P856%'
                            OR reason LIKE 'Canonical website seed verified from %')
                            AS verified_website_provenance
                    FROM company_coverage_events GROUP BY company_id
                ), candidate_counts AS (
                    SELECT company_id,
                        SUM(status NOT IN ('rejected', 'superseded')
                            AND evidence_json NOT IN ('', '{}')) AS evidenced_candidates,
                        SUM(status = 'approved' AND robots_status = 'allowed'
                            AND terms_status = 'permitted' AND reviewed_by != ''
                            AND evidence_json NOT IN ('', '{}')) AS verified_candidates
                    FROM career_source_candidates GROUP BY company_id
                ), approved_candidate_urls AS (
                    SELECT DISTINCT company_id, candidate_url
                    FROM career_source_candidates
                    WHERE status = 'approved' AND terms_status = 'permitted'
                      AND review_notes LIKE '%complete manifest%'
                ), successful_sync AS (
                    SELECT company_id, source, MAX(finished_at) AS finished_at
                    FROM sync_runs WHERE status = 'success' AND complete = 1
                    GROUP BY company_id, source
                ), source_stats AS (
                    SELECT s.company_id, COUNT(*) AS approved_sources,
                        SUM(d.company_id IS NOT NULL OR r.company_id IS NOT NULL)
                            AS manifest_verified_sources,
                        SUM(r.company_id IS NOT NULL) AS ingested_sources
                    FROM career_sources s
                    LEFT JOIN approved_candidate_urls d
                        ON d.company_id = s.company_id AND d.candidate_url = s.base_url
                    LEFT JOIN successful_sync r ON r.company_id = s.company_id
                        AND r.source = s.kind || ':' || s.board_token
                    WHERE s.enabled = 1 AND s.policy_approved_at IS NOT NULL
                    GROUP BY s.company_id
                ), job_stats AS (
                    SELECT company_id, COUNT(*) AS active_jobs,
                        SUM(posted_at IS NOT NULL) AS jobs_with_source_opened_at
                    FROM jobs WHERE status = 'active' AND us_eligibility = 'eligible'
                    GROUP BY company_id
                ), sync_stats AS (
                    SELECT company_id, MAX(finished_at) AS last_complete_ingestion_at
                    FROM successful_sync GROUP BY company_id
                )
                SELECT c.id, c.name, c.slug, c.sec_cik, c.ticker, c.career_url,
                    COALESCE(cc.disposition, 'unreviewed') AS coverage_disposition,
                    COALESCE(e.exact_cik_career_provenance, 0)
                        AS exact_cik_career_provenance,
                    COALESCE(e.verified_website_provenance, 0)
                        AS verified_website_provenance,
                    COALESCE(d.evidenced_candidates, 0) AS evidenced_candidates,
                    COALESCE(d.verified_candidates, 0) AS verified_candidates,
                    COALESCE(s.approved_sources, 0) AS approved_sources,
                    COALESCE(s.manifest_verified_sources, 0) AS manifest_verified_sources,
                    COALESCE(s.ingested_sources, 0) AS ingested_sources,
                    COALESCE(j.active_jobs, 0) AS active_jobs,
                    COALESCE(j.jobs_with_source_opened_at, 0) AS jobs_with_source_opened_at,
                    y.last_complete_ingestion_at
                FROM companies c
                LEFT JOIN company_coverage cc ON cc.company_id = c.id
                LEFT JOIN event_flags e ON e.company_id = c.id
                LEFT JOIN candidate_counts d ON d.company_id = c.id
                LEFT JOIN source_stats s ON s.company_id = c.id
                LEFT JOIN job_stats j ON j.company_id = c.id
                LEFT JOIN sync_stats y ON y.company_id = c.id
                WHERE (? = 1 OR c.is_synthetic = 0)
                ORDER BY c.name, c.id
                """,
                (int(include_synthetic),),
            ).fetchall()
            source_rows = connection.execute(
                """SELECT company_id, sync_interval_minutes, last_success_at
                FROM career_sources
                WHERE enabled = 1 AND policy_approved_at IS NOT NULL"""
            ).fetchall()

        sources: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for source in source_rows:
            sources[int(source["company_id"])].append(dict(source))

        needle = query.strip().casefold()
        result = []
        for raw in rows:
            row = dict(raw)
            if needle and needle not in str(row["name"]).casefold():
                continue
            company_sources = sources[int(row["id"])]
            identity = _valid_cik(row["sec_cik"])
            portal = identity and bool(
                (row["career_url"] and row["exact_cik_career_provenance"])
                or (row["verified_website_provenance"] and row["verified_candidates"])
            )
            candidate = bool(row["evidenced_candidates"])
            approved_count = int(row["approved_sources"])
            manifest = (
                approved_count > 0 and int(row["manifest_verified_sources"]) == approved_count
            )
            ingestion = approved_count > 0 and int(row["ingested_sources"]) == approved_count
            date_provenance = ingestion and int(row["jobs_with_source_opened_at"]) == int(
                row["active_jobs"]
            )
            stale_sources = 0
            for source in company_sources:
                succeeded_at = _timestamp(source["last_success_at"])
                tolerance = max(120, 2 * int(source["sync_interval_minutes"]))
                if (
                    succeeded_at is None
                    or (observed_at - succeeded_at).total_seconds() > tolerance * 60
                ):
                    stale_sources += 1
            fresh = approved_count > 0 and stale_sources == 0
            gates = (identity, portal, candidate, manifest, ingestion, date_provenance, fresh)
            covered = all(gates)
            if audit_status == "covered" and not covered:
                continue
            if audit_status == "incomplete" and covered:
                continue
            next_action = _next_action(dict(zip(COVERAGE_AUDIT_GATES, gates, strict=True)))
            result.append(
                {
                    "company_id": int(row["id"]),
                    "company_name": row["name"],
                    "company_slug": row["slug"],
                    "sec_cik": row["sec_cik"] or "",
                    "ticker": row["ticker"] or "",
                    "portal_seed_url": row["career_url"] or "",
                    "coverage_disposition": row["coverage_disposition"],
                    "identity_verified": identity,
                    "portal_seed_verified": portal,
                    "ats_candidate_discovered": candidate,
                    "complete_manifest_approved": manifest,
                    "successful_platform_ingestion": ingestion,
                    "opening_date_provenance_complete": date_provenance,
                    "fresh": fresh,
                    "covered": covered,
                    "completed_gates": sum(gates),
                    "total_gates": len(COVERAGE_AUDIT_GATES),
                    "next_action": next_action,
                    "evidenced_candidates": int(row["evidenced_candidates"]),
                    "approved_sources": approved_count,
                    "manifest_verified_sources": int(row["manifest_verified_sources"]),
                    "ingested_sources": int(row["ingested_sources"]),
                    "stale_sources": stale_sources,
                    "active_jobs": int(row["active_jobs"]),
                    "jobs_with_source_opened_at": int(row["jobs_with_source_opened_at"]),
                    "first_seen_fallback_jobs": int(row["active_jobs"])
                    - int(row["jobs_with_source_opened_at"]),
                    "last_complete_ingestion_at": row["last_complete_ingestion_at"],
                    "audit_as_of": observed_at.replace(microsecond=0).isoformat(),
                }
            )
        return result


def _next_action(gates: dict[str, bool]) -> str:
    actions = {
        "identity_verified": "verify_exact_company_identity",
        "portal_seed_verified": "verify_official_job_portal",
        "ats_candidate_discovered": "discover_supported_ats_source",
        "complete_manifest_approved": "review_policy_and_complete_manifest",
        "successful_platform_ingestion": "run_complete_platform_ingestion",
        "opening_date_provenance_complete": "resolve_opening_date_provenance",
        "fresh": "restore_source_freshness",
    }
    for gate in COVERAGE_AUDIT_GATES:
        if not gates[gate]:
            return actions[gate]
    return "complete"
