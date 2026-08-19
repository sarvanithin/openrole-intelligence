"""Policy-held iCIMS source identity and fail-closed connector probe.

The official iCIMS Job Portal API requires an authorized Integration User and
Basic authentication. Public career-site HTML is therefore inventory evidence,
not an ingestible API contract. This connector intentionally performs no HTTP.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from fortune_intel.connectors.http import JsonHttpClient
from fortune_intel.connectors.models import ConnectorError, ConnectorResult

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ICIMS_SUFFIXES = ("icims.com", "icims.eu")
_NON_PORTAL_LABELS = frozenset(
    {
        "api",
        "care",
        "community",
        "developer",
        "developer-community",
        "login",
        "social",
        "support",
        "www",
    }
)

POLICY_HOLD_REASON = (
    "iCIMS collection is policy-held: the official Job Portal API requires Basic "
    "authentication by an authorized Integration User, and no documented "
    "unauthenticated complete public feed or permission for automated career-site "
    "extraction has been verified"
)


@dataclass(frozen=True, slots=True)
class ICIMSSource:
    """An exact observed iCIMS-hosted public job-search URL."""

    host: str

    @property
    def key(self) -> str:
        return self.host

    @property
    def public_base_url(self) -> str:
        return f"https://{self.host}/jobs/search"


def icims_source(host: str) -> ICIMSSource:
    """Validate an exact customer portal host without deriving a tenant name."""

    normalized = host.strip().casefold().rstrip(".")
    labels = normalized.split(".")
    suffix_matches = any(
        normalized.endswith(f".{suffix}") and normalized != suffix for suffix in _ICIMS_SUFFIXES
    )
    if (
        not normalized
        or len(normalized) > 253
        or not normalized.isascii()
        or not suffix_matches
        or any(_DNS_LABEL.fullmatch(label) is None for label in labels)
        or any(label in _NON_PORTAL_LABELS for label in labels[:-2])
    ):
        raise ValueError("iCIMS source must be an exact customer portal host")
    return ICIMSSource(normalized)


def parse_icims_source_key(value: str) -> ICIMSSource:
    """Parse the stored source key, which is the exact observed portal host."""

    return icims_source(value)


def classify_icims_board_url(url: str) -> ICIMSSource | None:
    """Validate an exact public search or job-detail observation without deriving a URL."""

    value = url.strip()
    if not value or len(value) > 4096 or "\\" in value:
        return None
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or "%" in parsed.path
        or "//" in parsed.path
    ):
        return None
    path = parsed.path.rstrip("/")
    if (
        path != "/jobs/search"
        and re.fullmatch(r"/jobs/[1-9][0-9]{0,19}(?:/[A-Za-z0-9._~-]+/job)?", path) is None
    ):
        return None
    try:
        return icims_source(parsed.hostname or "")
    except ValueError:
        return None


class ICIMSPolicyHeldConnector:
    """Return a deterministic policy hold without requesting an iCIMS endpoint."""

    source = "icims"

    def __init__(self, source_key: str, *, client: JsonHttpClient | None = None) -> None:
        self.icims = parse_icims_source_key(source_key)
        # Keep the normal connector construction signature. The client is never
        # used while policy is held, which tests can assert explicitly.
        self.client = client

    def fetch(self) -> ConnectorResult:
        return ConnectorResult(
            source=self.source,
            source_key=self.icims.key,
            jobs=(),
            complete=False,
            errors=(
                ConnectorError(
                    code="policy_review_required",
                    message=POLICY_HOLD_REASON,
                    retryable=False,
                ),
            ),
            pages_fetched=0,
        )
