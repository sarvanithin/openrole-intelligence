"""Validated runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(value.strip() for value in os.getenv(name, default).split(",") if value.strip())


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path("data/job_intel.db")
    environment: str = "development"
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    cors_origins: tuple[str, ...] = ()
    rate_limit_per_minute: int = 120
    public_base_url: str = "http://127.0.0.1:8000"
    contact_email: str = ""
    show_synthetic: bool = True

    @classmethod
    def from_env(cls, *, database_path: str | Path | None = None) -> Settings:
        environment = os.getenv("JOB_INTEL_ENV", "development").casefold()
        settings = cls(
            database_path=Path(database_path or os.getenv("JOB_INTEL_DB", "data/job_intel.db")),
            environment=environment,
            allowed_hosts=_csv(
                "JOB_INTEL_ALLOWED_HOSTS",
                "127.0.0.1,localhost,testserver" if environment != "production" else "",
            ),
            cors_origins=_csv("JOB_INTEL_CORS_ORIGINS"),
            rate_limit_per_minute=int(os.getenv("JOB_INTEL_RATE_LIMIT", "120")),
            public_base_url=os.getenv("JOB_INTEL_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/"),
            contact_email=os.getenv("JOB_INTEL_CONTACT_EMAIL", ""),
            show_synthetic=os.getenv(
                "JOB_INTEL_SHOW_SYNTHETIC",
                "false" if environment == "production" else "true",
            ).casefold()
            == "true",
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("JOB_INTEL_ENV must be development, test, or production")
        if not 10 <= self.rate_limit_per_minute <= 10000:
            raise ValueError("JOB_INTEL_RATE_LIMIT must be between 10 and 10000")
        if self.environment == "production":
            if not self.allowed_hosts or "*" in self.allowed_hosts:
                raise ValueError("production requires explicit JOB_INTEL_ALLOWED_HOSTS")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("production JOB_INTEL_PUBLIC_URL must use HTTPS")
            if "@" not in self.contact_email:
                raise ValueError("production requires JOB_INTEL_CONTACT_EMAIL")
            if self.show_synthetic:
                raise ValueError("production cannot enable synthetic demo records")
