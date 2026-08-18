"""Typed contracts for deterministic public ATS connectors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ConnectorJob:
    """A normalized job returned directly by an employer's public ATS feed."""

    source: str
    external_job_id: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    # Opening/publish timestamp supplied by the ATS, when its public API has
    # one. Observation timestamps are assigned only by persistence.
    source_opened_at: str | None = None
    source_updated_at: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConnectorError:
    """A machine-readable crawl or record-level failure."""

    code: str
    message: str
    retryable: bool = False
    external_job_id: str | None = None
    page: int | None = None


@dataclass(frozen=True, slots=True)
class ConnectorResult:
    """A crawl manifest with explicit completeness semantics."""

    source: str
    source_key: str
    jobs: tuple[ConnectorJob, ...]
    complete: bool
    errors: tuple[ConnectorError, ...] = ()
    pages_fetched: int = 0
