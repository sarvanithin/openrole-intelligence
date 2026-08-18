"""Schema for durable, lease-based company acquisition work."""

from __future__ import annotations

PLAN_STATUSES = ("active", "completed", "failed")
TASK_STATUSES = ("pending", "leased", "completed", "failed")

_PLAN_STATUSES_SQL = ", ".join(f"'{value}'" for value in PLAN_STATUSES)
_TASK_STATUSES_SQL = ", ".join(f"'{value}'" for value in TASK_STATUSES)

ACQUISITION_DDL = (
    f"""CREATE TABLE IF NOT EXISTS acquisition_plans (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        snapshot_checksum TEXT NOT NULL,
        total_tasks INTEGER NOT NULL CHECK(total_tasks > 0),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ({_PLAN_STATUSES_SQL})),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    f"""CREATE TABLE IF NOT EXISTS acquisition_tasks (
        id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL REFERENCES acquisition_plans(id) ON DELETE CASCADE,
        company_id INTEGER NOT NULL,
        company_name TEXT NOT NULL,
        company_snapshot_json TEXT NOT NULL,
        stage TEXT NOT NULL,
        stage_snapshot_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ({_TASK_STATUSES_SQL})),
        lease_owner TEXT NOT NULL DEFAULT '',
        leased_until TEXT,
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
        max_attempts INTEGER NOT NULL CHECK(max_attempts BETWEEN 1 AND 20),
        outcome_code TEXT NOT NULL DEFAULT '',
        outcome_json TEXT NOT NULL DEFAULT '{{}}',
        error_summary TEXT NOT NULL DEFAULT '',
        retryable INTEGER NOT NULL DEFAULT 1 CHECK(retryable IN (0, 1)),
        last_attempt_at TEXT,
        next_attempt_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(plan_id, company_id, stage),
        CHECK(
            (status = 'leased' AND lease_owner != '' AND leased_until IS NOT NULL)
            OR (status != 'leased' AND lease_owner = '' AND leased_until IS NULL)
        )
    )""",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_tasks_claim ON acquisition_tasks(plan_id, status, next_attempt_at, leased_until, id)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_tasks_company ON acquisition_tasks(company_id, stage, status)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_plans_status ON acquisition_plans(status, updated_at)",
)

ACQUISITION_SCHEMA = ";\n\n".join(ACQUISITION_DDL) + ";"

ACQUISITION_REQUIRED_COLUMNS = {
    "acquisition_plans": {
        "id",
        "name",
        "snapshot_checksum",
        "total_tasks",
        "status",
        "created_by",
        "created_at",
        "updated_at",
    },
    "acquisition_tasks": {
        "id",
        "plan_id",
        "company_id",
        "company_name",
        "company_snapshot_json",
        "stage",
        "stage_snapshot_json",
        "status",
        "lease_owner",
        "leased_until",
        "attempts",
        "max_attempts",
        "outcome_code",
        "outcome_json",
        "error_summary",
        "retryable",
        "last_attempt_at",
        "next_attempt_at",
        "created_at",
        "updated_at",
    },
}
