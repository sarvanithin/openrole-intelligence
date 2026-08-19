"""Auditable discovery-priority queries.

Only facts linked through an explicit legal-entity review are treated as H-1B
evidence here.  Provisional normalized-name matches are intentionally excluded.
"""

from __future__ import annotations

from typing import Any

EXACT_REVIEW_METHODS = (
    "reviewed_legal_name_domain",
    "reviewed_exact_legal_name",
)

_METHOD_PLACEHOLDERS = ", ".join("?" for _ in EXACT_REVIEW_METHODS)

_PRIORITY_CTES = f"""
WITH exact_h1b_facts AS (
    SELECT company_id, fiscal_year, source, source_url, source_document,
        source_checksum, match_method, lca_worker_positions, initial_approvals
    FROM sponsorship_facts
    WHERE entity_match_confidence = 1.0
      AND match_method IN ({_METHOD_PLACEHOLDERS})
      AND (lca_worker_positions > 0 OR initial_approvals > 0)
),
latest_h1b_year AS (
    SELECT company_id, MAX(fiscal_year) AS fiscal_year
    FROM exact_h1b_facts
    GROUP BY company_id
),
exact_h1b AS (
    SELECT f.company_id, f.fiscal_year,
        SUM(f.lca_worker_positions) AS lca_worker_positions,
        SUM(f.initial_approvals) AS initial_approvals,
        GROUP_CONCAT(DISTINCT f.source) AS evidence_sources,
        GROUP_CONCAT(DISTINCT f.source_url) AS evidence_urls,
        GROUP_CONCAT(DISTINCT f.source_document) AS evidence_documents,
        GROUP_CONCAT(DISTINCT f.source_checksum) AS evidence_checksums,
        GROUP_CONCAT(DISTINCT f.match_method) AS match_methods
    FROM exact_h1b_facts f
    JOIN latest_h1b_year y
      ON y.company_id = f.company_id AND y.fiscal_year = f.fiscal_year
    GROUP BY f.company_id, f.fiscal_year
),
target_base AS (
    SELECT c.id AS company_id, c.name, c.slug, c.sec_cik, c.ticker,
        c.website_url, COALESCE(cc.disposition, 'unreviewed') AS coverage_disposition,
        COALESCE(cc.reason, '') AS coverage_reason,
        cc.last_discovered_at, cc.last_reviewed_at,
        CASE WHEN h.company_id IS NULL THEN 0 ELSE 1 END AS exact_reviewed_h1b,
        h.fiscal_year AS h1b_fiscal_year,
        COALESCE(h.lca_worker_positions, 0) AS lca_worker_positions,
        COALESCE(h.initial_approvals, 0) AS initial_approvals,
        COALESCE(h.evidence_sources, '') AS h1b_evidence_sources,
        COALESCE(h.evidence_urls, '') AS h1b_evidence_urls,
        COALESCE(h.evidence_documents, '') AS h1b_evidence_documents,
        COALESCE(h.evidence_checksums, '') AS h1b_evidence_checksums,
        COALESCE(h.match_methods, '') AS h1b_match_methods,
        CASE WHEN c.sec_cik IS NOT NULL AND c.sec_cik != '' THEN 1 ELSE 0 END
            AS sec_identified,
        COALESCE((SELECT COUNT(*) FROM career_source_candidates d
            WHERE d.company_id = c.id
              AND d.status IN ('discovered', 'reviewed')), 0) AS reviewable_candidates,
        COALESCE((SELECT COUNT(*) FROM career_source_candidates d
            WHERE d.company_id = c.id AND d.status = 'approved'), 0)
            AS approved_candidates
    FROM companies c
    LEFT JOIN company_coverage cc ON cc.company_id = c.id
    LEFT JOIN exact_h1b h ON h.company_id = c.id
    WHERE (? = 1 OR c.is_synthetic = 0)
      AND NOT EXISTS (
          SELECT 1 FROM career_sources s
          WHERE s.company_id = c.id AND s.enabled = 1
            AND s.policy_approved_at IS NOT NULL
      )
),
prioritized AS (
    SELECT *,
        CASE
            WHEN exact_reviewed_h1b = 1 AND sec_identified = 1 THEN 'h1b_sec'
            WHEN exact_reviewed_h1b = 1 THEN 'h1b'
            WHEN sec_identified = 1 THEN 'sec'
            ELSE 'general'
        END AS priority_band,
        CASE
            WHEN approved_candidates > 0 THEN 'activate_approved_candidate'
            WHEN reviewable_candidates > 0 THEN 'review_source_candidate'
            WHEN website_url IS NULL OR website_url = '' THEN 'acquire_verified_website'
            ELSE 'discover_career_source'
        END AS next_action,
        CASE
            WHEN exact_reviewed_h1b = 1 AND sec_identified = 1 THEN 0
            WHEN exact_reviewed_h1b = 1 THEN 1
            WHEN sec_identified = 1 THEN 2
            ELSE 3
        END AS band_rank,
        CASE
            WHEN approved_candidates > 0 THEN 0
            WHEN reviewable_candidates > 0 THEN 1
            WHEN website_url IS NOT NULL AND website_url != '' THEN 2
            ELSE 3
        END AS action_rank,
        CASE coverage_disposition
            WHEN 'stale' THEN 0
            WHEN 'candidate' THEN 1
            WHEN 'unreviewed' THEN 2
            WHEN 'no_source' THEN 3
            WHEN 'blocked' THEN 4
            WHEN 'unsupported' THEN 5
            ELSE 6
        END AS coverage_rank
    FROM target_base
)
"""


