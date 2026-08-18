"""Import a user-supplied company collection without redistributing licensed lists."""

from __future__ import annotations

import csv
from pathlib import Path

from fortune_intel.storage import JobRepository


def _first(row: dict[str, str], *names: str) -> str:
    lowered = {key.casefold().strip(): (value or "").strip() for key, value in row.items()}
    for name in names:
        if value := lowered.get(name.casefold()):
            return value
    return ""


def import_companies(
    repository: JobRepository,
    csv_path: str | Path,
    *,
    collection_name: str = "Custom",
    collection_year: int | None = None,
) -> int:
    count = 0
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            name = _first(row, "Company Name", "company", "name")
            if not name:
                continue
            rank_text = _first(row, "Rank", "Fortune Rank", "collection_rank")
            repository.upsert_company(
                name,
                career_url=_first(row, "Career Search URL", "career_url", "url"),
                website_url=_first(row, "Website URL", "website_url", "website"),
                ats_type=_first(row, "Platform Type", "ats_type", "source"),
                collection_name=collection_name,
                collection_year=collection_year,
                collection_rank=int(rank_text) if rank_text.isdigit() else None,
            )
            count += 1
    return count
