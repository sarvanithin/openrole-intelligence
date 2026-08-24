"""SQLite repository for the local-first MVP.

The public interfaces intentionally map cleanly to a future PostgreSQL adapter.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fortune_intel.services.sponsorship import EmployerHistory
from fortune_intel.storage.coverage_ops import normalize_public_url
from fortune_intel.storage.identity import normalize_company_name, slugify
from fortune_intel.storage.repository_ops import RepositoryOperations
from fortune_intel.storage.schema import initialize_schema


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class JobRepository(RepositoryOperations):
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            initialize_schema(connection)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def upsert_company(
        self,
        name: str,
        *,
        career_url: str = "",
        ats_type: str = "",
        collection_name: str | None = None,
        collection_year: int | None = None,
        collection_rank: int | None = None,
        sec_cik: str | int = "",
        ticker: str = "",
        website_url: str = "",
        is_synthetic: bool = False,
    ) -> int:
        now = utc_now()
        slug = slugify(name)
        cik = str(sec_cik).strip()
        if cik and (not cik.isdigit() or len(cik) > 10):
            raise ValueError("sec_cik must contain at most 10 digits")
        cik = cik.zfill(10) if cik else ""
        corporate_url = normalize_public_url(website_url, field="website_url", optional=True)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO companies (
                    slug, name, normalized_name, career_url, ats_type,
                    collection_name, collection_year, collection_rank,
                    sec_cik, ticker, website_url, is_synthetic, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name = excluded.name,
                    career_url = CASE WHEN excluded.career_url != '' THEN excluded.career_url ELSE companies.career_url END,
                    ats_type = CASE WHEN excluded.ats_type != '' THEN excluded.ats_type ELSE companies.ats_type END,
                    collection_name = COALESCE(excluded.collection_name, companies.collection_name),
                    collection_year = COALESCE(excluded.collection_year, companies.collection_year),
                    collection_rank = COALESCE(excluded.collection_rank, companies.collection_rank),
                    sec_cik = CASE WHEN excluded.sec_cik != '' THEN excluded.sec_cik ELSE companies.sec_cik END,
                    ticker = CASE WHEN excluded.ticker != '' THEN excluded.ticker ELSE companies.ticker END,
                    website_url = CASE WHEN excluded.website_url != '' THEN excluded.website_url ELSE companies.website_url END,
                    is_synthetic = excluded.is_synthetic,
                    updated_at = excluded.updated_at
                """,
                (
                    slug,
                    name.strip(),
                    normalize_company_name(name),
                    career_url.strip(),
                    ats_type.strip().lower(),
                    collection_name,
                    collection_year,
                    collection_rank,
                    cik,
                    ticker.strip().upper(),
                    corporate_url,
                    int(is_synthetic),
                    now,
                    now,
                ),
            )
            row = connection.execute("SELECT id FROM companies WHERE slug = ?", (slug,)).fetchone()
            assert row is not None
            return int(row["id"])

    def find_company_by_normalized_name(self, name: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM companies WHERE normalized_name = ?",
                (normalize_company_name(name),),
            ).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None

    def record_sponsorship_fact(
        self,
        company_id: int,
        *,
        source: str,
        fiscal_year: int,
        initial_approvals: int = 0,
        initial_denials: int = 0,
        lca_worker_positions: int = 0,
        entity_match_confidence: float,
        source_url: str = "",
        source_document: str = "",
        source_checksum: str = "",
        match_method: str = "reviewed",
    ) -> None:
        if not 0 <= entity_match_confidence <= 1:
            raise ValueError("entity_match_confidence must be between 0 and 1")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sponsorship_facts (
                    company_id, source, fiscal_year, initial_approvals,
                    initial_denials, lca_worker_positions, entity_match_confidence,
                    source_url, source_document, source_checksum, match_method,
                    imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, source, fiscal_year) DO UPDATE SET
                    initial_approvals = excluded.initial_approvals,
                    initial_denials = excluded.initial_denials,
                    lca_worker_positions = excluded.lca_worker_positions,
                    entity_match_confidence = excluded.entity_match_confidence,
                    source_url = excluded.source_url,
                    source_document = excluded.source_document,
                    source_checksum = excluded.source_checksum,
                    match_method = excluded.match_method,
                    imported_at = excluded.imported_at
                """,
                (
                    company_id,
                    source,
                    fiscal_year,
                    initial_approvals,
                    initial_denials,
                    lca_worker_positions,
                    entity_match_confidence,
                    source_url,
                    source_document,
                    source_checksum,
                    match_method,
                    utc_now(),
                ),
            )

    def get_employer_history(self, company_id: int) -> EmployerHistory:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    fiscal_year, initial_approvals, initial_denials,
                    lca_worker_positions, entity_match_confidence
                FROM sponsorship_facts
                WHERE company_id = ? AND fiscal_year >= CAST(strftime('%Y', 'now') AS INTEGER) - 4
                """,
                (company_id,),
            ).fetchall()
        if not rows:
            return EmployerHistory()
        current_year = datetime.now(UTC).year

        def weight(year: int) -> float:
            return max(0.2, 1 - (0.2 * max(0, current_year - int(year))))

        return EmployerHistory(
            uscis_initial_approvals=round(
                sum(row["initial_approvals"] * weight(row["fiscal_year"]) for row in rows)
            ),
            uscis_initial_denials=round(
                sum(row["initial_denials"] * weight(row["fiscal_year"]) for row in rows)
            ),
            lca_worker_positions=round(
                sum(row["lca_worker_positions"] * weight(row["fiscal_year"]) for row in rows)
            ),
            latest_fiscal_year=max(int(row["fiscal_year"]) for row in rows),
            entity_match_confidence=min(float(row["entity_match_confidence"]) for row in rows),
            recency_weighted=True,
        )

    def start_sync_run(self, source: str, company_id: int | None = None) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO sync_runs (source, company_id, status, started_at) VALUES (?, ?, 'running', ?)",
                (source, company_id, utc_now()),
            )
            return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        complete: bool,
        jobs_seen: int,
        error_message: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE sync_runs SET status = ?, complete = ?, jobs_seen = ?,
                    error_message = ?, finished_at = ? WHERE id = ?
                """,
                (status, int(complete), jobs_seen, error_message, utc_now(), run_id),
            )

    def list_jobs(
        self,
        *,
        query: str = "",
        company: str = "",
        location: str = "",
        tier: str = "",
        opened_within_days: int = 0,
        status: str = "active",
        limit: int = 50,
        offset: int = 0,
        include_synthetic: bool = True,
        us_eligibility: str = "",
    ) -> list[dict[str, Any]]:
        clauses = ["j.status = ?"]
        params: list[Any] = [status]
        if not include_synthetic:
            clauses.append("c.is_synthetic = 0")
        if us_eligibility:
            if us_eligibility not in {"eligible", "ineligible", "unknown"}:
                raise ValueError("invalid us_eligibility")
            clauses.append("j.us_eligibility = ?")
            params.append(us_eligibility)
        if query:
            clauses.append("(j.title LIKE ? OR j.description LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if company:
            clauses.append("c.slug = ?")
            params.append(company)
        if location:
            clauses.append("j.location LIKE ?")
            params.append(f"%{location}%")
        if tier:
            clauses.append("j.sponsorship_tier = ?")
            params.append(tier.upper())
        if opened_within_days:
            if not 1 <= opened_within_days <= 365:
                raise ValueError("opened_within_days must be between 1 and 365")
            clauses.append("j.posted_at IS NOT NULL AND datetime(j.posted_at) >= datetime('now', ?)")
            params.append(f"-{opened_within_days} days")
        params.extend([min(max(limit, 1), 200), max(offset, 0)])
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT j.id, j.title, j.location, j.canonical_url AS url,
                    j.posted_at AS source_opened_at, j.first_seen_at,
                    COALESCE(j.posted_at, j.first_seen_at) AS display_date,
                    CASE WHEN j.posted_at IS NOT NULL
                        THEN 'source_opened_at' ELSE 'first_seen_at'
                    END AS date_provenance,
                    j.last_seen_at, j.status,
                    j.sponsorship_tier, j.sponsorship_evidence_score,
                    j.sponsorship_reasons, j.sponsorship_excerpt,
                    j.sponsorship_rule_version, j.us_eligibility,
                    c.name AS company_name, c.slug AS company_slug, c.ats_type
                FROM jobs j JOIN companies c ON c.id = j.company_id
                WHERE {" AND ".join(clauses)}
                ORDER BY datetime(COALESCE(j.posted_at, j.first_seen_at)) DESC,
                    j.title ASC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        results = [dict(row) for row in rows]
        for result in results:
            result["sponsorship_reasons"] = json.loads(result["sponsorship_reasons"])
        return results

    def count_jobs(
        self,
        *,
        query: str = "",
        company: str = "",
        location: str = "",
        tier: str = "",
        opened_within_days: int = 0,
        status: str = "active",
        include_synthetic: bool = True,
        us_eligibility: str = "",
    ) -> int:
        clauses = ["j.status = ?"]
        params: list[Any] = [status]
        if not include_synthetic:
            clauses.append("c.is_synthetic = 0")
        if us_eligibility:
            if us_eligibility not in {"eligible", "ineligible", "unknown"}:
                raise ValueError("invalid us_eligibility")
            clauses.append("j.us_eligibility = ?")
            params.append(us_eligibility)
        if query:
            clauses.append("(j.title LIKE ? OR j.description LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if company:
            clauses.append("c.slug = ?")
            params.append(company)
        if location:
            clauses.append("j.location LIKE ?")
            params.append(f"%{location}%")
        if tier:
            clauses.append("j.sponsorship_tier = ?")
            params.append(tier.upper())
        if opened_within_days:
            if not 1 <= opened_within_days <= 365:
                raise ValueError("opened_within_days must be between 1 and 365")
            clauses.append("j.posted_at IS NOT NULL AND datetime(j.posted_at) >= datetime('now', ?)")
            params.append(f"-{opened_within_days} days")
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS count FROM jobs j
                JOIN companies c ON c.id = j.company_id
                WHERE {" AND ".join(clauses)}
                """,
                params,
            ).fetchone()
        assert row is not None
        return int(row["count"])
