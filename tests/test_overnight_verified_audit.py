from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "overnight_verified_audit.py"
SPEC = importlib.util.spec_from_file_location("overnight_verified_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE career_sources (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                base_url TEXT,
                enabled INTEGER NOT NULL,
                last_started_at TEXT,
                last_success_at TEXT,
                consecutive_failures INTEGER NOT NULL,
                last_error TEXT,
                next_sync_at TEXT
            );
            CREATE TABLE career_source_fingerprints (
                company_id INTEGER NOT NULL,
                observed_url TEXT,
                family TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            );
            INSERT INTO companies VALUES (1, 'Example');
            INSERT INTO career_sources VALUES
                (1, 1, 'greenhouse', 'https://boards.greenhouse.io/example', 1,
                 NULL, NULL, 0, NULL, NULL);
            INSERT INTO career_source_fingerprints VALUES
                (1, 'https://example.com/careers', 'unknown_external',
                 '{"review_method":"user_supplied_career_url_registry"}');
            """
        )


def test_load_targets_reads_registry_and_sources_without_writing(tmp_path: Path):
    database = tmp_path / "audit.db"
    _database(database)

    targets = audit.load_targets(database)

    assert {(target.target_type, target.url) for target in targets} == {
        ("enabled_source", "https://boards.greenhouse.io/example"),
        ("registry_portal", "https://example.com/careers"),
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM career_sources").fetchone()[0] == 1


def test_public_url_rejects_local_network_targets(monkeypatch):
    monkeypatch.setattr(
        audit.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 0))],
    )

    assert audit.public_url("https://internal.example/careers") == (False, "non_public_target")
