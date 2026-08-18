"""
Tests for the job scraper components.
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.job_filter import matches_any_keyword, is_relevant_job
from utils.deduplication import generate_job_hash, filter_new_jobs


class TestKeywordFilter:
    """Tests for keyword filtering."""

    def test_matches_exact_keyword(self):
        """Should match exact keywords."""
        result = matches_any_keyword("Data Analyst")
        assert "data" in result
        assert "analyst" in result

    def test_matches_case_insensitive(self):
        """Should be case insensitive."""
        result = matches_any_keyword("DATA SCIENTIST")
        assert "data" in result or "data scientist" in result

    def test_no_match_irrelevant(self):
        """Should not match irrelevant titles."""
        result = matches_any_keyword("Marketing Manager")
        assert len(result) == 0

    def test_matches_ml_keyword(self):
        """Should match ML/machine learning keywords."""
        result = matches_any_keyword("Machine Learning Engineer")
        assert "machine learning" in result

    def test_is_relevant_data_engineer(self):
        """Data engineer should be relevant."""
        assert is_relevant_job("Senior Data Engineer")

    def test_is_not_relevant_sales(self):
        """Sales role should not be relevant."""
        assert not is_relevant_job("Sales Representative")


class TestDeduplication:
    """Tests for deduplication utilities."""

    def test_generate_hash_consistent(self):
        """Same inputs should produce same hash."""
        hash1 = generate_job_hash("https://example.com/job/123", "Company A")
        hash2 = generate_job_hash("https://example.com/job/123", "Company A")
        assert hash1 == hash2

    def test_generate_hash_different(self):
        """Different inputs should produce different hashes."""
        hash1 = generate_job_hash("https://example.com/job/123", "Company A")
        hash2 = generate_job_hash("https://example.com/job/456", "Company A")
        assert hash1 != hash2

    def test_filter_new_jobs(self):
        """Should filter out existing jobs."""
        jobs = [
            {"job_id": "abc123", "title": "Job 1"},
            {"job_id": "def456", "title": "Job 2"},
            {"job_id": "ghi789", "title": "Job 3"},
        ]
        existing = {"abc123", "ghi789"}

        new_jobs = filter_new_jobs(jobs, existing)

        assert len(new_jobs) == 1
        assert new_jobs[0]["job_id"] == "def456"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
