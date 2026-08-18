import csv

import pytest

from fortune_intel.importers.sources import import_source_registry
from fortune_intel.storage import JobRepository


def write_registry(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "company_name",
                "kind",
                "board_token",
                "base_url",
                "terms_url",
                "policy_approved_at",
                "owner_contact",
                "sync_interval_minutes",
                "enabled",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def test_bulk_source_import_registers_only_reviewed_fixed_hosts(tmp_path):
    repository = JobRepository(tmp_path / "sources.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "greenhouse",
                "board_token": "example",
                "base_url": "https://boards.greenhouse.io/example",
                "terms_url": "https://example.org/terms",
                "policy_approved_at": "2026-08-06T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    assert import_source_registry(repository, registry) == 1
    source = repository.due_career_sources()[0]
    assert source["board_token"] == "example"
    assert source["sync_interval_minutes"] == 60


def test_bulk_source_import_rejects_arbitrary_hosts_before_writing(tmp_path):
    repository = JobRepository(tmp_path / "sources.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "greenhouse",
                "board_token": "example",
                "base_url": "https://attacker.example/internal",
                "terms_url": "https://example.org/terms",
                "policy_approved_at": "2026-08-06T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    with pytest.raises(ValueError, match="approved greenhouse public host"):
        import_source_registry(repository, registry)
    assert repository.source_status() == []


def test_bulk_source_reimport_changes_cadence_and_makes_source_due(tmp_path):
    repository = JobRepository(tmp_path / "reschedule.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    row = {
        "company_name": "Example Company",
        "kind": "greenhouse",
        "board_token": "example",
        "base_url": "https://boards.greenhouse.io/example",
        "terms_url": "https://example.org/terms",
        "policy_approved_at": "2026-08-06T12:00:00Z",
        "owner_contact": "owner@example.org",
        "sync_interval_minutes": "360",
        "enabled": "true",
    }
    write_registry(registry, [row])
    import_source_registry(repository, registry)
    source = repository.due_career_sources()[0]
    repository.mark_source_finished(source["id"], success=True)
    assert repository.due_career_sources() == []

    row["sync_interval_minutes"] = "60"
    write_registry(registry, [row])
    import_source_registry(repository, registry)

    due = repository.due_career_sources()
    assert len(due) == 1
    assert due[0]["sync_interval_minutes"] == 60


def test_bulk_source_import_accepts_exact_reviewed_workday_host_tenant_and_site(tmp_path):
    repository = JobRepository(tmp_path / "workday.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "workday",
                "board_token": "acme.wd5.myworkdayjobs.com|acme|External",
                "base_url": "https://acme.wd5.myworkdayjobs.com/External",
                "terms_url": "https://example.org/terms",
                "policy_approved_at": "2026-08-07T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    assert import_source_registry(repository, registry) == 1
    source = repository.due_career_sources()[0]
    assert source["kind"] == "workday"
    assert source["board_token"] == "acme.wd5.myworkdayjobs.com|acme|External"


def test_bulk_source_import_accepts_exact_official_structured_manifest(tmp_path):
    repository = JobRepository(tmp_path / "structured.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    manifest = "https://careers.example.com/job-sitemap.xml"
    write_registry(
        registry,
        [{
            "company_name": "Example Company",
            "kind": "official_structured",
            "board_token": manifest,
            "base_url": manifest,
            "terms_url": "https://example.com/terms",
            "policy_approved_at": "2026-08-13T12:00:00Z",
            "owner_contact": "owner@example.org",
        }],
    )

    assert import_source_registry(repository, registry) == 1
    assert repository.due_career_sources()[0]["board_token"] == manifest


def test_bulk_source_import_rejects_workday_host_token_mismatch(tmp_path):
    repository = JobRepository(tmp_path / "workday-mismatch.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "workday",
                "board_token": "acme.wd5.myworkdayjobs.com|acme|External",
                "base_url": "https://other.wd5.myworkdayjobs.com/External",
                "terms_url": "https://example.org/terms",
                "policy_approved_at": "2026-08-07T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    with pytest.raises(ValueError, match="approved workday public host"):
        import_source_registry(repository, registry)


def test_bulk_source_import_accepts_exact_recruiting_path_workday_source(tmp_path):
    repository = JobRepository(tmp_path / "workdaysite.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "workday",
                "board_token": "wd5.myworkdaysite.com|chewy|External",
                "base_url": ("https://wd5.myworkdaysite.com/recruiting/chewy/External"),
                "terms_url": "https://example.org/terms",
                "policy_approved_at": "2026-08-11T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    assert import_source_registry(repository, registry) == 1
    source = repository.due_career_sources()[0]
    assert source["board_token"] == "wd5.myworkdaysite.com|chewy|External"
    assert source["base_url"] == ("https://wd5.myworkdaysite.com/recruiting/chewy/External")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://wd5.myworkdaysite.com/recruiting/other/External",
        "https://wd5.myworkdaysite.com/recruiting/chewy/Other",
        "https://wd1.myworkdaysite.com/recruiting/chewy/External",
        "https://wd5.myworkdaysite.com:443/recruiting/chewy/External",
        "https://wd5.myworkdaysite.com/recruiting/chewy/External?source=review",
        "https://wd5.myworkdaysite.com/recruiting/chewy/External#jobs",
        "https://wd5.myworkdaysite.com/recruiting/chewy/External?",
        "https://wd5.myworkdaysite.com/recruiting/chewy/External#",
    ],
)
def test_bulk_source_import_rejects_mismatched_or_ambiguous_recruiting_path_source(
    tmp_path, base_url
):
    repository = JobRepository(tmp_path / "workdaysite-mismatch.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "workday",
                "board_token": "wd5.myworkdaysite.com|chewy|External",
                "base_url": base_url,
                "terms_url": "https://example.org/terms",
                "policy_approved_at": "2026-08-11T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    with pytest.raises(ValueError, match="approved workday public host"):
        import_source_registry(repository, registry)


def test_bulk_source_import_accepts_exact_reviewed_ukg_board(tmp_path):
    repository = JobRepository(tmp_path / "ukg.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    board_id = "2af23579-6cf8-4926-be1a-3bc74872c197"
    base_url = f"https://recruiting2.ultipro.com/ARC1026ARCOI/JobBoard/{board_id}"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "ukg_recruiting_public",
                "board_token": (
                    f"recruiting2.ultipro.com|ARC1026ARCOI|{board_id}"
                ),
                "base_url": base_url,
                "terms_url": "https://example.org/written-authorization",
                "policy_approved_at": "2026-08-12T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    assert import_source_registry(repository, registry) == 1
    source = repository.due_career_sources()[0]
    assert source["kind"] == "ukg_recruiting_public"
    assert source["base_url"] == base_url


@pytest.mark.parametrize(
    ("board_token", "base_url"),
    [
        (
            "recruiting2.ultipro.com|OTHER|2af23579-6cf8-4926-be1a-3bc74872c197",
            "https://recruiting2.ultipro.com/ARC1026ARCOI/JobBoard/"
            "2af23579-6cf8-4926-be1a-3bc74872c197",
        ),
        (
            "recruiting2.ultipro.com|ARC1026ARCOI|2af23579-6cf8-4926-be1a-3bc74872c197",
            "https://recruiting.ultipro.com/ARC1026ARCOI/JobBoard/"
            "2af23579-6cf8-4926-be1a-3bc74872c197",
        ),
        (
            "recruiting2.ultipro.com|ARC1026ARCOI|2af23579-6cf8-4926-be1a-3bc74872c197",
            "https://recruiting2.ultipro.com/ARC1026ARCOI/JobBoard/"
            "2af23579-6cf8-4926-be1a-3bc74872c197?source=review",
        ),
        (
            "recruiting2.ultipro.com|ARC1026ARCOI|2af23579-6cf8-4926-be1a-3bc74872c197",
            "https://recruiting2.ultipro.com:443/ARC1026ARCOI/JobBoard/"
            "2af23579-6cf8-4926-be1a-3bc74872c197",
        ),
    ],
)
def test_bulk_source_import_rejects_ukg_board_identity_mismatch(
    tmp_path, board_token, base_url
):
    repository = JobRepository(tmp_path / "ukg-mismatch.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "ukg_recruiting_public",
                "board_token": board_token,
                "base_url": base_url,
                "terms_url": "https://example.org/written-authorization",
                "policy_approved_at": "2026-08-12T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    with pytest.raises(ValueError, match="approved ukg_recruiting_public public host"):
        import_source_registry(repository, registry)




def test_bulk_source_import_accepts_exact_reviewed_oracle_host_locale_and_site(tmp_path):
    repository = JobRepository(tmp_path / "oracle.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "oracle_recruiting",
                "board_token": "tenant.fa.oraclecloud.com|en|CX_1",
                "base_url": (
                    "https://tenant.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
                ),
                "terms_url": "https://example.org/terms",
                "policy_approved_at": "2026-08-07T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    assert import_source_registry(repository, registry) == 1
    source = repository.due_career_sources()[0]
    assert source["kind"] == "oracle_recruiting"
    assert source["board_token"] == "tenant.fa.oraclecloud.com|en|CX_1"


def test_bulk_source_import_rejects_oracle_host_token_mismatch(tmp_path):
    repository = JobRepository(tmp_path / "oracle-mismatch.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "oracle_recruiting",
                "board_token": "tenant.fa.oraclecloud.com|en|CX_1",
                "base_url": (
                    "https://other.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
                ),
                "terms_url": "https://example.org/terms",
                "policy_approved_at": "2026-08-07T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    with pytest.raises(ValueError, match="approved oracle_recruiting public host"):
        import_source_registry(repository, registry)


def test_bulk_source_import_accepts_exact_reviewed_adp_workforce_now_source(tmp_path):
    repository = JobRepository(tmp_path / "adp.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    client_id = "be841b2c-7fe9-4b77-bda5-63263ad0f62b"
    board_token = f"{client_id}|19000101_000001|en_US"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "adp_workforce_now",
                "board_token": board_token,
                "base_url": (
                    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
                    f"recruitment.html?cid={client_id}&ccId=19000101_000001&lang=en_US"
                ),
                "terms_url": "https://example.org/terms",
                "policy_approved_at": "2026-08-12T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    assert import_source_registry(repository, registry) == 1
    source = repository.due_career_sources()[0]
    assert source["kind"] == "adp_workforce_now"
    assert source["board_token"] == board_token


def test_bulk_source_import_rejects_adp_source_key_url_mismatch(tmp_path):
    repository = JobRepository(tmp_path / "adp-mismatch.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "sources.csv"
    client_id = "be841b2c-7fe9-4b77-bda5-63263ad0f62b"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "kind": "adp_workforce_now",
                "board_token": f"{client_id}|19000101_000001|en_US",
                "base_url": (
                    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
                    f"recruitment.html?cid={client_id}&ccId=19000101_999999&lang=en_US"
                ),
                "terms_url": "https://example.org/terms",
                "policy_approved_at": "2026-08-12T12:00:00Z",
                "owner_contact": "owner@example.org",
            }
        ],
    )

    with pytest.raises(ValueError, match="approved adp_workforce_now public host"):
        import_source_registry(repository, registry)
