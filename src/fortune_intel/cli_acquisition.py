"""CLI integration for durable website and source-discovery acquisition work."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import Any

from fortune_intel.services.acquisition_planning import (
    ACQUISITION_SCOPES,
    ACQUISITION_STAGES,
    create_acquisition_plan,
)
from fortune_intel.services.acquisition_worker import run_acquisition_worker
from fortune_intel.services.acquisition_recovery import create_acquisition_recovery_plan
from fortune_intel.services.acquisition_scheduler import (
    DEFAULT_CADENCE_SECONDS,
    acquisition_scheduler_lock,
    verified_discovery_scheduler_loop,
)
from fortune_intel.services.continuous_acquisition import (
    acquisition_operational_metrics,
    continuous_acquisition_scheduler_loop,
)
from fortune_intel.storage import JobRepository


ACQUISITION_COMMANDS = frozenset(
    {
        "acquisition-plan-create",
        "acquisition-plan-requeue",
        "acquisition-plan-status",
        "acquisition-worker",
        "acquisition-metrics",
        "run-acquisition-scheduler",
        "run-discovery-scheduler",
    }
)


def _environment_policies() -> list[str]:
    return [value.strip() for value in os.getenv("ATS_POLICY_URLS", "").split(",") if value.strip()]


def add_acquisition_parsers(commands: argparse._SubParsersAction[Any]) -> None:
    create = commands.add_parser(
        "acquisition-plan-create",
        help="Freeze eligible website/discovery work into a durable plan",
    )
    create.add_argument("--name", required=True)
    create.add_argument("--scope", choices=ACQUISITION_SCOPES, required=True)
    create.add_argument("--actor", required=True)
    create.add_argument("--policy", action="append", default=[], metavar="KIND=URL")
    create.add_argument("--policy-approved-at", default="")
    create.add_argument("--interval", type=int, default=60)
    create.add_argument(
        "--companies-csv",
        help="Optional reviewed CSV with a company_name column to freeze only that exact batch",
    )

    status = commands.add_parser(
        "acquisition-plan-status",
        help="Show durable task counts and retry readiness for one plan",
    )
    status.add_argument("plan")

    commands.add_parser(
        "acquisition-metrics",
        help="Show checkpoint, dead-letter, supported-candidate, and family queues",
    )

    requeue = commands.add_parser(
        "acquisition-plan-requeue",
        help="Create a recovery plan from verified exhausted transient failures",
    )
    requeue.add_argument("plan", help="Failed source acquisition plan")
    requeue.add_argument("--name", required=True, help="Name for the immutable recovery plan")
    requeue.add_argument("--actor", required=True)
    requeue.add_argument("--stage", choices=ACQUISITION_STAGES)

    worker = commands.add_parser(
        "acquisition-worker",
        help="Claim and process one bounded batch from an acquisition plan",
    )
    worker.add_argument("plan")
    worker.add_argument("--stage", choices=ACQUISITION_STAGES, required=True)
    worker.add_argument("--lease-owner", required=True)
    worker.add_argument("--limit", type=int, default=10)
    worker.add_argument("--lease-seconds", type=int, default=300)
    worker.add_argument(
        "--user-agent",
        default=os.getenv("WIKIMEDIA_USER_AGENT", ""),
        help="Contactable Wikimedia User-Agent required for website workers",
    )

    scheduler = commands.add_parser(
        "run-discovery-scheduler",
        help="Continuously recheck uncovered companies from verified URL seeds",
    )
    scheduler.add_argument(
        "--cadence-seconds",
        type=int,
        default=int(os.getenv("DISCOVERY_CADENCE_SECONDS", str(DEFAULT_CADENCE_SECONDS))),
    )
    scheduler.add_argument(
        "--batch-size", type=int, default=int(os.getenv("DISCOVERY_BATCH_SIZE", "50"))
    )
    scheduler.add_argument(
        "--lease-seconds", type=int, default=int(os.getenv("DISCOVERY_LEASE_SECONDS", "300"))
    )
    scheduler.add_argument(
        "--max-batches", type=int, default=int(os.getenv("DISCOVERY_MAX_BATCHES", "200"))
    )
    scheduler.add_argument(
        "--actor", default=os.getenv("DISCOVERY_SCHEDULER_ACTOR", "verified-discovery-scheduler")
    )
    scheduler.add_argument(
        "--lease-owner", default=os.getenv("DISCOVERY_LEASE_OWNER", "discovery-scheduler-1")
    )

    continuous = commands.add_parser(
        "run-acquisition-scheduler",
        help="Continuously acquire official URLs, discover sources, and activate policy-ready boards",
    )
    continuous.add_argument(
        "--cadence-seconds",
        type=int,
        default=int(os.getenv("ACQUISITION_CADENCE_SECONDS", str(DEFAULT_CADENCE_SECONDS))),
    )
    continuous.add_argument(
        "--poll-seconds", type=int, default=int(os.getenv("ACQUISITION_POLL_SECONDS", "60"))
    )
    continuous.add_argument(
        "--batch-size", type=int, default=int(os.getenv("ACQUISITION_BATCH_SIZE", "50"))
    )
    continuous.add_argument(
        "--lease-seconds", type=int, default=int(os.getenv("ACQUISITION_LEASE_SECONDS", "300"))
    )
    continuous.add_argument(
        "--max-batches", type=int, default=int(os.getenv("ACQUISITION_MAX_BATCHES", "200"))
    )
    continuous.add_argument(
        "--actor",
        default=os.getenv("ACQUISITION_SCHEDULER_ACTOR", "continuous-acquisition-scheduler"),
    )
    continuous.add_argument(
        "--lease-owner",
        default=os.getenv("ACQUISITION_LEASE_OWNER", "continuous-acquisition-1"),
    )
    continuous.add_argument(
        "--user-agent",
        default=os.getenv("WIKIMEDIA_USER_AGENT", ""),
        help="Contactable Wikimedia User-Agent required for exact-CIK website acquisition",
    )
    continuous.add_argument(
        "--policy", action="append", default=_environment_policies(), metavar="KIND=URL"
    )
    continuous.add_argument("--policy-approved-at", default=os.getenv("ATS_POLICY_APPROVED_AT", ""))
    continuous.add_argument(
        "--interval", type=int, default=int(os.getenv("SOURCE_SYNC_INTERVAL_MINUTES", "60"))
    )


def _policy_urls(values: list[str]) -> dict[str, str]:
    policies: dict[str, str] = {}
    for value in values:
        kind, separator, url = value.partition("=")
        if not separator or not kind.strip() or not url.strip():
            raise ValueError("each --policy must use KIND=URL")
        normalized = kind.strip().casefold()
        if normalized in policies:
            raise ValueError(f"duplicate policy kind: {normalized}")
        policies[normalized] = url.strip()
    return policies


def run_acquisition_command(
    args: argparse.Namespace,
    repository: JobRepository,
) -> dict[str, object]:
    if args.command == "acquisition-plan-create":
        company_ids = None
        if args.companies_csv:
            with Path(args.companies_csv).open(newline="", encoding="utf-8-sig") as handle:
                names = [(row.get("company_name") or "").strip() for row in csv.DictReader(handle)]
            if not names or any(not name for name in names):
                raise ValueError("companies CSV must contain non-empty company_name values")
            company_ids = set()
            for name in names:
                company = repository.find_company_by_normalized_name(name)
                if company is None:
                    raise ValueError(f"company not found or ambiguous: {name}")
                company_ids.add(int(company["id"]))
        policies = _policy_urls(args.policy)
        plan_id = create_acquisition_plan(
            repository,
            name=args.name,
            scope=args.scope,
            actor=args.actor,
            company_ids=company_ids,
            policy_urls=policies,
            policy_approved_at=args.policy_approved_at,
            sync_interval_minutes=args.interval,
        )
        return repository.acquisition_plan_status(plan_id)
    if args.command == "acquisition-plan-status":
        return repository.acquisition_plan_status(args.plan)
    if args.command == "acquisition-metrics":
        return acquisition_operational_metrics(repository)
    if args.command == "acquisition-plan-requeue":
        plan_id = create_acquisition_recovery_plan(
            repository,
            args.plan,
            name=args.name,
            actor=args.actor,
            stage=args.stage,
        )
        result = repository.acquisition_plan_status(plan_id)
        result["source_plan_id"] = args.plan
        result["requeued_tasks"] = result["total_tasks"]
        return result
    if args.command == "acquisition-worker":
        return run_acquisition_worker(
            repository,
            args.plan,
            stage=args.stage,
            lease_owner=args.lease_owner,
            limit=args.limit,
            lease_seconds=args.lease_seconds,
            wikimedia_user_agent=args.user_agent,
        )
    if args.command == "run-discovery-scheduler":
        with acquisition_scheduler_lock(args.database):
            verified_discovery_scheduler_loop(
                repository,
                cadence_seconds=args.cadence_seconds,
                batch_size=args.batch_size,
                lease_seconds=args.lease_seconds,
                max_batches=args.max_batches,
                actor=args.actor,
                lease_owner=args.lease_owner,
            )
        return {"status": "stopped"}
    if args.command == "run-acquisition-scheduler":
        policies = _policy_urls(args.policy)
        if policies and not args.policy_approved_at:
            raise ValueError("--policy-approved-at is required when --policy is configured")
        with acquisition_scheduler_lock(args.database):
            continuous_acquisition_scheduler_loop(
                repository,
                poll_seconds=args.poll_seconds,
                cadence_seconds=args.cadence_seconds,
                batch_size=args.batch_size,
                lease_seconds=args.lease_seconds,
                max_batches=args.max_batches,
                actor=args.actor,
                lease_owner=args.lease_owner,
                wikimedia_user_agent=args.user_agent,
                policy_urls=policies,
                policy_approved_at=args.policy_approved_at,
                sync_interval_minutes=args.interval,
            )
        return {"status": "stopped"}
    raise ValueError(f"unsupported acquisition command: {args.command}")
