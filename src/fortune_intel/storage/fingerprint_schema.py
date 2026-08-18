"""Schema-v10 migration for expanded passive fingerprint families."""

from __future__ import annotations

import sqlite3

from fortune_intel.storage.coverage_schema import COVERAGE_FINGERPRINT_DDL

_NEW_FAMILIES = ("paycom", "paycor", "paylocity", "rippling")
_LEGACY_TABLE = "career_source_fingerprints_v9"


def migrate_fingerprint_family_constraint(connection: sqlite3.Connection) -> bool:
    """Rebuild the v9 table only when its CHECK constraint lacks v10 families."""

    row = connection.execute(
        """SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'career_source_fingerprints'"""
    ).fetchone()
    if row is None or all(f"'{family}'" in str(row[0]) for family in _NEW_FAMILIES):
        return False
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_LEGACY_TABLE,),
    ).fetchone():
        raise RuntimeError(f"cannot migrate while {_LEGACY_TABLE} already exists")
    connection.execute(f"ALTER TABLE career_source_fingerprints RENAME TO {_LEGACY_TABLE}")
    connection.execute("DROP INDEX IF EXISTS idx_fingerprints_family_company")
    connection.execute(COVERAGE_FINGERPRINT_DDL)
    connection.execute(
        f"""INSERT INTO career_source_fingerprints (
            id, company_id, observed_url, family, host, evidence_json,
            observation_count, first_seen_at, last_seen_at, last_observed_by
        ) SELECT id, company_id, observed_url, family, host, evidence_json,
            observation_count, first_seen_at, last_seen_at, last_observed_by
        FROM {_LEGACY_TABLE}"""
    )
    connection.execute(f"DROP TABLE {_LEGACY_TABLE}")
    connection.execute(
        """CREATE INDEX idx_fingerprints_family_company
        ON career_source_fingerprints(family, company_id)"""
    )
    return True
