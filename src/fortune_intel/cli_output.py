"""Structured CLI report writers kept separate from command dispatch."""

from __future__ import annotations

import csv
import json
import sys

from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_audit_ops import (
    COVERAGE_AUDIT_DEFINITION,
    coverage_audit_summary,
)


def write_coverage_audit(
    repository: JobRepository,
    *,
    output_format: str,
    query: str,
    status: str,
    limit: int,
    offset: int,
) -> None:
    if not 1 <= limit <= 10_000:
        raise ValueError("limit must be between 1 and 10000")
    if not 0 <= offset <= 100_000:
        raise ValueError("offset must be between 0 and 100000")
    records = repository.company_coverage_audit(
        include_synthetic=False,
        query=query,
        audit_status=status,
    )
    page = records[offset : offset + limit]
    if output_format == "csv":
        fieldnames = (
            list(page[0])
            if page
            else [
                "company_id",
                "company_name",
                "covered",
                "completed_gates",
                "total_gates",
                "next_action",
            ]
        )
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(page)
        return
    print(
        json.dumps(
            {
                "items": page,
                "count": len(page),
                "total": len(records),
                "limit": limit,
                "offset": offset,
                "summary": coverage_audit_summary(records),
                "definition": COVERAGE_AUDIT_DEFINITION,
            },
            indent=2,
        )
    )
