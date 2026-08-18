import sqlite3

from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_schema import COVERAGE_SCHEMA
from fortune_intel.storage.schema import SCHEMA, SCHEMA_VERSION


def _without_v5(schema: str, *, version: int) -> str:
    result = schema.replace(f"\n{COVERAGE_SCHEMA}\n", "\n")
    for line in (
        "    sec_cik TEXT,\n",
        "    ticker TEXT,\n",
        "    website_url TEXT,\n",
        "CREATE INDEX IF NOT EXISTS idx_companies_sec_cik ON companies(sec_cik);\n",
        "CREATE INDEX IF NOT EXISTS idx_companies_ticker ON companies(ticker);\n",
        "    us_eligibility TEXT NOT NULL DEFAULT 'unknown'\n",
        "        CHECK(us_eligibility IN ('eligible', 'ineligible', 'unknown')),\n",
        "    location_evidence TEXT NOT NULL DEFAULT '{{}}',\n",
        "    location_rule_version TEXT NOT NULL DEFAULT '',\n",
        "CREATE INDEX IF NOT EXISTS idx_jobs_us_eligibility_status\n",
        "ON jobs(us_eligibility, status, posted_at);\n",
    ):
        result = result.replace(line, "")
    return result.replace(
        "VALUES ('schema_version', '10')", f"VALUES ('schema_version', '{version}')"
    )


def _without_v6(schema: str) -> str:
    result = schema
    for line in (
        "    us_eligibility TEXT NOT NULL DEFAULT 'unknown'\n",
        "        CHECK(us_eligibility IN ('eligible', 'ineligible', 'unknown')),\n",
        "    location_evidence TEXT NOT NULL DEFAULT '{{}}',\n",
        "    location_rule_version TEXT NOT NULL DEFAULT '',\n",
        "CREATE INDEX IF NOT EXISTS idx_jobs_us_eligibility_status\n",
        "ON jobs(us_eligibility, status, posted_at);\n",
    ):
        result = result.replace(line, "")
    return result.replace("VALUES ('schema_version', '10')", "VALUES ('schema_version', '5')")


def _without_v9(schema: str) -> str:
    result = schema.replace(
        "    consecutive_complete_empty_observations INTEGER NOT NULL DEFAULT 0,\n",
        "",
        1,
    )
    result = result.replace(
        "        consecutive_complete_empty_observations INTEGER NOT NULL DEFAULT 0,\n",
        "",
        1,
    )
    return result.replace("VALUES ('schema_version', '10')", "VALUES ('schema_version', '8')")


def _without_v10(schema: str) -> str:
    result = schema
    for family in ("paycom", "paycor", "paylocity", "rippling"):
        result = result.replace(f", '{family}'", "")
    return result.replace("VALUES ('schema_version', '10')", "VALUES ('schema_version', '9')")


def test_version_one_schema_migrates_and_marks_demo_data(tmp_path):
    database = tmp_path / "legacy.db"
    legacy_schema = _without_v5(SCHEMA, version=1)
    for line in (
        "    is_synthetic INTEGER NOT NULL DEFAULT 0,\n",
        "    terms_url TEXT,\n",
        "    policy_approved_at TEXT,\n",
        "    owner_contact TEXT,\n",
        "    source_document TEXT,\n",
        "    source_checksum TEXT,\n",
        "    match_method TEXT NOT NULL DEFAULT 'reviewed',\n",
    ):
        legacy_schema = legacy_schema.replace(line, "")
    with sqlite3.connect(database) as connection:
        connection.executescript(legacy_schema)
        connection.execute(
            """INSERT INTO companies (
                slug, name, normalized_name, career_url, ats_type, collection_name,
                collection_year, collection_rank, created_at, updated_at
            ) VALUES ('demo', 'Demo', 'demo', '', '', 'Synthetic demo', 2026, 1, 'now', 'now')"""
        )

    repository = JobRepository(database)
    repository.initialize()

    with repository.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        company = connection.execute(
            "SELECT is_synthetic FROM companies WHERE slug = 'demo'"
        ).fetchone()[0]
    assert int(version) == SCHEMA_VERSION
    assert company == 1
    assert repository.readiness()["ready"] is True


