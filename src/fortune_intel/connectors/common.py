"""Shared parsing helpers for public ATS payloads."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from fortune_intel.connectors.http import HttpFailure
from fortune_intel.connectors.models import ConnectorError


def clean_html(value: object) -> str:
    if not value:
        return ""
    decoded = html.unescape(html.unescape(str(value)))
    return " ".join(BeautifulSoup(decoded, "html.parser").get_text(" ").split())


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def require_text(record: dict[str, object], key: str) -> str:
    value = clean_text(record.get(key))
    if not value:
        raise ValueError(f"missing required field: {key}")
    return value


def require_public_url(value: object) -> str:
    url = clean_text(value)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("job URL must be an absolute HTTPS URL")
    return url


def normalize_timestamp(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value) / 1000 if abs(float(value)) >= 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = clean_text(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def location_from_parts(*parts: object, remote: bool = False) -> str:
    values: list[str] = []
    if remote:
        values.append("Remote")
    for part in parts:
        value = clean_text(part)
        if value and value.casefold() not in {item.casefold() for item in values}:
            values.append(value)
    return ", ".join(values)


def http_error(error: HttpFailure, *, page: int | None = None) -> ConnectorError:
    return ConnectorError(
        code=error.code,
        message=error.message,
        retryable=error.retryable,
        page=page,
    )


def record_error(error: Exception, *, external_id: str | None = None) -> ConnectorError:
    return ConnectorError(
        code="invalid_job",
        message=str(error),
        external_job_id=external_id,
    )
