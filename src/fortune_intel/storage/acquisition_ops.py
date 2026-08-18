"""Durable acquisition-plan queue with transactional SQLite leases."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


_CODE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_MIN_LEASE_SECONDS = 30
_MAX_LEASE_SECONDS = 3_600
_BASE_BACKOFF_SECONDS = 60
_MAX_BACKOFF_SECONDS = 21_600


@dataclass(frozen=True, slots=True)
class AcquisitionTaskSeed:
    """Immutable input used to freeze a company and acquisition-stage snapshot."""

    company_id: int
    company_name: str
    stage: str
    company_snapshot: Mapping[str, object] = field(default_factory=dict)
    stage_snapshot: Mapping[str, object] = field(default_factory=dict)
    max_attempts: int = 5


def _moment(value: str | datetime | None = None) -> datetime:
    if value is None:
        parsed = datetime.now(UTC)
    elif isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC).replace(microsecond=0)


def _json_object(value: Mapping[str, object], *, field_name: str) -> str:
    try:
        return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a JSON-serializable object") from error


def _code(value: str, *, field_name: str) -> str:
    normalized = value.strip().casefold()
    if _CODE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a stable lowercase code")
    return normalized


def _decode_task(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["company_snapshot"] = json.loads(result.pop("company_snapshot_json"))
    result["stage_snapshot"] = json.loads(result.pop("stage_snapshot_json"))
    result["outcome"] = json.loads(result.pop("outcome_json"))
    result["retryable"] = bool(result["retryable"])
    return result


def _task_spec(seed: AcquisitionTaskSeed) -> dict[str, object]:
    if seed.company_id <= 0 or not seed.company_name.strip():
        raise ValueError("company_id and company_name are required")
    if not 1 <= seed.max_attempts <= 20:
        raise ValueError("max_attempts must be between 1 and 20")
    stage = _code(seed.stage, field_name="stage")
    company_snapshot = dict(seed.company_snapshot)
    existing_id = company_snapshot.setdefault("id", seed.company_id)
    existing_name = company_snapshot.setdefault("name", seed.company_name.strip())
    if existing_id != seed.company_id or existing_name != seed.company_name.strip():
        raise ValueError("company snapshot id and name must match the frozen task identity")
    return {
        "company_id": seed.company_id,
        "company_name": seed.company_name.strip(),
        "company_snapshot_json": _json_object(company_snapshot, field_name="company_snapshot"),
        "stage": stage,
        "stage_snapshot_json": _json_object(seed.stage_snapshot, field_name="stage_snapshot"),
        "max_attempts": seed.max_attempts,
    }


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:24]}"


class AcquisitionOperationsMixin:
    def create_acquisition_plan(
        self,
        name: str,
        tasks: Iterable[AcquisitionTaskSeed],
        *,
        actor: str,
        created_at: str | datetime | None = None,
    ) -> str:
        """Create an idempotent plan whose task snapshots can never be rewritten."""

        plan_name = name.strip()
        if not plan_name or not actor.strip():
            raise ValueError("name and actor are required")
        specs: dict[tuple[int, str], dict[str, object]] = {}
        for seed in tasks:
            spec = _task_spec(seed)
            key = (int(spec["company_id"]), str(spec["stage"]))
            existing = specs.get(key)
            if existing is not None and existing != spec:
                raise ValueError("duplicate company and stage have conflicting snapshots")
            specs[key] = spec
        if not specs:
            raise ValueError("at least one acquisition task is required")
        ordered = [specs[key] for key in sorted(specs)]
        frozen = json.dumps(
            {"name": plan_name, "tasks": ordered},
            sort_keys=True,
            separators=(",", ":"),
        )
        checksum = hashlib.sha256(frozen.encode()).hexdigest()
        plan_id = _stable_id("ap", checksum)
        timestamp = _moment(created_at).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT snapshot_checksum FROM acquisition_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            if existing is not None:
                if str(existing["snapshot_checksum"]) != checksum:
                    raise RuntimeError("stable acquisition plan ID collision")
                return plan_id
            connection.execute(
                """INSERT INTO acquisition_plans (
                    id, name, snapshot_checksum, total_tasks, status,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
                (plan_id, plan_name, checksum, len(ordered), actor.strip(), timestamp, timestamp),
            )
            for spec in ordered:
                identity = f"{plan_id}:{spec['company_id']}:{spec['stage']}"
                connection.execute(
                    """INSERT INTO acquisition_tasks (
                        id, plan_id, company_id, company_name, company_snapshot_json,
                        stage, stage_snapshot_json, max_attempts, next_attempt_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _stable_id("at", identity),
                        plan_id,
                        spec["company_id"],
                        spec["company_name"],
                        spec["company_snapshot_json"],
                        spec["stage"],
                        spec["stage_snapshot_json"],
                        spec["max_attempts"],
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
        return plan_id

    @staticmethod
    def _reconcile_acquisition_plan(connection: Any, plan_id: str, timestamp: str) -> None:
        counts = {
            str(row["status"]): int(row["count"])
            for row in connection.execute(
                """SELECT status, COUNT(*) AS count FROM acquisition_tasks
                WHERE plan_id = ? GROUP BY status""",
                (plan_id,),
            )
        }
        if counts.get("pending", 0) or counts.get("leased", 0):
            status = "active"
        elif counts.get("failed", 0):
            status = "failed"
        else:
            status = "completed"
        connection.execute(
            "UPDATE acquisition_plans SET status = ?, updated_at = ? WHERE id = ?",
            (status, timestamp, plan_id),
        )

    @classmethod
    def _expire_exhausted_leases(cls, connection: Any, plan_id: str, timestamp: str) -> None:
        connection.execute(
            """UPDATE acquisition_tasks SET status = 'failed', lease_owner = '',
                leased_until = NULL, outcome_code = 'lease_expired',
                error_summary = 'lease expired after the final allowed attempt',
                retryable = 0, next_attempt_at = NULL, updated_at = ?
            WHERE plan_id = ? AND status = 'leased' AND leased_until <= ?
              AND attempts >= max_attempts""",
            (timestamp, plan_id, timestamp),
        )
        cls._reconcile_acquisition_plan(connection, plan_id, timestamp)

    def claim_acquisition_tasks(
        self,
        plan_id: str,
        *,
        lease_owner: str,
        stage: str | None = None,
        limit: int = 1,
        lease_seconds: int = 300,
        now: str | datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Atomically claim ready or crash-expired work in stable order."""

        if not lease_owner.strip():
            raise ValueError("lease_owner is required")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if not _MIN_LEASE_SECONDS <= lease_seconds <= _MAX_LEASE_SECONDS:
            raise ValueError("lease_seconds must be between 30 and 3600")
        normalized_stage = _code(stage, field_name="stage") if stage is not None else None
        current = _moment(now)
        timestamp = current.isoformat()
        leased_until = (current + timedelta(seconds=lease_seconds)).isoformat()
        claimed: list[dict[str, Any]] = []
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute(
                    "SELECT 1 FROM acquisition_plans WHERE id = ?", (plan_id,)
                ).fetchone()
                is None
            ):
                raise ValueError("acquisition plan not found")
            self._expire_exhausted_leases(connection, plan_id, timestamp)
            stage_clause = " AND stage = ?" if normalized_stage else ""
            parameters: list[object] = [plan_id]
            if normalized_stage:
                parameters.append(normalized_stage)
            parameters.extend((timestamp, timestamp, limit))
            rows = connection.execute(
                f"""SELECT id FROM acquisition_tasks WHERE plan_id = ?{stage_clause}
                  AND attempts < max_attempts AND (
                    (status = 'pending' AND retryable = 1
                        AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
                    OR (status = 'leased' AND leased_until <= ?)
                  )
                ORDER BY
                    COALESCE(
                        CAST(json_extract(stage_snapshot_json, '$.priority_rank') AS INTEGER),
                        999
                    ),
                    COALESCE(next_attempt_at, created_at), id LIMIT ?""",
                parameters,
            ).fetchall()
            for row in rows:
                connection.execute(
                    """UPDATE acquisition_tasks SET status = 'leased', lease_owner = ?,
                        leased_until = ?, attempts = attempts + 1, last_attempt_at = ?,
                        next_attempt_at = NULL, outcome_code = '', outcome_json = '{}',
                        error_summary = '', retryable = 1, updated_at = ? WHERE id = ?""",
                    (lease_owner.strip(), leased_until, timestamp, timestamp, row["id"]),
                )
                task = connection.execute(
                    "SELECT * FROM acquisition_tasks WHERE id = ?", (row["id"],)
                ).fetchone()
                claimed.append(_decode_task(task))
        return claimed

    @staticmethod
    def _leased_task(connection: Any, task_id: str, owner: str, timestamp: str) -> Any:
        task = connection.execute(
            "SELECT * FROM acquisition_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if task is None:
            raise ValueError("acquisition task not found")
        if task["status"] != "leased" or task["lease_owner"] != owner.strip():
            raise ValueError("task is not leased by this owner")
        if str(task["leased_until"]) <= timestamp:
            raise ValueError("task lease has expired")
        return task

    def complete_acquisition_task(
        self,
        task_id: str,
        *,
        lease_owner: str,
        outcome_code: str,
        outcome: Mapping[str, object] | None = None,
        now: str | datetime | None = None,
    ) -> None:
        timestamp = _moment(now).isoformat()
        code = _code(outcome_code, field_name="outcome_code")
        encoded = _json_object(outcome or {}, field_name="outcome")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._leased_task(connection, task_id, lease_owner, timestamp)
            connection.execute(
                """UPDATE acquisition_tasks SET status = 'completed', lease_owner = '',
                    leased_until = NULL, outcome_code = ?, outcome_json = ?,
                    error_summary = '', retryable = 0, next_attempt_at = NULL,
                    updated_at = ? WHERE id = ?""",
                (code, encoded, timestamp, task_id),
            )
            self._reconcile_acquisition_plan(connection, str(task["plan_id"]), timestamp)

    def fail_acquisition_task(
        self,
        task_id: str,
        *,
        lease_owner: str,
        outcome_code: str,
        retryable: bool,
        error_summary: str = "",
        now: str | datetime | None = None,
    ) -> dict[str, Any]:
        current = _moment(now)
        timestamp = current.isoformat()
        code = _code(outcome_code, field_name="outcome_code")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._leased_task(connection, task_id, lease_owner, timestamp)
            will_retry = retryable and int(task["attempts"]) < int(task["max_attempts"])
            if will_retry:
                delay = min(
                    _BASE_BACKOFF_SECONDS * (2 ** max(int(task["attempts"]) - 1, 0)),
                    _MAX_BACKOFF_SECONDS,
                )
                status = "pending"
                next_attempt = (current + timedelta(seconds=delay)).isoformat()
            else:
                status = "failed"
                next_attempt = None
            connection.execute(
                """UPDATE acquisition_tasks SET status = ?, lease_owner = '',
                    leased_until = NULL, outcome_code = ?, error_summary = ?,
                    retryable = ?, next_attempt_at = ?, updated_at = ? WHERE id = ?""",
                (
                    status,
                    code,
                    error_summary.strip()[:500],
                    int(will_retry),
                    next_attempt,
                    timestamp,
                    task_id,
                ),
            )
            self._reconcile_acquisition_plan(connection, str(task["plan_id"]), timestamp)
            updated = connection.execute(
                "SELECT * FROM acquisition_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return _decode_task(updated)

    def list_acquisition_tasks(
        self,
        plan_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        parameters: tuple[object, ...] = (plan_id,)
        clause = ""
        if status is not None:
            normalized = status.strip().casefold()
            if normalized not in {"pending", "leased", "completed", "failed"}:
                raise ValueError("invalid acquisition task status")
            clause = " AND status = ?"
            parameters = (plan_id, normalized)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM acquisition_tasks WHERE plan_id = ?{clause}
                ORDER BY created_at, id""",
                parameters,
            ).fetchall()
        return [_decode_task(row) for row in rows]

    def active_acquisition_plan_by_name(self, name: str) -> dict[str, Any] | None:
        """Return the newest active plan with an exact operator-assigned name."""

        plan_name = name.strip()
        if not plan_name:
            raise ValueError("name is required")
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM acquisition_plans
                WHERE name = ? AND status = 'active'
                ORDER BY updated_at DESC, id DESC LIMIT 1""",
                (plan_name,),
            ).fetchone()
        return dict(row) if row is not None else None

    def acquisition_plan_status(
        self,
        plan_id: str,
        *,
        now: str | datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = _moment(now).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = connection.execute(
                "SELECT * FROM acquisition_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            if plan is None:
                raise ValueError("acquisition plan not found")
            self._expire_exhausted_leases(connection, plan_id, timestamp)
            plan = connection.execute(
                "SELECT * FROM acquisition_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            counts = {
                str(row["status"]): int(row["count"])
                for row in connection.execute(
                    """SELECT status, COUNT(*) AS count FROM acquisition_tasks
                    WHERE plan_id = ? GROUP BY status""",
                    (plan_id,),
                )
            }
            ready = connection.execute(
                """SELECT COUNT(*) FROM acquisition_tasks WHERE plan_id = ?
                AND status = 'pending' AND retryable = 1
                AND (next_attempt_at IS NULL OR next_attempt_at <= ?)""",
                (plan_id, timestamp),
            ).fetchone()[0]
            next_attempt = connection.execute(
                """SELECT MIN(next_attempt_at) FROM acquisition_tasks
                WHERE plan_id = ? AND status = 'pending' AND retryable = 1""",
                (plan_id,),
            ).fetchone()[0]
        result = dict(plan)
        result["counts"] = {
            status: counts.get(status, 0) for status in ("pending", "leased", "completed", "failed")
        }
        result["ready_tasks"] = int(ready)
        result["next_attempt_at"] = next_attempt
        return result