def test_version_four_schema_migrates_identity_and_coverage_without_data_loss(tmp_path):
    database = tmp_path / "v4.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(_without_v5(SCHEMA, version=4))
        connection.execute(
            """INSERT INTO companies (
                slug, name, normalized_name, career_url, ats_type,
                collection_name, collection_year, collection_rank,
                is_synthetic, created_at, updated_at
            ) VALUES ('example', 'Example', 'example', '', '', 'SEC', 2026, NULL, 0,
                '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00')"""
        )
        connection.execute(
            """INSERT INTO career_sources (
                company_id, kind, board_token, base_url, enabled,
                policy_approved_at, created_at, updated_at
            ) VALUES (1, 'greenhouse', 'example', 'https://boards.greenhouse.io/example',
                1, '2026-01-02T00:00:00+00:00',
                '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00')"""
        )

    repository = JobRepository(database)
    repository.initialize()

    with repository.connect() as connection:
        company = connection.execute(
            "SELECT name, sec_cik, ticker, website_url FROM companies WHERE id = 1"
        ).fetchone()
        source_count = connection.execute("SELECT COUNT(*) FROM career_sources").fetchone()[0]
    assert dict(company) == {
        "name": "Example",
        "sec_cik": None,
        "ticker": None,
        "website_url": None,
    }
    assert source_count == 1
    assert repository.get_company_coverage(1)["disposition"] == "approved"
    assert repository.readiness()["schema_version"] == SCHEMA_VERSION


def test_version_five_jobs_are_backfilled_with_us_location_evidence(tmp_path):
    database = tmp_path / "v5.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(_without_v6(SCHEMA))
        connection.execute(
            """INSERT INTO companies (
                slug, name, normalized_name, career_url, ats_type,
                is_synthetic, created_at, updated_at
            ) VALUES ('example', 'Example', 'example', '', '', 0, 'now', 'now')"""
        )
        connection.execute(
            """INSERT INTO jobs (
                id, company_id, source, external_job_id, canonical_url, title,
                location, first_seen_at, last_seen_at, content_hash,
                cluster_fingerprint, sponsorship_tier, sponsorship_evidence_score,
                sponsorship_reasons, sponsorship_rule_version, metadata
            ) VALUES ('job-us', 1, 'test', '1', 'https://example.test/jobs/1',
                'Engineer', 'Austin, TX', 'first', 'last', 'hash', 'cluster',
                'D', 0, '[]', 'rules-1.0', '{}')"""
        )
        connection.execute(
            """INSERT INTO jobs (
                id, company_id, source, external_job_id, canonical_url, title,
                location, first_seen_at, last_seen_at, content_hash,
                cluster_fingerprint, sponsorship_tier, sponsorship_evidence_score,
                sponsorship_reasons, sponsorship_rule_version, metadata
            ) VALUES ('job-ca', 1, 'test', '2', 'https://example.test/jobs/2',
                'Engineer', 'Toronto, Ontario, Canada', 'first', 'last', 'hash2',
                'cluster2', 'D', 0, '[]', 'rules-1.0', '{}')"""
        )

    repository = JobRepository(database)
    repository.initialize()

    with repository.connect() as connection:
        rows = connection.execute(
            """SELECT id, us_eligibility, location_rule_version, location_evidence
            FROM jobs ORDER BY id"""
        ).fetchall()
    assert [(row["id"], row["us_eligibility"]) for row in rows] == [
        ("job-ca", "ineligible"),
        ("job-us", "eligible"),
    ]
    assert all(row["location_rule_version"] == "us-location-v4" for row in rows)
    assert all('"classification"' in row["location_evidence"] for row in rows)

    with repository.connect() as connection:
        connection.execute("UPDATE jobs SET us_eligibility = 'invalid' WHERE id = 'job-us'")
    readiness = repository.readiness()
    assert readiness["ready"] is False
    assert "jobs contain invalid us_eligibility values" in readiness["errors"]


