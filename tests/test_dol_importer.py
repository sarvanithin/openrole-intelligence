import csv

from fortune_intel.importers.dol_h1b import import_dol_lca
from fortune_intel.storage import JobRepository


def test_dol_import_filters_program_status_and_invalid_counts(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    company_id = repository.upsert_company("Acme, Inc.")
    disclosure = tmp_path / "LCA_Disclosure_Data_FY2026_Q4.csv"
    rows = [
        ["A-1", "ACME INC", "CERTIFIED", "H-1B", "2"],
        ["A-2", "Acme, Inc.", "CERTIFIED", "H-1B", "3"],
        ["A-3", "ACME INC", "CERTIFIED", "H-1B1 Chile", "50"],
        ["A-4", "ACME INC", "CERTIFIED-WITHDRAWN", "H-1B", "30"],
        ["A-5", "ACME INC", "CERTIFIED", "H-1B", "bad"],
        ["A-5", "ACME INC", "CERTIFIED", "H-1B", "999"],
    ]
    with disclosure.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "CASE_NUMBER",
                "EMPLOYER_NAME",
                "CASE_STATUS",
                "VISA_CLASS",
                "TOTAL_WORKER_POSITIONS",
            ]
        )
        writer.writerows(rows)

    result = import_dol_lca(repository, disclosure, fiscal_year=2026)
    history = repository.get_employer_history(company_id)
    assert history.lca_worker_positions == 5
    assert history.entity_match_confidence == 0.85
    assert result["rows_skipped_other_visa_class"] == 1
    assert result["rows_skipped_certified_withdrawn"] == 1
    assert result["rows_skipped_invalid_worker_positions"] == 1
    assert result["rows_skipped_duplicate_or_missing_case"] == 1
    employers = repository.list_h1b_employers()
    assert employers == [
        {
            "employer_name": "ACME INC",
            "fiscal_year": 2026,
            "lca_worker_positions": 5,
            "source": "dol_lca",
            "source_url": "https://www.dol.gov/agencies/eta/foreign-labor/performance",
            "imported_at": employers[0]["imported_at"],
        }
    ]
    assert repository.h1b_overview()["employers"] == 1


def test_dol_import_rejects_fiscal_year_mismatch(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    path = tmp_path / "LCA_Disclosure_Data_FY2025.csv"
    path.write_text("CASE_NUMBER,EMPLOYER_NAME,CASE_STATUS,VISA_CLASS,TOTAL_WORKER_POSITIONS\n")
    try:
        import_dol_lca(repository, path, fiscal_year=2026)
    except ValueError as error:
        assert "conflicts" in str(error)
    else:
        raise AssertionError("conflicting fiscal year should be rejected")
