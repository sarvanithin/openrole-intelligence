"""Small synthetic demo dataset; it contains no licensed Fortune data."""

from __future__ import annotations

from datetime import UTC, datetime

from fortune_intel.domain import JobRecord
from fortune_intel.services.sponsorship import EmployerHistory, assess_sponsorship
from fortune_intel.storage import JobRepository


def seed_demo(repository: JobRepository) -> int:
    year = datetime.now(UTC).year
    examples = [
        (
            "Northstar Systems",
            "greenhouse",
            0,
            91,
            JobRecord(
                company_name="Northstar Systems",
                title="Senior Data Engineer",
                url="https://careers.example.com/northstar/1024",
                source="greenhouse",
                external_job_id="1024",
                location="New York, NY",
                description="Visa sponsorship is available for this position for qualified candidates.",
                source_opened_at=f"{year}-08-01",
            ),
        ),
        (
            "Harbor Analytics",
            "lever",
            0,
            64,
            JobRecord(
                company_name="Harbor Analytics",
                title="Machine Learning Engineer",
                url="https://careers.example.com/harbor/ml-7",
                source="lever",
                external_job_id="ml-7",
                location="Boston, MA · Hybrid",
                description="Build reliable model training and inference platforms.",
                source_opened_at=f"{year}-07-29",
            ),
        ),
        (
            "Juniper Works",
            "smartrecruiters",
            0,
            8,
            JobRecord(
                company_name="Juniper Works",
                title="Business Intelligence Analyst",
                url="https://careers.example.com/juniper/bi-22",
                source="smartrecruiters",
                external_job_id="bi-22",
                location="Remote — United States",
                description="Applicants must be able to work without current or future visa sponsorship.",
                source_opened_at=f"{year}-07-27",
            ),
        ),
        (
            "Cinder Labs",
            "ashby",
            0,
            0,
            JobRecord(
                company_name="Cinder Labs",
                title="Product Data Scientist",
                url="https://careers.example.com/cinder/ds-3",
                source="ashby",
                external_job_id="ds-3",
                location="San Francisco, CA",
                description="Partner with product and engineering to design experiments.",
                source_opened_at=f"{year}-07-24",
            ),
        ),
    ]
    for name, ats_type, approvals, workers, job in examples:
        company_id = repository.upsert_company(
            name,
            ats_type=ats_type,
            career_url=f"https://careers.example.com/{name.split()[0].lower()}",
            collection_name="Synthetic demo",
            collection_year=year,
            is_synthetic=True,
        )
        if approvals or workers:
            repository.record_sponsorship_fact(
                company_id,
                source="synthetic_demo",
                fiscal_year=year - 1,
                initial_approvals=approvals,
                lca_worker_positions=workers,
                entity_match_confidence=1.0,
            )
        history = EmployerHistory(
            uscis_initial_approvals=approvals,
            lca_worker_positions=workers,
            latest_fiscal_year=year - 1 if approvals or workers else None,
            entity_match_confidence=1.0 if approvals or workers else 0.0,
        )
        repository.upsert_job(company_id, job, assess_sponsorship(job.description, history))
    return len(examples)
