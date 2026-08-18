"""Company and fleet summary queries."""

from __future__ import annotations

from typing import Any


class SummaryOperationsMixin:
    def list_companies(
        self, *, include_synthetic: bool = True, us_only: bool = False
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.name, c.slug, c.ats_type, c.collection_name,
                    c.collection_year, c.collection_rank, c.sec_cik, c.ticker,
                    c.website_url, c.career_url,
                    COALESCE(cc.disposition, 'unreviewed') AS coverage_disposition,
                    COALESCE((SELECT COUNT(*) FROM jobs j
                        WHERE j.company_id = c.id AND j.status = 'active'
                          AND (? = 0 OR j.us_eligibility = 'eligible')), 0) AS active_jobs,
                    (SELECT MAX(j.last_seen_at) FROM jobs j
                        WHERE j.company_id = c.id
                          AND (? = 0 OR j.us_eligibility = 'eligible')) AS last_verified_at,
                    COALESCE((SELECT COUNT(*) FROM career_sources s
                        WHERE s.company_id = c.id AND s.enabled = 1
                          AND s.policy_approved_at IS NOT NULL), 0) AS approved_sources,
                    (SELECT MAX(s.last_success_at) FROM career_sources s
                        WHERE s.company_id = c.id AND s.enabled = 1
                          AND s.policy_approved_at IS NOT NULL) AS source_last_success_at
                FROM companies c
                LEFT JOIN company_coverage cc ON cc.company_id = c.id
                WHERE (? = 1 OR c.is_synthetic = 0)
                ORDER BY active_jobs DESC, c.name
                """,
                (int(us_only), int(us_only), int(include_synthetic)),
            ).fetchall()
        return [dict(row) for row in rows]

    def coverage_overview(self, *, include_synthetic: bool = True) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT COALESCE(cc.disposition, 'unreviewed') AS disposition,
                    COUNT(*) AS count
                FROM companies c
                LEFT JOIN company_coverage cc ON cc.company_id = c.id
                WHERE (? = 1 OR c.is_synthetic = 0)
                GROUP BY COALESCE(cc.disposition, 'unreviewed')""",
                (int(include_synthetic),),
            ).fetchall()
            totals = connection.execute(
                """WITH company_flags AS (
                    SELECT c.*,
                    CASE WHEN (
                        (TRIM(COALESCE(c.website_url, '')) != '' AND EXISTS (
                            SELECT 1 FROM company_coverage_events e
                            WHERE e.company_id = c.id AND (
                                e.reason LIKE 'Canonical website seed verified from %'
                                OR e.reason LIKE 'SEC Submissions JSON %'
                                OR (e.reason LIKE '%Canonical company URL imported by exact SEC CIK%'
                                    AND INSTR(e.reason, 'P856') > 0)
                            )
                        )) OR (TRIM(COALESCE(c.career_url, '')) != '' AND EXISTS (
                            SELECT 1 FROM company_coverage_events e
                            WHERE e.company_id = c.id AND (
                                (e.reason LIKE '%Canonical company URL imported by exact SEC CIK%'
                                    AND INSTR(e.reason, 'P10311') > 0)
                                OR (e.reason LIKE 'Canonical website seed verified from %'
                                    AND INSTR(e.reason, 'reviewed career URL ') > 0)
                              )
                              AND INSTR(e.reason, c.career_url) > 0
                        )))
                    THEN 1 ELSE 0 END AS verified_seed,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM sponsorship_facts f
                        WHERE f.company_id = c.id
                          AND f.entity_match_confidence = 1.0
                          AND f.match_method IN (
                              'reviewed_legal_name_domain', 'reviewed_exact_legal_name'
                          )
                          AND (f.lca_worker_positions > 0 OR f.initial_approvals > 0)
                    ) THEN 1 ELSE 0 END AS exact_h1b,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM career_source_candidates d WHERE d.company_id = c.id
                    ) THEN 1 ELSE 0 END AS has_candidate,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM career_sources s WHERE s.company_id = c.id
                          AND s.enabled = 1 AND s.policy_approved_at IS NOT NULL
                    ) THEN 1 ELSE 0 END AS has_approved_source,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM career_sources s WHERE s.company_id = c.id
                          AND s.enabled = 1 AND s.policy_approved_at IS NOT NULL
                          AND s.last_success_at IS NOT NULL
                    ) THEN 1 ELSE 0 END AS has_successful_source
                    FROM companies c WHERE (? = 1 OR c.is_synthetic = 0)
                )
                SELECT COUNT(*) AS companies,
                    SUM(CASE WHEN website_url IS NOT NULL AND website_url != ''
                        THEN 1 ELSE 0 END) AS websites_known,
                    SUM(verified_seed) AS companies_with_verified_discovery_seeds,
                    SUM(has_candidate) AS companies_with_candidates,
                    SUM(has_approved_source) AS companies_with_approved_sources,
                    SUM(has_successful_source) AS companies_with_successful_sources,
                    SUM(exact_h1b) AS exact_h1b_companies,
                    SUM(CASE WHEN exact_h1b = 1 AND verified_seed = 1
                        THEN 1 ELSE 0 END) AS exact_h1b_with_verified_discovery_seeds,
                    SUM(CASE WHEN exact_h1b = 1 AND has_successful_source = 1
                        THEN 1 ELSE 0 END) AS exact_h1b_with_successful_sources
                FROM company_flags""",
                (int(include_synthetic),),
            ).fetchone()
        assert totals is not None
        result = dict(totals)
        result["dispositions"] = {row["disposition"]: row["count"] for row in rows}
        return result

    def overview(self, *, include_synthetic: bool = True, us_only: bool = False) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM companies WHERE (? = 1 OR is_synthetic = 0)) AS companies,
                  (
                    SELECT COUNT(DISTINCT s.company_id)
                    FROM career_sources s JOIN companies source_company
                      ON source_company.id = s.company_id
                    WHERE s.enabled = 1 AND s.last_success_at IS NOT NULL
                      AND (? = 1 OR source_company.is_synthetic = 0)
                  ) AS companies_with_successful_job_fetches,
                  COUNT(CASE WHEN j.status = 'active' AND (? = 1 OR c.is_synthetic = 0)
                    AND (? = 0 OR j.us_eligibility = 'eligible') THEN 1 END) AS active_jobs,
                  COUNT(CASE WHEN j.status = 'active' AND j.sponsorship_tier IN ('A','B','C','E')
                    AND (? = 1 OR c.is_synthetic = 0)
                    AND (? = 0 OR j.us_eligibility = 'eligible') THEN 1 END) AS jobs_with_evidence,
                  MAX(CASE WHEN (? = 1 OR c.is_synthetic = 0)
                    AND (? = 0 OR j.us_eligibility = 'eligible') THEN j.last_seen_at END) AS last_verified_at
                FROM jobs j JOIN companies c ON c.id = j.company_id
                """,
                (
                    int(include_synthetic),
                    int(include_synthetic),
                    int(include_synthetic),
                    int(us_only),
                    int(include_synthetic),
                    int(us_only),
                    int(include_synthetic),
                    int(us_only),
                ),
            ).fetchone()
            tiers = connection.execute(
                """SELECT j.sponsorship_tier AS tier, COUNT(*) AS count
                FROM jobs j JOIN companies c ON c.id = j.company_id
                WHERE j.status = 'active' AND (? = 1 OR c.is_synthetic = 0)
                  AND (? = 0 OR j.us_eligibility = 'eligible')
                GROUP BY j.sponsorship_tier""",
                (int(include_synthetic), int(us_only)),
            ).fetchall()
            synthetic = connection.execute(
                "SELECT COUNT(*) FROM companies WHERE collection_name = 'Synthetic demo'"
            ).fetchone()[0]
        assert row is not None
        result = dict(row)
        result["tiers"] = {tier["tier"]: tier["count"] for tier in tiers}
        result["demo_mode"] = bool(result["companies"] and synthetic == result["companies"])
        return result
