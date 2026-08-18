"""
Configuration settings for the Fortune Job Scraper.
"""
import os
from typing import List

# Keywords to filter jobs - case insensitive matching
KEYWORDS: List[str] = [
    "data",
    "analyst",
    "analytics",
    "machine learning",
    "ml",
    "data science",
    "data scientist",
    "data engineer",
    "data analyst",
    "business intelligence",
    "bi",
    "ai",
    "artificial intelligence",
]

# Scraping settings
SCRAPE_DELAY_SECONDS: float = 2.0  # Delay between page loads (be respectful)
PAGE_LOAD_TIMEOUT_MS: int = 45000  # 45 seconds for page to load (increased for slow SPAs)
MAX_PAGES_PER_COMPANY: int = 50  # Maximum pages to scrape per company
MAX_RETRIES: int = 3  # Retries on failure

# Batch settings (for large-scale scraping)
COMPANIES_PER_BATCH: int = 10  # Process companies in batches
BATCH_DELAY_SECONDS: float = 5.0  # Delay between batches

# Google Sheets settings
COMPANIES_SHEET_ID: str = os.getenv("COMPANIES_SHEET_ID", "")
JOBS_SHEET_ID: str = os.getenv("JOBS_SHEET_ID", "")

# Sheet names (tabs within the spreadsheet)
COMPANIES_SHEET_NAME: str = "Sheet1"
JOBS_SHEET_NAME: str = "Sheet1"

# Job status values
STATUS_ACTIVE: str = "active"
STATUS_REMOVED: str = "removed"
STATUS_ERROR: str = "error"

# =============================================================================
# Platform Detection Patterns
# =============================================================================
# Each list contains URL substrings that identify a specific ATS platform

# Workday - Most common among Fortune 500
WORKDAY_PATTERNS: List[str] = [
    "myworkdayjobs.com",
    "wd1.myworkdayjobs",
    "wd3.myworkdayjobs",
    "wd5.myworkdayjobs",
    "workday.com/en-us/careers",
]

# Eightfold AI - Modern AI-powered ATS
EIGHTFOLD_PATTERNS: List[str] = [
    "eightfold.ai",
    ".eightfold.ai/careers",
]

# Greenhouse - Popular with tech companies
GREENHOUSE_PATTERNS: List[str] = [
    "boards.greenhouse.io",
    "greenhouse.io/embed",
    "grnh.se",  # Greenhouse short URLs
]

# Lever - Common in tech/startups
LEVER_PATTERNS: List[str] = [
    "jobs.lever.co",
    "lever.co/",
]

# iCIMS - Enterprise ATS
ICIMS_PATTERNS: List[str] = [
    ".icims.com",
    "careers-icims.com",
    "icims.com/jobs",
]

# Oracle Taleo - Legacy enterprise ATS
TALEO_PATTERNS: List[str] = [
    ".taleo.net",
    "taleo.com",
    "careersection",
]

# SmartRecruiters - Modern enterprise ATS
SMARTRECRUITERS_PATTERNS: List[str] = [
    "jobs.smartrecruiters.com",
    "smartrecruiters.com/job",
    "careers.smartrecruiters.com",
]

# Jobvite - Mid-market ATS
JOBVITE_PATTERNS: List[str] = [
    "jobs.jobvite.com",
    "jobvite.com",
]

# BambooHR - HR software with ATS
BAMBOOHR_PATTERNS: List[str] = [
    "bamboohr.com/careers",
    ".bamboohr.com/jobs",
]

# Phenom - Talent experience platform
PHENOM_PATTERNS: List[str] = [
    "phenom.com",
    "jobs.phenom.com",
]

# SuccessFactors (SAP) - Enterprise ATS
SUCCESSFACTORS_PATTERNS: List[str] = [
    "successfactors.com",
    "successfactors.eu",
    "jobs.sap.com",
]

# All patterns for easy iteration
ALL_PLATFORM_PATTERNS = {
    "workday": WORKDAY_PATTERNS,
    "eightfold": EIGHTFOLD_PATTERNS,
    "greenhouse": GREENHOUSE_PATTERNS,
    "lever": LEVER_PATTERNS,
    "icims": ICIMS_PATTERNS,
    "taleo": TALEO_PATTERNS,
    "smartrecruiters": SMARTRECRUITERS_PATTERNS,
    "jobvite": JOBVITE_PATTERNS,
    "bamboohr": BAMBOOHR_PATTERNS,
    "phenom": PHENOM_PATTERNS,
    "successfactors": SUCCESSFACTORS_PATTERNS,
}

# Plaid - Custom React/Next.js career site
PLAID_PATTERNS: List[str] = [
    "plaid.com/careers",
]
