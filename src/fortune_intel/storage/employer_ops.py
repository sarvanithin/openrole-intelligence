"""Official H-1B employer-directory operations."""

from __future__ import annotations

from typing import Any

from fortune_intel.storage.identity import normalize_company_name


class EmployerOperationsMixin:
    def exact_h1b_link_inventory(self) -> dict[str, Any]:
        """Return immutable inputs for exact latest-year legal-name linking."""

        with self.connect() as connection:
            latest = connection.execute(
                "SELECT MAX(fiscal_year) AS fiscal_year FROM h1b_employers"
            ).fetchone()["fiscal_year"]
            companies = connection.execute(
                """SELECT id, name, normalized_name
                FROM companies WHERE is_synthetic = 0
                ORDER BY normalized_name, id"""
            ).fetchall()
            employers = (
                connection.execute(
                    """SELECT id, normalized_name, employer_name, fiscal_year,
                        lca_worker_positions, source, source_url, source_document,
                        source_checksum, imported_at
                    FROM h1b_employers WHERE fiscal_year = ?
                    ORDER BY normalized_name, source, id""",
                    (latest,),
                ).fetchall()
                if latest is not None
                else []
            )
        return {
            "latest_fiscal_year": int(latest) if latest is not None else None,
            "companies": [dict(row) for row in companies],
            "employers": [dict(row) for row in employers],
        }

    def get_sponsorship_fact(
        self, company_id: int, *, source: str, fiscal_year: int
    ) -> dict[str, Any] | None:
        """Return one source/year fact so bulk promotion can remain idempotent."""

        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM sponsorship_facts
                WHERE company_id = ? AND source = ? AND fiscal_year = ?""",
                (company_id, source, fiscal_year),
            ).fetchone()
        return dict(row) if row is not None else None

    def upsert_h1b_employer(
        self,
        employer_name: str,
        *,
        fiscal_year: int,
        lca_worker_positions: int,
        source: str,
        source_url: str,
        source_document: str,
        source_checksum: str,
        imported_at: str,
    ) -> None:
        normalized = normalize_company_name(employer_name)
        if not normalized:
            raise ValueError("employer name is required")
        if lca_worker_positions < 0:
            raise ValueError("worker positions cannot be negative")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO h1b_employers (
                    normalized_name, employer_name, fiscal_year, lca_worker_positions,
                    source, source_url, source_document, source_checksum, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_name, source, fiscal_year) DO UPDATE SET
                    employer_name = excluded.employer_name,
                    lca_worker_positions = excluded.lca_worker_positions,
                    source_url = excluded.source_url,
                    source_document = excluded.source_document,
                    source_checksum = excluded.source_checksum,
                    imported_at = excluded.imported_at
                """,
                (
                    normalized,
                    employer_name.strip(),
                    fiscal_year,
                    lca_worker_positions,
                    source,
                    source_url,
                    source_document,
                    source_checksum,
                    imported_at,
                ),
            )

    def list_h1b_employers(
        self, *, query: str = "", limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = [f"%{query}%"] if query else []
        parameters.extend([min(max(limit, 1), 100), max(offset, 0)])
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY normalized_name ORDER BY fiscal_year DESC, imported_at DESC
                    ) AS recency_rank
                    FROM h1b_employers
                )
                SELECT employer_name, fiscal_year, lca_worker_positions,
                    source, source_url, imported_at
                FROM ranked WHERE recency_rank = 1
                  {"AND employer_name LIKE ?" if query else ""}
                ORDER BY lca_worker_positions DESC, employer_name
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def count_h1b_employers(self, *, query: str = "") -> int:
        parameters: tuple[str, ...] = (f"%{query}%",) if query else ()
        with self.connect() as connection:
            row = connection.execute(
                f"""SELECT COUNT(DISTINCT normalized_name) AS count
                FROM h1b_employers {"WHERE employer_name LIKE ?" if query else ""}""",
                parameters,
            ).fetchone()
        assert row is not None
        return int(row["count"])

    def h1b_overview(self) -> dict[str, int | None]:
        with self.connect() as connection:
            latest = connection.execute(
                "SELECT MAX(fiscal_year) AS fiscal_year FROM h1b_employers"
            ).fetchone()["fiscal_year"]
            if latest is None:
                return {"employers": 0, "latest_fiscal_year": None, "worker_positions": 0}
            row = connection.execute(
                """SELECT COUNT(*) AS employers,
                    COALESCE(SUM(lca_worker_positions), 0) AS worker_positions
                FROM h1b_employers WHERE fiscal_year = ?""",
                (latest,),
            ).fetchone()
        return {
            "employers": int(row["employers"]),
            "latest_fiscal_year": int(latest),
            "worker_positions": int(row["worker_positions"]),
        }

    def link_reviewed_h1b_employer(
        self,
        company_id: int,
        *,
        employer_name: str,
        fiscal_year: int,
        match_method: str = "reviewed_legal_name_domain",
    ) -> None:
        normalized = normalize_company_name(employer_name)
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM h1b_employers
                WHERE normalized_name = ? AND fiscal_year = ?""",
                (normalized, fiscal_year),
            ).fetchall()
        if len(rows) != 1:
            raise ValueError("official H-1B employer record was not uniquely resolved")
        fact = rows[0]
        self.record_sponsorship_fact(
            company_id,
            source=str(fact["source"]),
            fiscal_year=int(fact["fiscal_year"]),
            lca_worker_positions=int(fact["lca_worker_positions"]),
            entity_match_confidence=1.0,
            source_url=str(fact["source_url"]),
            source_document=str(fact["source_document"]),
            source_checksum=str(fact["source_checksum"]),
            match_method=match_method,
        )