def _base_parameters(include_synthetic: bool) -> tuple[Any, ...]:
    return (*EXACT_REVIEW_METHODS, int(include_synthetic))


class PriorityOperationsMixin:
    """Repository operations used to create deterministic acquisition batches."""

    def list_discovery_priority_targets(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        include_synthetic: bool = False,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 1000)
        safe_offset = max(offset, 0)
        with self.connect() as connection:
            rows = connection.execute(
                _PRIORITY_CTES
                + """
                SELECT * FROM prioritized
                ORDER BY band_rank, action_rank,
                    (lca_worker_positions + initial_approvals) DESC,
                    coverage_rank, name, company_id
                LIMIT ? OFFSET ?
                """,
                (*_base_parameters(include_synthetic), safe_limit, safe_offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def discovery_priority_overview(self, *, include_synthetic: bool = False) -> dict[str, Any]:
        parameters = _base_parameters(include_synthetic)
        with self.connect() as connection:
            totals = connection.execute(
                _PRIORITY_CTES
                + """
                SELECT COUNT(*) AS total_targets,
                    SUM(exact_reviewed_h1b) AS exact_reviewed_h1b,
                    SUM(sec_identified) AS sec_identified,
                    SUM(CASE WHEN exact_reviewed_h1b = 1 AND sec_identified = 1
                        THEN 1 ELSE 0 END) AS h1b_and_sec,
                    SUM(CASE WHEN website_url IS NULL OR website_url = ''
                        THEN 1 ELSE 0 END) AS websites_missing
                FROM prioritized
                """,
                parameters,
            ).fetchone()
            by_action = connection.execute(
                _PRIORITY_CTES
                + """SELECT next_action, COUNT(*) AS count FROM prioritized
                    GROUP BY next_action ORDER BY next_action""",
                parameters,
            ).fetchall()
            by_band = connection.execute(
                _PRIORITY_CTES
                + """SELECT priority_band, COUNT(*) AS count FROM prioritized
                    GROUP BY priority_band ORDER BY MIN(band_rank)""",
                parameters,
            ).fetchall()
            by_coverage = connection.execute(
                _PRIORITY_CTES
                + """SELECT coverage_disposition, COUNT(*) AS count FROM prioritized
                    GROUP BY coverage_disposition ORDER BY MIN(coverage_rank)""",
                parameters,
            ).fetchall()
        assert totals is not None
        result = {key: int(value or 0) for key, value in dict(totals).items()}
        result["by_action"] = {row["next_action"]: row["count"] for row in by_action}
        result["by_priority_band"] = {row["priority_band"]: row["count"] for row in by_band}
        result["by_coverage"] = {row["coverage_disposition"]: row["count"] for row in by_coverage}
        return result
