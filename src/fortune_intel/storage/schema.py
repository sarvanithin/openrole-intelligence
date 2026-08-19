from __future__ import annotations

import json
import sqlite3

from fortune_intel.storage import acquisition_schema, coverage_schema
from fortune_intel.storage.fingerprint_schema import migrate_fingerprint_family_constraint
from fortune_intel.storage.job_geography import backfill_job_geography

SCHEMA_VERSION = 10

SCHEMA = f"""
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    career_url TEXT,
    ats_type TEXT,
    collection_name TEXT,
    collection_year INTEGER,
    collection_rank INTEGER,
    sec_cik TEXT,
    ticker TEXT,
    website_url TEXT,
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_companies_normalized_name
ON companies(normalized_name);

CREATE TABLE IF NOT EXISTS career_sources (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    board_token TEXT NOT NULL,
    base_url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    sync_interval_minutes INTEGER NOT NULL DEFAULT 360,
    last_started_at TEXT,
    last_success_at TEXT,
    next_sync_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    consecutive_complete_empty_observations INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    terms_url TEXT,
    policy_approved_at TEXT,
    owner_contact TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(company_id, base_url)
);

CREATE INDEX IF NOT EXISTS idx_career_sources_due
ON career_sources(enabled, next_sync_at);

CREATE TABLE IF NOT EXISTS sponsorship_facts (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    initial_approvals INTEGER NOT NULL DEFAULT 0,
    initial_denials INTEGER NOT NULL DEFAULT 0,
    lca_worker_positions INTEGER NOT NULL DEFAULT 0,
    entity_match_confidence REAL NOT NULL,
    source_url TEXT,
    source_document TEXT,
    source_checksum TEXT,
    match_method TEXT NOT NULL DEFAULT 'reviewed',
    imported_at TEXT NOT NULL,
    UNIQUE(company_id, source, fiscal_year)
);

CREATE TABLE IF NOT EXISTS h1b_employers (
    id INTEGER PRIMARY KEY,
    normalized_name TEXT NOT NULL,
    employer_name TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    lca_worker_positions INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_document TEXT NOT NULL,
    source_checksum TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    UNIQUE(normalized_name, source, fiscal_year)
);

CREATE INDEX IF NOT EXISTS idx_h1b_employers_latest
ON h1b_employers(fiscal_year DESC, lca_worker_positions DESC);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    company_id INTEGER REFERENCES companies(id),
    status TEXT NOT NULL,
    complete INTEGER NOT NULL DEFAULT 0,
    jobs_seen INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_started ON sync_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    external_job_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    posted_at TEXT,
    source_updated_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    closed_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    missed_complete_runs INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    cluster_fingerprint TEXT NOT NULL,
    sponsorship_tier TEXT NOT NULL,
    sponsorship_evidence_score INTEGER NOT NULL,
    sponsorship_reasons TEXT NOT NULL,
    sponsorship_excerpt TEXT,
    sponsorship_rule_version TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{{}}',
    us_eligibility TEXT NOT NULL DEFAULT 'unknown'
        CHECK(us_eligibility IN ('eligible', 'ineligible', 'unknown')),
    location_evidence TEXT NOT NULL DEFAULT '{{}}',
    location_rule_version TEXT NOT NULL DEFAULT '',
    UNIQUE(company_id, source, external_job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_company_status ON jobs(company_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_tier_status ON jobs(sponsorship_tier, status);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_cluster ON jobs(cluster_fingerprint);
CREATE INDEX IF NOT EXISTS idx_jobs_us_eligibility_status
ON jobs(us_eligibility, status, posted_at);

CREATE TABLE IF NOT EXISTS job_versions (
    id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(job_id, content_hash)
);

{coverage_schema.COVERAGE_SCHEMA}
{acquisition_schema.ACQUISITION_SCHEMA}

CREATE INDEX IF NOT EXISTS idx_companies_sec_cik ON companies(sec_cik);
CREATE INDEX IF NOT EXISTS idx_companies_ticker ON companies(ticker);

INSERT INTO schema_meta (key, value) VALUES ('schema_version', '10')
ON CONFLICT(key) DO UPDATE SET value = excluded.value;
"""


