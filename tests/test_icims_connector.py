import pytest

from fortune_intel.connectors import (
    ICIMSPolicyHeldConnector,
    build_connector,
    icims_source,
    parse_icims_source_key,
)
from fortune_intel.services.source_approval import approve_source_candidate
from fortune_intel.storage import JobRepository


class FailingClient:
    def get_json(self, url, *, params=None):
        raise AssertionError(f"policy-held connector must not request {url}")

    def post_json(self, url, *, json_body):
        raise AssertionError(f"policy-held connector must not request {url}")


def test_source_identity_uses_only_exact_observed_customer_host():
    source = icims_source("Careers-Acme.ICIMS.com.")

    assert source.key == "careers-acme.icims.com"
    assert source.public_base_url == "https://careers-acme.icims.com/jobs/search"
    assert parse_icims_source_key(source.key) == source


@pytest.mark.parametrize(
    "host",
    [
        "icims.com",
        "icims.eu",
        "api.icims.com",
        "api.dev.icims.com",
        "developer-community.icims.com",
        "careers-acme.icims.com.evil.example",
        "-careers.icims.com",
        "careers..icims.com",
        "cäreers.icims.com",
    ],
)
def test_source_identity_rejects_non_customer_or_unsafe_hosts(host):
    with pytest.raises(ValueError, match="exact customer portal host"):
        icims_source(host)


def test_policy_held_probe_never_calls_http_and_fails_closed():
    connector = ICIMSPolicyHeldConnector(
        "careers-acme.icims.com",
        client=FailingClient(),
    )

    result = connector.fetch()

    assert result.source == "icims"
    assert result.source_key == "careers-acme.icims.com"
    assert result.jobs == ()
    assert result.complete is False
    assert result.pages_fetched == 0
    assert len(result.errors) == 1
    assert result.errors[0].code == "policy_review_required"
    assert result.errors[0].retryable is False
    assert "Integration User" in result.errors[0].message


def test_factory_builds_only_the_policy_held_icims_probe():
    connector = build_connector(
        "icims",
        "jobs-example.icims.eu",
        client=FailingClient(),
    )

    assert isinstance(connector, ICIMSPolicyHeldConnector)
    assert connector.fetch().complete is False


def test_policy_held_candidate_cannot_be_approved_or_scheduled(tmp_path):
    repository = JobRepository(tmp_path / "icims-policy.db")
    repository.initialize()
    company_id = repository.upsert_company("Acme")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://careers-acme.icims.com/jobs/search",
        kind="icims",
        confidence=0.99,
        evidence={"observed_url": "https://careers-acme.icims.com/jobs/search"},
        robots_status="allowed",
        terms_status="review_required",
    )

    with pytest.raises(ValueError, match="no longer matches its supported connector"):
        approve_source_candidate(
            repository,
            candidate_id,
            terms_url="https://www.icims.com/legal/terms-of-use/",
            policy_approved_at="2026-08-10T00:00:00+00:00",
            actor="policy-review@example.org",
        )

    assert repository.due_career_sources() == []
    assert repository.get_source_candidate(candidate_id)["status"] == "discovered"
