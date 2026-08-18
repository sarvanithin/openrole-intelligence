"""Structured, redacted operational logging."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("openrole")


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")


def log_event(event: str, **fields: Any) -> None:
    safe = {
        key: value
        for key, value in fields.items()
        if key not in {"description", "metadata", "board_token", "query"}
    }
    logger.info(
        json.dumps(
            {"timestamp": datetime.now(UTC).isoformat(), "event": event, **safe},
            default=str,
            sort_keys=True,
        )
    )
