"""CLI boundary for recorded, provider-permitted search result harvesting."""

from __future__ import annotations

import argparse
from typing import Any

from fortune_intel.services.search_ats_harvest import (
    harvest_verified_search_ats_results,
    load_recorded_search_results,
)
from fortune_intel.storage import JobRepository

SEARCH_HARVEST_COMMANDS = frozenset({"harvest-search-ats-results"})


def add_search_harvest_parser(commands: argparse._SubParsersAction[Any]) -> None:
    harvest = commands.add_parser(
        "harvest-search-ats-results",
        help="Verify recorded search-result ATS URLs; creates candidates only, never live sources",
    )
    harvest.add_argument("results_jsonl", help="Recorded permitted-provider results; one JSON object per line")
    harvest.add_argument("--policy", action="append", required=True, metavar="KIND=URL")
    harvest.add_argument("--policy-approved-at", required=True)
    harvest.add_argument("--actor", required=True)
    harvest.add_argument("--concurrency", type=int, default=4)


def _policy_urls(values: list[str]) -> dict[str, str]:
    policies: dict[str, str] = {}
    for value in values:
        kind, separator, url = value.partition("=")
        normalized = kind.strip().casefold()
        if not separator or not normalized or not url.strip() or normalized in policies:
            raise ValueError("each --policy must use a unique KIND=URL")
        policies[normalized] = url.strip()
    return policies


def run_search_harvest_command(args: argparse.Namespace, repository: JobRepository) -> dict[str, int]:
    return harvest_verified_search_ats_results(
        repository,
        load_recorded_search_results(args.results_jsonl),
        actor=args.actor,
        policy_urls=_policy_urls(args.policy),
        policy_approved_at=args.policy_approved_at,
        concurrency=args.concurrency,
    )
