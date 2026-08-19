"""Bounded JSON HTTP client with deterministic retries and backoff."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import requests

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_RETRYABLE_WORKDAY_REDIRECT_STATUSES = {302, 303}
_RETRY_AFTER_STATUSES = {429, 503}
_PER_HOST_MAX_CONCURRENCY = 4
_PER_HOST_MIN_REQUEST_INTERVAL_SECONDS = 0.1
_MIN_RATE_LIMIT_DELAY_SECONDS = 2.0
_MAX_RETRY_DELAY_SECONDS = 60.0


class _HostRequestLimiter:
    """Coordinate request starts and in-flight work across all client instances."""

    def __init__(self) -> None:
        self._in_flight = threading.BoundedSemaphore(_PER_HOST_MAX_CONCURRENCY)
        self._start_lock = threading.Lock()
        self._next_start_at = 0.0

    @contextmanager
    def slot(
        self,
        *,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ):
        self._in_flight.acquire()
        try:
            # Holding this lock while waiting intentionally serializes request
            # starts for the host. The semaphore separately bounds requests that
            # remain in flight after their start time.
            with self._start_lock:
                now = monotonic()
                delay = max(0.0, self._next_start_at - now)
                if delay:
                    sleep(delay)
                started_at = max(self._next_start_at, monotonic())
                self._next_start_at = started_at + _PER_HOST_MIN_REQUEST_INTERVAL_SECONDS
            yield
        finally:
            self._in_flight.release()

    def defer_requests(
        self,
        delay: float,
        *,
        monotonic: Callable[[], float],
    ) -> None:
        """Apply a host-wide cooldown without releasing the fixed-host boundary."""

        bounded_delay = max(0.0, min(delay, _MAX_RETRY_DELAY_SECONDS))
        with self._start_lock:
            self._next_start_at = max(
                self._next_start_at,
                monotonic() + bounded_delay,
            )


_HOST_LIMITERS: dict[str, _HostRequestLimiter] = {}
_HOST_LIMITERS_LOCK = threading.Lock()


def _host_limiter(url: str) -> _HostRequestLimiter:
    hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    with _HOST_LIMITERS_LOCK:
        limiter = _HOST_LIMITERS.get(hostname)
        if limiter is None:
            limiter = _HostRequestLimiter()
            _HOST_LIMITERS[hostname] = limiter
        return limiter


def _is_retryable_status(status: int, url: str) -> bool:
    if status in _RETRYABLE_STATUSES:
        return True
    hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    is_workday_public_host = hostname.endswith((".myworkdayjobs.com", ".myworkdaysite.com"))
    return status in _RETRYABLE_WORKDAY_REDIRECT_STATUSES and is_workday_public_host


def _retry_after_seconds(value: object, *, now: float) -> float | None:
    """Parse Retry-After delta-seconds or an HTTP date without trusting it unboundedly."""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = retry_at.timestamp() - now
    if not math.isfinite(seconds):
        return None
    return max(0.0, min(seconds, _MAX_RETRY_DELAY_SECONDS))


@dataclass(frozen=True, slots=True)
class HttpFailure(Exception):
    code: str
    message: str
    url: str
    retryable: bool
    attempts: int
    status_code: int | None = None

    def __str__(self) -> str:
        return self.message


class JsonHttpClient:
    """Small injectable wrapper around requests for public, read-only feeds."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (5.0, 30.0),
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        user_agent: str = "OpenRole-Intelligence/0.1 (+public ATS feed)",
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if timeout[0] <= 0 or timeout[1] <= 0:
            raise ValueError("timeouts must be positive")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.sleep = sleep
        self.monotonic = monotonic
        self.wall_time = wall_time
        self.headers = {
            "Accept": "application/json",
            # Some public ATS endpoints negotiate zstd when it is advertised by
            # the underlying HTTP library.  Several endpoints (including
            # Amazon Jobs) send an invalid streamed zstd response in that case.
            # Restricting this client to broadly supported encodings keeps
            # ingestion deterministic without changing the response payload.
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": user_agent,
        }

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Any:
        response = self._request("GET", url, params=params)
        try:
            return response.json()
        except (ValueError, TypeError) as error:
            raise HttpFailure(
                "invalid_json",
                "ATS endpoint returned invalid JSON",
                url,
                False,
                1,
                int(response.status_code),
            ) from error

    def get_text(self, url: str, *, max_bytes: int = 2_000_000) -> str:
        """GET bounded UTF-8 text from a connector-owned fixed public endpoint."""

        if not 1_024 <= max_bytes <= 5_000_000:
            raise ValueError("max_bytes must be between 1024 and 5000000")
        response = self._request("GET", url, stream=True)
        body = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=65_536):
                if not isinstance(chunk, bytes):
                    raise HttpFailure(
                        "invalid_text", "ATS text endpoint returned non-byte content", url, False, 1
                    )
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise HttpFailure(
                        "response_too_large",
                        "ATS text endpoint exceeded the byte limit",
                        url,
                        False,
                        1,
                        int(response.status_code),
                    )
        finally:
            response.close()
        try:
            return bytes(body).decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise HttpFailure(
                "invalid_text",
                "ATS text endpoint returned invalid UTF-8",
                url,
                False,
                1,
                int(response.status_code),
            ) from error

    def post_json(
        self,
        url: str,
        *,
        json_body: Mapping[str, object],
    ) -> Any:
        """POST a bounded JSON request to a connector-owned fixed endpoint."""

        response = self._request("POST", url, json_body=json_body)
        try:
            return response.json()
        except (ValueError, TypeError) as error:
            raise HttpFailure(
                "invalid_json",
                "ATS endpoint returned invalid JSON",
                url,
                False,
                1,
                int(response.status_code),
            ) from error

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        json_body: Mapping[str, object] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        last_failure: HttpFailure | None = None
        limiter = _host_limiter(url)
        for attempt in range(1, self.max_attempts + 1):
            retry_after: float | None = None
            try:
                request = self.session.get if method == "GET" else self.session.post
                kwargs: dict[str, object] = {
                    "headers": self.headers,
                    "timeout": self.timeout,
                    # Hosts are fixed by each connector. Refusing redirects keeps
                    # an upstream response from turning into an arbitrary fetch.
                    "allow_redirects": False,
                    "stream": stream,
                }
                if params is not None:
                    kwargs["params"] = dict(params)
                if json_body is not None:
                    kwargs["json"] = dict(json_body)
                with limiter.slot(monotonic=self.monotonic, sleep=self.sleep):
                    response = request(
                        url,
                        **kwargs,
                    )
            except requests.Timeout as error:
                last_failure = HttpFailure(
                    "timeout", f"ATS request timed out: {error}", url, True, attempt
                )
            except requests.ConnectionError as error:
                last_failure = HttpFailure(
                    "connection_error",
                    f"ATS connection failed: {error}",
                    url,
                    True,
                    attempt,
                )
            except requests.RequestException as error:
                raise HttpFailure(
                    "request_error", f"ATS request failed: {error}", url, False, attempt
                ) from error
            else:
                status = int(response.status_code)
                if 200 <= status < 300:
                    return response
                retryable = _is_retryable_status(status, url)
                if status in _RETRY_AFTER_STATUSES:
                    headers = getattr(response, "headers", {})
                    retry_after = _retry_after_seconds(
                        headers.get("Retry-After", ""), now=self.wall_time()
                    )
                last_failure = HttpFailure(
                    "http_error",
                    f"ATS endpoint returned HTTP {status}",
                    url,
                    retryable,
                    attempt,
                    status,
                )
                if not retryable:
                    raise last_failure

                if status == 429:
                    backoff = self.backoff_seconds * (2 ** (attempt - 1))
                    limiter.defer_requests(
                        max(
                            backoff,
                            retry_after or 0.0,
                            _MIN_RATE_LIMIT_DELAY_SECONDS,
                        ),
                        monotonic=self.monotonic,
                    )

            if attempt < self.max_attempts:
                # The host limiter performs the 429 wait so every client for
                # that host observes the same cooldown. Other retryable
                # failures retain bounded per-request exponential backoff.
                if last_failure.status_code == 429:
                    continue
                backoff = self.backoff_seconds * (2 ** (attempt - 1))
                delay = max(backoff, retry_after or 0.0)
                self.sleep(min(delay, _MAX_RETRY_DELAY_SECONDS))

        assert last_failure is not None
        raise last_failure
