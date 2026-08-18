"""Schema fragments for auditable company career-source discovery."""

from __future__ import annotations

COVERAGE_DISPOSITIONS = (
    "unreviewed",
    "candidate",
    "approved",
    "supported",
    "unsupported",
    "blocked",
    "no_source",
    "stale",
)

CANDIDATE_STATUSES = ("discovered", "reviewed", "approved", "rejected", "superseded")
ROBOTS_STATUSES = ("unknown", "allowed", "disallowed", "unavailable", "error")
TERMS_STATUSES = ("unknown", "permitted", "restricted", "prohibited", "review_required")
FINGERPRINT_FAMILIES = (
    "adp",
    "avature",
    "dayforce",
    "directemployers",
    "eightfold",
    "gr8_people",
    "icims",
    "jobvite",
    "paycom",
    "paycor",
    "paylocity",
    "phenom",
    "rippling",
    "successfactors",
    "taleo",
    "ukg",
    "unknown_external",
)

_DISPOSITIONS_SQL = ", ".join(f"'{value}'" for value in COVERAGE_DISPOSITIONS)
_CANDIDATES_SQL = ", ".join(f"'{value}'" for value in CANDIDATE_STATUSES)
_ROBOTS_SQL = ", ".join(f"'{value}'" for value in ROBOTS_STATUSES)
_TERMS_SQL = ", ".join(f"'{value}'" for value in TERMS_STATUSES)
_FINGERPRINTS_SQL = ", ".join(f"'{value}'" for value in FINGERPRINT_FAMILIES)

COVERAGE_FINGERPRINT_DDL = f"""CREATE TABLE IF NOT EXISTS career_source_fingerprints (
        id INTEGER PRIMARY KEY,
        company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        observed_url TEXT NOT NULL,
        family TEXT NOT NULL CHECK(family IN ({_FINGERPRINTS_SQL})),
        host TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '{{}}',
        observation_count INTEGER NOT NULL DEFAULT 1 CHECK(observation_count > 0),
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        last_observed_by TEXT NOT NULL,
        UNIQUE(company_id, family, observed_url)
    )"""

COVERAGE_DDL = (
    f"""CREATE TABLE IF NOT EXISTS company_coverage (
        company_id INTEGER PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
        disposition TEXT NOT NULL DEFAULT 'unreviewed'
            CHECK(disposition IN ({_DISPOSITIONS_SQL})),
        reason TEXT NOT NULL DEFAULT '',
        last_discovered_at TEXT,
        last_reviewed_at TEXT,
        stale_after TEXT,
        reviewed_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    f"""CREATE TABLE IF NOT EXISTS career_source_candidates (
        id INTEGER PRIMARY KEY,
        company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        candidate_url TEXT NOT NULL,
        kind TEXT NOT NULL,
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        evidence_json TEXT NOT NULL DEFAULT '{{}}',
        status TEXT NOT NULL DEFAULT 'discovered' CHECK(status IN ({_CANDIDATES_SQL})),
        robots_status TEXT NOT NULL DEFAULT 'unknown' CHECK(robots_status IN ({_ROBOTS_SQL})),
        robots_checked_at TEXT,
        terms_url TEXT NOT NULL DEFAULT '',
        terms_status TEXT NOT NULL DEFAULT 'unknown' CHECK(terms_status IN ({_TERMS_SQL})),
        terms_reviewed_at TEXT,
        discovered_at TEXT NOT NULL,
        reviewed_at TEXT,
        reviewed_by TEXT NOT NULL DEFAULT '',
        review_notes TEXT NOT NULL DEFAULT '',
        consecutive_complete_empty_observations INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(company_id, candidate_url)
    )""",
    COVERAGE_FINGERPRINT_DDL,
    f"""CREATE TABLE IF NOT EXISTS company_coverage_events (
        id INTEGER PRIMARY KEY,
        company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
        candidate_id INTEGER REFERENCES career_source_candidates(id) ON DELETE SET NULL,
        from_disposition TEXT CHECK(
            from_disposition IS NULL OR from_disposition IN ({_DISPOSITIONS_SQL})
        ),
        to_disposition TEXT NOT NULL CHECK(to_disposition IN ({_DISPOSITIONS_SQL})),
        reason TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL,
        occurred_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_company_coverage_disposition ON company_coverage(disposition, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_company_status ON career_source_candidates(company_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_fingerprints_family_company ON career_source_fingerprints(family, company_id)",
    "CREATE INDEX IF NOT EXISTS idx_coverage_events_company ON company_coverage_events(company_id, occurred_at DESC)",
    """CREATE TRIGGER IF NOT EXISTS insert_default_company_coverage
    AFTER INSERT ON companies
    BEGIN
        INSERT OR IGNORE INTO company_coverage (
            company_id, disposition, created_at, updated_at
        ) VALUES (NEW.id, 'unreviewed', NEW.created_at, NEW.created_at);
    END""",
)

COVERAGE_SCHEMA = ";\n\n".join(COVERAGE_DDL) + ";"

COVERAGE_REQUIRED_COLUMNS = {
    "company_coverage": {
        "company_id",
        "disposition",
        "reason",
        "last_discovered_at",
        "last_reviewed_at",
        "stale_after",
        "reviewed_by",
        "created_at",
        "updated_at",
    },
    "career_source_candidates": {
        "id",
        "company_id",
        "candidate_url",
        "kind",
        "confidence",
        "evidence_json",
        "status",
        "robots_status",
        "robots_checked_at",
        "terms_url",
        "terms_status",
        "terms_reviewed_at",
        "discovered_at",
        "reviewed_at",
        "reviewed_by",
        "review_notes",
        "consecutive_complete_empty_observations",
        "created_at",
        "updated_at",
    },
    "career_source_fingerprints": {
        "id",
        "company_id",
        "observed_url",
        "family",
        "host",
        "evidence_json",
        "observation_count",
        "first_seen_at",
        "last_seen_at",
        "last_observed_by",
    },
    "company_coverage_events": {
        "id",
        "company_id",
        "candidate_id",
        "from_disposition",
        "to_disposition",
        "reason",
        "actor",
        "occurred_at",
    },
}