_ADDED_COLUMNS = {
    "companies": (
        ("is_synthetic", "INTEGER NOT NULL DEFAULT 0"),
        ("sec_cik", "TEXT"),
        ("ticker", "TEXT"),
        ("website_url", "TEXT"),
    ),
    "sponsorship_facts": (
        ("source_document", "TEXT"),
        ("source_checksum", "TEXT"),
        ("match_method", "TEXT NOT NULL DEFAULT 'reviewed'"),
    ),
    "career_sources": (
        ("terms_url", "TEXT"),
        ("policy_approved_at", "TEXT"),
        ("owner_contact", "TEXT"),
        ("consecutive_complete_empty_observations", "INTEGER NOT NULL DEFAULT 0"),
    ),
    "career_source_candidates": (
        ("consecutive_complete_empty_observations", "INTEGER NOT NULL DEFAULT 0"),
    ),
    "jobs": (
        ("us_eligibility", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("location_evidence", "TEXT NOT NULL DEFAULT '{}'"),
        ("location_rule_version", "TEXT NOT NULL DEFAULT ''"),
    ),
}

_AUXILIARY_DDL = (
    """CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS career_sources (
        id INTEGER PRIMARY KEY,
        company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        kind TEXT NOT NULL, board_token TEXT NOT NULL, base_url TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        sync_interval_minutes INTEGER NOT NULL DEFAULT 360,
        last_started_at TEXT, last_success_at TEXT, next_sync_at TEXT,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        consecutive_complete_empty_observations INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        terms_url TEXT, policy_approved_at TEXT, owner_contact TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(company_id, base_url)
    )""",
    """CREATE TABLE IF NOT EXISTS job_versions (
        id INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        content_hash TEXT NOT NULL, snapshot TEXT NOT NULL, observed_at TEXT NOT NULL,
        UNIQUE(job_id, content_hash)
    )""",
    """CREATE TABLE IF NOT EXISTS h1b_employers (
        id INTEGER PRIMARY KEY,
        normalized_name TEXT NOT NULL, employer_name TEXT NOT NULL,
        fiscal_year INTEGER NOT NULL, lca_worker_positions INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL, source_url TEXT NOT NULL, source_document TEXT NOT NULL,
        source_checksum TEXT NOT NULL, imported_at TEXT NOT NULL,
        UNIQUE(normalized_name, source, fiscal_year)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_career_sources_due ON career_sources(enabled, next_sync_at)",
    "CREATE INDEX IF NOT EXISTS idx_sync_runs_started ON sync_runs(started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_h1b_employers_latest ON h1b_employers(fiscal_year DESC, lca_worker_positions DESC)",
    *coverage_schema.COVERAGE_DDL,
    *acquisition_schema.ACQUISITION_DDL,
)

_REQUIRED_COLUMNS = {
    "companies": {
        "id",
        "slug",
        "name",
        "normalized_name",
        "career_url",
        "ats_type",
        "collection_name",
        "collection_year",
        "collection_rank",
        "sec_cik",
        "ticker",
        "website_url",
        "is_synthetic",
        "created_at",
        "updated_at",
    },
    "career_sources": {
        "id",
        "company_id",
        "kind",
        "board_token",
        "base_url",
        "enabled",
        "sync_interval_minutes",
        "last_started_at",
        "last_success_at",
        "next_sync_at",
        "consecutive_failures",
        "consecutive_complete_empty_observations",
        "last_error",
        "terms_url",
        "policy_approved_at",
        "owner_contact",
        "created_at",
        "updated_at",
    },
    "jobs": {
        "id",
        "company_id",
        "source",
        "external_job_id",
        "canonical_url",
        "title",
        "location",
        "description",
        "posted_at",
        "source_updated_at",
        "first_seen_at",
        "last_seen_at",
        "closed_at",
        "status",
        "missed_complete_runs",
        "content_hash",
        "cluster_fingerprint",
        "sponsorship_tier",
        "sponsorship_evidence_score",
        "sponsorship_reasons",
        "sponsorship_excerpt",
        "sponsorship_rule_version",
        "metadata",
        "us_eligibility",
        "location_evidence",
        "location_rule_version",
    },
    "sponsorship_facts": {
        "id",
        "company_id",
        "source",
        "fiscal_year",
        "initial_approvals",
        "initial_denials",
        "lca_worker_positions",
        "entity_match_confidence",
        "source_url",
        "source_document",
        "source_checksum",
        "match_method",
        "imported_at",
    },
    "sync_runs": {
        "id",
        "source",
        "company_id",
        "status",
        "complete",
        "jobs_seen",
        "error_message",
        "started_at",
        "finished_at",
    },
    "job_versions": {"id", "job_id", "content_hash", "snapshot", "observed_at"},
    "h1b_employers": {
        "id",
        "normalized_name",
        "employer_name",
        "fiscal_year",
        "lca_worker_positions",
        "source",
        "source_url",
        "source_document",
        "source_checksum",
        "imported_at",
    },
    **coverage_schema.COVERAGE_REQUIRED_COLUMNS,
    **acquisition_schema.ACQUISITION_REQUIRED_COLUMNS,
}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def validate_schema(connection: sqlite3.Connection) -> list[str]:
    errors = []
    for table, required in _REQUIRED_COLUMNS.items():
        if not _table_exists(connection, table):
            errors.append(f"missing table: {table}")
            continue
        missing = sorted(required - _columns(connection, table))
        if missing:
            errors.append(f"{table} missing columns: {', '.join(missing)}")
    if _table_exists(connection, "schema_meta"):
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or int(row[0]) != SCHEMA_VERSION:
            errors.append("schema version mismatch")
    else:
        errors.append("missing schema version")
    return errors


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create a fresh schema or transactionally migrate earlier repository schemas."""

    if not _table_exists(connection, "companies"):
        connection.executescript(SCHEMA)
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in _AUXILIARY_DDL:
            connection.execute(statement)
        migrate_fingerprint_family_constraint(connection)
        for table, additions in _ADDED_COLUMNS.items():
            if not _table_exists(connection, table):
                continue
            existing = _columns(connection, table)
            for column, definition in additions:
                if column not in existing:
                    connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_us_eligibility_status ON jobs(us_eligibility, status, posted_at)"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_companies_sec_cik ON companies(sec_cik)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_companies_ticker ON companies(ticker)")
        connection.execute(
            "UPDATE companies SET is_synthetic = 1 WHERE collection_name = 'Synthetic demo'"
        )
        connection.execute(
            """UPDATE career_source_candidates AS candidate
            SET terms_url = COALESCE((
                    SELECT source.terms_url FROM career_sources AS source
                    WHERE source.company_id = candidate.company_id
                      AND source.base_url = candidate.candidate_url
                      AND source.enabled = 1
                      AND source.policy_approved_at IS NOT NULL
                ), candidate.terms_url),
                terms_status = 'permitted',
                terms_reviewed_at = COALESCE((
                    SELECT source.policy_approved_at FROM career_sources AS source
                    WHERE source.company_id = candidate.company_id
                      AND source.base_url = candidate.candidate_url
                      AND source.enabled = 1
                      AND source.policy_approved_at IS NOT NULL
                ), candidate.terms_reviewed_at)
            WHERE candidate.status = 'approved'
              AND candidate.terms_status IN ('unknown', 'review_required')
              AND EXISTS (
                SELECT 1 FROM career_sources AS source
                WHERE source.company_id = candidate.company_id
                  AND source.base_url = candidate.candidate_url
                  AND source.enabled = 1
                  AND source.policy_approved_at IS NOT NULL
              )"""
        )
        now = "1970-01-01T00:00:00+00:00"
        connection.execute(
            """INSERT OR IGNORE INTO company_coverage (
                company_id, disposition, reason, last_reviewed_at,
                reviewed_by, created_at, updated_at
            )
            SELECT c.id,
                CASE WHEN EXISTS (
                    SELECT 1 FROM career_sources s
                    WHERE s.company_id = c.id AND s.enabled = 1
                      AND s.policy_approved_at IS NOT NULL
                      AND s.last_success_at IS NOT NULL
                ) THEN 'supported' WHEN EXISTS (
                    SELECT 1 FROM career_sources s
                    WHERE s.company_id = c.id AND s.enabled = 1
                      AND s.policy_approved_at IS NOT NULL
                ) THEN 'approved' ELSE 'unreviewed' END,
                CASE WHEN EXISTS (
                    SELECT 1 FROM career_sources s
                    WHERE s.company_id = c.id AND s.enabled = 1
                      AND s.policy_approved_at IS NOT NULL
                      AND s.last_success_at IS NOT NULL
                ) THEN 'Migrated successfully synchronized career source'
                WHEN EXISTS (
                    SELECT 1 FROM career_sources s
                    WHERE s.company_id = c.id AND s.enabled = 1
                      AND s.policy_approved_at IS NOT NULL
                ) THEN 'Migrated approved career source' ELSE '' END,
                CASE WHEN EXISTS (
                    SELECT 1 FROM career_sources s
                    WHERE s.company_id = c.id AND s.enabled = 1
                      AND s.policy_approved_at IS NOT NULL
                ) THEN c.updated_at ELSE NULL END,
                CASE WHEN EXISTS (
                    SELECT 1 FROM career_sources s
                    WHERE s.company_id = c.id AND s.enabled = 1
                      AND s.policy_approved_at IS NOT NULL
                ) THEN 'schema-v5-migration' ELSE '' END,
                COALESCE(c.created_at, ?), COALESCE(c.updated_at, ?)
            FROM companies c""",
            (now, now),
        )
        if _table_exists(connection, "job_versions"):
            for row in connection.execute(
                "SELECT id, content_hash, snapshot FROM job_versions"
            ).fetchall():
                try:
                    snapshot = json.loads(row[2])
                except (TypeError, ValueError):
                    snapshot = {}
                if "description" in snapshot:
                    sanitized = {
                        "title": snapshot.get("title", ""),
                        "location": snapshot.get("location", ""),
                        "content_hash": row[1],
                        "description_redacted": True,
                    }
                    connection.execute(
                        "UPDATE job_versions SET snapshot = ? WHERE id = ?",
                        (json.dumps(sanitized, sort_keys=True), row[0]),
                    )
        backfill_job_geography(connection)
        connection.execute(
            """INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (str(SCHEMA_VERSION),),
        )
        errors = validate_schema(connection)
        if errors:
            raise RuntimeError("; ".join(errors))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
