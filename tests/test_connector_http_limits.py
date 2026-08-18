from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from fortune_intel.connectors import HttpFailure, JsonHttpClient


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, headers=None, chunks=None):
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}
        self.chunks = chunks or []
        self.closed = False

    def json(self):
        return self.payload

    def iter_content(self, *, chunk_size):
        assert chunk_size == 65_536
        yield from self.chunks

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = deque(responses)

    def get(self, _url, **_kwargs):
        return self.responses.popleft()


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_http_client_rate_limits_request_starts_across_instances():
    clock = FakeClock()
    first = JsonHttpClient(
        session=FakeSession([FakeResponse({"page": 1})]),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    second = JsonHttpClient(
        session=FakeSession([FakeResponse({"page": 2})]),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert first.get_json("https://shared-rate.example.test/one") == {"page": 1}
    assert second.get_json("https://shared-rate.example.test/two") == {"page": 2}
    assert clock.sleeps == [0.1]


def test_http_client_limits_in_flight_requests_across_instances(monkeypatch):
    from fortune_intel.connectors import http

    monkeypatch.setattr(http, "_PER_HOST_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
    condition = threading.Condition()
    release = threading.Event()
    state = {"active": 0, "maximum": 0, "calls": 0}

    class BlockingSession:
        def get(self, _url, **_kwargs):
            with condition:
                state["active"] += 1
                state["calls"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
                condition.notify_all()
            if not release.wait(timeout=2):
                raise AssertionError("test did not release blocked HTTP requests")
            with condition:
                state["active"] -= 1
                condition.notify_all()
            return FakeResponse({"ok": True})

    clients = [JsonHttpClient(session=BlockingSession()) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(
                client.get_json,
                "https://shared-concurrency.example.test/jobs",
            )
            for client in clients
        ]
        deadline = time.monotonic() + 2
        with condition:
            while state["active"] < 4 and time.monotonic() < deadline:
                condition.wait(timeout=0.05)
            assert state["active"] == 4
            assert state["calls"] == 4
        release.set()
        assert [future.result(timeout=2) for future in futures] == [{"ok": True}] * 6

    assert state["maximum"] == 4


def test_rate_limit_cooldown_is_shared_across_client_instances(monkeypatch):
    from fortune_intel.connectors import http

    monkeypatch.setattr(http, "_PER_HOST_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
    clock = FakeClock()
    host = "https://shared-cooldown.example.test"
    first = JsonHttpClient(
        session=FakeSession([FakeResponse(status_code=429)]),
        max_attempts=1,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        wall_time=clock.monotonic,
    )
    second = JsonHttpClient(
        session=FakeSession([FakeResponse({"ok": True})]),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    with pytest.raises(HttpFailure) as captured:
        first.get_json(f"{host}/first")

    assert captured.value.status_code == 429
    assert second.get_json(f"{host}/second") == {"ok": True}
    assert clock.sleeps == [2.0]


def test_text_response_is_streamed_with_a_hard_byte_limit():
    response = FakeResponse(chunks=[b"a" * 700, b"b" * 400])
    client = JsonHttpClient(session=FakeSession([response]))

    with pytest.raises(HttpFailure) as captured:
        client.get_text("https://bounded-text.example.test/job", max_bytes=1024)

    assert captured.value.code == "response_too_large"
    assert response.closed is True


def test_text_response_requires_strict_utf8_and_closes_response():
    response = FakeResponse(chunks=[b"a" * 1024, b"\xff"])
    client = JsonHttpClient(session=FakeSession([response]))

    with pytest.raises(HttpFailure) as captured:
        client.get_text("https://utf8-text.example.test/job", max_bytes=2048)

    assert captured.value.code == "invalid_text"
    assert response.closed is True
