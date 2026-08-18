"""
Deduplication utilities for job listings.
"""
import hashlib
from typing import List, Set, Dict, Any


def generate_job_hash(job_url: str, company_name: str) -> str:
    """
    Generate a unique hash for a job based on URL and company.

    Args:
        job_url: The job posting URL.
        company_name: The company name.

    Returns:
        16-character hash string.
    """
    content = f"{company_name}:{job_url}".lower()
    return hashlib.md5(content.encode()).hexdigest()[:16]


def filter_new_jobs(
    jobs: List[Dict[str, Any]],
    existing_ids: Set[str],
) -> List[Dict[str, Any]]:
    """
    Filter out jobs that already exist in the database.

    Args:
        jobs: List of job dictionaries to filter.
        existing_ids: Set of existing job IDs.

    Returns:
        List of new jobs not in existing_ids.
    """
    return [job for job in jobs if job.get("job_id") not in existing_ids]


def find_existing_jobs(
    jobs: List[Dict[str, Any]],
    existing_ids: Set[str],
) -> List[str]:
    """
    Find job IDs that already exist (for updating last_seen).

    Args:
        jobs: List of job dictionaries to check.
        existing_ids: Set of existing job IDs.

    Returns:
        List of job IDs that exist in both.
    """
    return [job["job_id"] for job in jobs if job.get("job_id") in existing_ids]
