import sys

import pytest

from fortune_intel import cli
from fortune_intel.services.source_approval import CompleteEmptyObservationPending


def test_approval_cli_reports_pending_empty_observation_cleanly(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli,
        "approve_source_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CompleteEmptyObservationPending(1)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "job-intel",
            "--database",
            str(tmp_path / "cli.db"),
            "approve-source-candidate",
            "7",
            "--terms-url",
            "https://example.test/terms",
            "--policy-approved-at",
            "2026-08-10T12:00:00+00:00",
            "--actor",
            "operator@example.test",
        ],
    )

    with pytest.raises(SystemExit, match="complete-empty observation 1 of 2 recorded"):
        cli.main()
