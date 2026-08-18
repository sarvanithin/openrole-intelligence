#!/usr/bin/env python3
"""Bounded, read-only overnight audit of observed career portals.

This script deliberately never imports, approves, registers, or reschedules a
source.  Its purpose is to make an overnight run safe while the lock-protected
acquisition and job-sync services continue their normal work: it verifies that
the observed URLs can be reached and emits a durable queue of exact results for
the next connector/recovery pass.
"""

from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import socket
import sqlite3
import threading
import time
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_DURATION_SECONDS = 36_000
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_WORKERS = 6
DEFAULT_HOST_INTERVAL_SECONDS = 1.0
SUPPORTED_FAMILIES = {
    "ashby",
    "greenhouse",
    "lever",
    "oracle_recruiting",
    "smartrecruiters",
    "workday",
}


def _utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class AuditTarget:
    company_id: int
    company_name: str
    url: str
    family: str
    target_type: str

    @property
    def key(self) -> str:
        return f"{self.target_type}:{self.company_id}:{self.url}"


class NoRedirect(HTTPRedirectHandler):
    """Expose redirects in the report instead of following unknown hosts."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class HostPacer:
    """Bound request starts for each hostname, even when worker threads run."""

    def __init__(self, interval_seconds: float) -> None:
        self._interval = interval_seconds
        self._lock = threading.Lock()
        self._next_allowed: dict[str, float] = {}

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            next_allowed = self._next_allowed.get(host, now)
            delay = max(0.0, next_allowed - now)
            self._next_allowed[host] = max(now, next_allowed) + self._interval
        if delay:
            time.sleep(delay)


@contextmanager
def audit_lock(run_directory: Path) -> Iterable[None]:
    lock_path = run_directory / ".overnight-verified-audit.lock"
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"another audit already owns {lock_path}") from error
        yield


def readonly_connection(database: Path) -> sqlite3.Connection:
    uri = f"file:{database.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def load_targets(database: Path) -> list[AuditTarget]:
    """Read exact observed registry URLs and active-source base URLs only."""

    with readonly_connection(database) as connection:
        source_rows = connection.execute(
            """SELECT c.id AS company_id, c.name AS company_name, s.base_url AS url,
                      s.kind AS family
               FROM career_sources s JOIN companies c ON c.id = s.company_id
               WHERE s.enabled = 1 AND s.base_url IS NOT NULL AND s.base_url != ''
               ORDER BY c.id, s.id"""
        ).fetchall()
        registry_rows = connection.execute(
            """SELECT c.id AS company_id, c.name AS company_name, f.observed_url AS url,
                      f.family AS family
               FROM career_source_fingerprints f JOIN companies c ON c.id = f.company_id
               WHERE json_extract(f.evidence_json, '$.review_method')
                         = 'user_supplied_career_url_registry'
                 AND f.observed_url IS NOT NULL AND f.observed_url != ''
               ORDER BY c.id, f.observed_url"""
        ).fetchall()

    targets: dict[str, AuditTarget] = {}
    for row in source_rows:
        target = AuditTarget(
            company_id=int(row["company_id"]),
            company_name=str(row["company_name"]),
            url=str(row["url"]),
            family=str(row["family"]),
            target_type="enabled_source",
        )
        targets[target.key] = target
    for row in registry_rows:
        family = str(row["family"])
        target = AuditTarget(
            company_id=int(row["company_id"]),
            company_name=str(row["company_name"]),
            url=str(row["url"]),
            family=family,
            target_type="registry_supported_ats" if family in SUPPORTED_FAMILIES else "registry_portal",
        )
        targets[target.key] = target
    return list(targets.values())


def load_completed(results_path: Path) -> set[str]:
    if not results_path.exists():
        return set()
    completed: set[str] = set()
    with results_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
                completed.add(str(value["key"]))
            except (KeyError, json.JSONDecodeError):
                continue
    return completed


def public_url(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "invalid_public_http_url"
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False, "dns_failure"
    for address in addresses:
        candidate = ipaddress.ip_address(address[4][0])
        if not candidate.is_global:
            return False, "non_public_target"
    return True, "ok"


def probe(target: AuditTarget, *, pacer: HostPacer, timeout_seconds: int) -> dict[str, object]:
    started_at = _utcnow()
    permitted, reason = public_url(target.url)
    if not permitted:
        return {**asdict(target), "key": target.key, "started_at": started_at, "outcome": reason}
    host = urlparse(target.url).hostname or ""
    pacer.wait(host)
    request = Request(
        target.url,
        headers={
            "User-Agent": "OpenRoleIntelligence/1.0 (overnight verification)",
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity",
            "Range": "bytes=0-4095",
        },
    )
    try:
        with build_opener(NoRedirect).open(request, timeout=timeout_seconds) as response:
            return {
                **asdict(target),
                "key": target.key,
                "started_at": started_at,
                "completed_at": _utcnow(),
                "outcome": "reachable",
                "http_status": int(response.status),
                "content_type": response.headers.get("Content-Type", ""),
            }
    except HTTPError as error:
        return {
            **asdict(target),
            "key": target.key,
            "started_at": started_at,
            "completed_at": _utcnow(),
            "outcome": "redirect" if 300 <= error.code < 400 else "http_error",
            "http_status": error.code,
            "location": error.headers.get("Location", ""),
        }
    except (TimeoutError, URLError, OSError, UnicodeError) as error:
        return {
            **asdict(target),
            "key": target.key,
            "started_at": started_at,
            "completed_at": _utcnow(),
            "outcome": "network_error",
            "error": str(error)[:500],
        }


def write_health_snapshot(database: Path, destination: Path) -> dict[str, object]:
    """Capture source state for recovery, without touching a source row."""

    with readonly_connection(database) as connection:
        rows = connection.execute(
            """SELECT id, company_id, kind, base_url, enabled, last_started_at,
                      last_success_at, consecutive_failures, last_error, next_sync_at
               FROM career_sources WHERE enabled = 1 ORDER BY id"""
        ).fetchall()
    payload = {"generated_at": _utcnow(), "sources": [dict(row) for row in rows]}
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def write_summary(results_path: Path, destination: Path, *, deadline_reached: bool) -> dict[str, object]:
    totals: dict[str, int] = {}
    by_target_type: dict[str, int] = {}
    with results_path.open(encoding="utf-8") as handle:
        for line in handle:
            value = json.loads(line)
            outcome = str(value.get("outcome", "unknown"))
            target_type = str(value.get("target_type", "unknown"))
            totals[outcome] = totals.get(outcome, 0) + 1
            by_target_type[target_type] = by_target_type.get(target_type, 0) + 1
    report = {
        "generated_at": _utcnow(),
        "deadline_reached": deadline_reached,
        "result_counts": totals,
        "target_counts": by_target_type,
        "results_file": str(results_path),
        "safety": "read-only database audit; no registry, candidate, or source status was changed",
    }
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def run(arguments: argparse.Namespace) -> dict[str, object]:
    database = Path(arguments.database).expanduser().resolve(strict=True)
    run_directory = Path(arguments.run_directory).expanduser().resolve()
    run_directory.mkdir(parents=True, exist_ok=True)
    results_path = run_directory / "portal-results.jsonl"
    health_path = run_directory / "source-health-before.json"
    summary_path = run_directory / "summary.json"
    started = time.monotonic()
    deadline = started + arguments.max_duration_seconds

    with audit_lock(run_directory):
        targets = load_targets(database)
        completed = load_completed(results_path)
        pending = [target for target in targets if target.key not in completed]
        write_health_snapshot(database, health_path)
        pacer = HostPacer(arguments.host_interval_seconds)
        with results_path.open("a", encoding="utf-8") as output, ThreadPoolExecutor(
            max_workers=arguments.workers
        ) as executor:
            futures: dict[Future[dict[str, object]], AuditTarget] = {}
            iterator = iter(pending)
            while len(futures) < arguments.workers:
                try:
                    target = next(iterator)
                except StopIteration:
                    break
                futures[executor.submit(probe, target, pacer=pacer, timeout_seconds=arguments.timeout_seconds)] = target
            while futures and time.monotonic() < deadline:
                future = next(as_completed(futures))
                futures.pop(future)
                result = future.result()
                output.write(json.dumps(result, sort_keys=True) + "\n")
                output.flush()
                try:
                    target = next(iterator)
                except StopIteration:
                    continue
                futures[executor.submit(probe, target, pacer=pacer, timeout_seconds=arguments.timeout_seconds)] = target
        deadline_reached = bool(futures) or time.monotonic() >= deadline
        write_health_snapshot(database, run_directory / "source-health-after.json")
        summary = write_summary(results_path, summary_path, deadline_reached=deadline_reached)
    return {"targets": len(targets), "already_completed": len(completed), "pending_at_start": len(pending), **summary}


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--database", required=True, help="live SQLite database to open read-only")
    command.add_argument("--run-directory", required=True, help="durable output directory")
    command.add_argument("--max-duration-seconds", type=int, default=MAX_DURATION_SECONDS)
    command.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    command.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    command.add_argument("--host-interval-seconds", type=float, default=DEFAULT_HOST_INTERVAL_SECONDS)
    return command


def main() -> None:
    arguments = parser().parse_args()
    if not 60 <= arguments.max_duration_seconds <= MAX_DURATION_SECONDS:
        raise SystemExit(f"--max-duration-seconds must be between 60 and {MAX_DURATION_SECONDS}")
    if not 1 <= arguments.workers <= 12:
        raise SystemExit("--workers must be between 1 and 12")
    if not 1 <= arguments.timeout_seconds <= 60:
        raise SystemExit("--timeout-seconds must be between 1 and 60")
    if not 0.25 <= arguments.host_interval_seconds <= 60:
        raise SystemExit("--host-interval-seconds must be between 0.25 and 60")
    print(json.dumps(run(arguments), indent=2))


if __name__ == "__main__":
    main()
