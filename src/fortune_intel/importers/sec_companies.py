"""Importer for the public SEC company ticker/CIK association file."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fortune_intel.storage import JobRepository

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def import_sec_companies(
    repository: JobRepository,
    json_path: str | Path,
    *,
    collection_year: int | None = None,
) -> dict[str, int]:
    path = Path(json_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.values() if isinstance(payload, dict) else payload
    if not isinstance(records, (list, tuple)) and not hasattr(records, "__iter__"):
        raise ValueError("SEC company ticker file must contain an object or list")
    imported = 0
    skipped = 0
    for record in records:
        if not isinstance(record, dict):
            skipped += 1
            continue
        name = str(record.get("title") or "").strip()
        cik = record.get("cik_str")
        ticker = str(record.get("ticker") or "").strip()
        if not name or cik in {None, ""} or not ticker:
            skipped += 1
            continue
        repository.upsert_company(
            name,
            collection_name="SEC EDGAR company tickers",
            collection_year=collection_year or datetime.now(UTC).year,
            sec_cik=str(cik),
            ticker=ticker,
        )
        imported += 1
    return {"companies_imported": imported, "records_skipped": skipped}