def test_version_six_migrates_passive_fingerprint_inventory(tmp_path):
    database = tmp_path / "v6.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA)
        connection.execute("DROP TABLE career_source_fingerprints")
        connection.execute("UPDATE schema_meta SET value = '6' WHERE key = 'schema_version'")

    repository = JobRepository(database)
    repository.initialize()

    with repository.connect() as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(career_source_fingerprints)")
        }
    assert {"observed_url", "family", "observation_count", "last_observed_by"} <= columns
    assert repository.readiness()["schema_version"] == SCHEMA_VERSION


def test_version_seven_migrates_durable_acquisition_queue(tmp_path):
    database = tmp_path / "v7.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA)
        connection.execute("DROP TABLE acquisition_tasks")
        connection.execute("DROP TABLE acquisition_plans")
        connection.execute("UPDATE schema_meta SET value = '7' WHERE key = 'schema_version'")

    repository = JobRepository(database)
    repository.initialize()

    with repository.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master WHERE type = 'table'
                AND name LIKE 'acquisition_%'"""
            )
        }
    assert tables == {"acquisition_plans", "acquisition_tasks"}
    assert repository.readiness()["schema_version"] == SCHEMA_VERSION


def test_version_eight_adds_complete_empty_observation_state(tmp_path):
    database = tmp_path / "v8.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(_without_v9(SCHEMA))
        connection.execute(
            """INSERT INTO companies (
                slug, name, normalized_name, is_synthetic, created_at, updated_at
            ) VALUES ('example', 'Example', 'example', 0, 'now', 'now')"""
        )
        connection.execute(
            """INSERT INTO career_sources (
                company_id, kind, board_token, base_url, created_at, updated_at
            ) VALUES (1, 'greenhouse', 'example',
                'https://boards.greenhouse.io/example', 'now', 'now')"""
        )
        connection.execute(
            """INSERT INTO career_source_candidates (
                company_id, candidate_url, kind, confidence, discovered_at,
                created_at, updated_at
            ) VALUES (1, 'https://boards.greenhouse.io/example', 'greenhouse', 1,
                'now', 'now', 'now')"""
        )

    repository = JobRepository(database)
    repository.initialize()

    with repository.connect() as connection:
        source = connection.execute(
            "SELECT consecutive_complete_empty_observations FROM career_sources"
        ).fetchone()[0]
        candidate = connection.execute(
            """SELECT consecutive_complete_empty_observations
            FROM career_source_candidates"""
        ).fetchone()[0]
    assert source == candidate == 0
    assert repository.readiness()["schema_version"] == SCHEMA_VERSION


def test_version_nine_expands_passive_fingerprint_family_constraint(tmp_path):
    database = tmp_path / "v9.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(_without_v10(SCHEMA))
        connection.execute(
            """INSERT INTO companies (
                slug, name, normalized_name, is_synthetic, created_at, updated_at
            ) VALUES ('example', 'Example', 'example', 0, 'now', 'now')"""
        )
        connection.execute(
            """INSERT INTO career_source_fingerprints (
                company_id, observed_url, family, host, evidence_json,
                observation_count, first_seen_at, last_seen_at, last_observed_by
            ) VALUES (1, 'https://ats.rippling.com/example/jobs', 'unknown_external',
                'ats.rippling.com', '{"legacy":true}', 7,
                '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00', 'v9')"""
        )

    repository = JobRepository(database)
    repository.initialize()
    repository.upsert_source_fingerprint(
        1,
        observed_url="https://ats.rippling.com/example/jobs",
        family="rippling",
        evidence={"v10": True},
        actor="migration-test",
        observed_at="2026-01-03T00:00:00+00:00",
    )

    rows = repository.list_source_fingerprints(1)
    legacy = next(row for row in rows if row["family"] == "unknown_external")
    assert legacy["observation_count"] == 7
    assert legacy["evidence"] == {"legacy": True}
    assert {row["family"] for row in rows} == {"unknown_external", "rippling"}
    assert repository.readiness()["schema_version"] == SCHEMA_VERSION
