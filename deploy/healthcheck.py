#!/usr/bin/env python3
"""Database-aware container health check using only the standard library."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen


def main() -> int:
    port = os.getenv("PORT", "8000")
    url = os.getenv("HEALTHCHECK_URL", f"http://127.0.0.1:{port}/readyz")
    allowed_hosts = os.getenv("JOB_INTEL_ALLOWED_HOSTS", "127.0.0.1").split(",")
    default_host = next((host.strip() for host in allowed_hosts if host.strip()), "127.0.0.1")
    health_host = os.getenv("HEALTHCHECK_HOST", default_host)
    request = Request(
        url,
        headers={
            "Host": health_host,
            "User-Agent": "openrole-container-health/1.0",
        },
    )
    try:
        with urlopen(request, timeout=3) as response:  # noqa: S310 - fixed operator URL
            if response.status != 200:
                raise RuntimeError(f"unexpected HTTP status {response.status}")
            payload = json.load(response)
    except (OSError, URLError, ValueError, RuntimeError) as error:
        print(f"unhealthy: {error}", file=sys.stderr)
        return 1

    if not isinstance(payload, dict) or payload.get("status") != "ready" or not payload.get("ready"):
        print("unhealthy: readiness response did not report ready", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
