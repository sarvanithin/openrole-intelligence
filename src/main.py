"""
Fortune Job Scraper - Main Entry Point

This script orchestrates the job scraping process:
1. Reads company URLs from Google Sheets
2. Scrapes each company's career page
3. Filters jobs by keywords
4. Deduplicates against existing jobs
5. Updates Google Sheets with new jobs
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime
from typing import List, Dict, Any

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

from config import (
    COMPANIES_PER_BATCH,
    BATCH_DELAY_SECONDS,
    STATUS_ACTIVE,
    STATUS_ERROR,
)
from sheets_client import SheetsClient
from scraper.dispatcher import ScraperDispatcher
from scraper.base_scraper import Job
from utils.deduplication import filter_new_jobs, find_existing_jobs

# Load environment variables
load_dotenv()


async def scrape_company(company: Dict[str, str]) -> List[Job]:
    """
    Scrape jobs from a single company's career page.

    Args:
        company: Dict with company_name, career_url, platform_type.

    Returns:
        List of Job objects.
    """
    company_name = company["company_name"]
    career_url = company["career_url"]
    platform_type = company.get("platform_type", "")

    print(f"\n{'='*60}")
    print(f"Scraping: {company_name}")
    print(f"URL: {career_url}")
    print(f"{'='*60}")

    try:
        scraper = ScraperDispatcher.get_scraper(
            company_name=company_name,
            career_url=career_url,
            platform_hint=platform_type if platform_type else None,
        )
        jobs = await scraper.scrape()
        return jobs

    except Exception as e:
        print(f"Error scraping {company_name}: {e}")
        return []


async def process_companies(
    companies: List[Dict[str, str]],
    sheets_client: SheetsClient,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Process multiple companies and update Google Sheets.

    Args:
        companies: List of company dicts.
        sheets_client: Google Sheets client.
        dry_run: If True, don't write to sheets.

    Returns:
        Summary statistics.
    """
    stats = {
        "companies_processed": 0,
        "companies_with_errors": 0,
        "total_jobs_found": 0,
        "new_jobs_added": 0,
        "existing_jobs_updated": 0,
        "initial_load_jobs": 0,
    }

    # Get existing job IDs for deduplication
    if not dry_run:
        print("\nFetching existing jobs for deduplication...")
        existing_ids = sheets_client.get_existing_job_ids()
        print(f"Found {len(existing_ids)} existing jobs in database")
        
        # Get companies that have already been scraped (have jobs in sheet)
        scraped_companies = sheets_client.get_scraped_companies()
        print(f"Found {len(scraped_companies)} companies with existing jobs")
    else:
        existing_ids = set()
        scraped_companies = set()

    # Process companies in batches
    for i in range(0, len(companies), COMPANIES_PER_BATCH):
        batch = companies[i:i + COMPANIES_PER_BATCH]
        batch_num = (i // COMPANIES_PER_BATCH) + 1
        total_batches = (len(companies) + COMPANIES_PER_BATCH - 1) // COMPANIES_PER_BATCH

        print(f"\n{'#'*60}")
        print(f"Processing batch {batch_num}/{total_batches}")
        print(f"{'#'*60}")

        for company in batch:
            company_name = company["company_name"]
            
            try:
                jobs = await scrape_company(company)
                stats["companies_processed"] += 1

                if jobs:
                    job_dicts = [job.to_dict() for job in jobs]
                    stats["total_jobs_found"] += len(job_dicts)

                    # Deduplicate
                    new_jobs = filter_new_jobs(job_dicts, existing_ids)
                    existing_job_ids = find_existing_jobs(job_dicts, existing_ids)

                    # Check if this is the first time scraping this company
                    is_initial_load = company_name not in scraped_companies

                    if is_initial_load:
                        stats["initial_load_jobs"] += len(new_jobs)
                        print(f"  [INITIAL LOAD] First time scraping {company_name}")
                    else:
                        stats["new_jobs_added"] += len(new_jobs)
                    
                    stats["existing_jobs_updated"] += len(existing_job_ids)

                    if not dry_run:
                        # Add new jobs
                        if new_jobs:
                            sheets_client.append_jobs(
                                new_jobs,
                                is_initial_load=is_initial_load,
                                company_name=company_name,
                            )
                            # Add to existing_ids to prevent duplicates within this run
                            for job in new_jobs:
                                existing_ids.add(job["job_id"])
                            # Mark company as scraped
                            scraped_companies.add(company_name)

                        # Update last_seen for existing jobs
                        if existing_job_ids:
                            sheets_client.update_job_last_seen(existing_job_ids)

                        # Update company status
                        sheets_client.update_company_status(
                            company_name,
                            STATUS_ACTIVE,
                        )

                    print(f"  → {len(new_jobs)} new jobs, {len(existing_job_ids)} existing")

            except Exception as e:
                print(f"Error processing {company['company_name']}: {e}")
                stats["companies_with_errors"] += 1

                if not dry_run:
                    sheets_client.update_company_status(
                        company["company_name"],
                        STATUS_ERROR,
                    )

        # Delay between batches
        if i + COMPANIES_PER_BATCH < len(companies):
            print(f"\nWaiting {BATCH_DELAY_SECONDS}s before next batch...")
            await asyncio.sleep(BATCH_DELAY_SECONDS)

    return stats


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Fortune Job Scraper")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run in test mode (process only first 3 companies)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write to Google Sheets",
    )
    parser.add_argument(
        "--company",
        type=str,
        help="Scrape only a specific company by name",
    )
    parser.add_argument(
        "--local-csv",
        type=str,
        help="Use local CSV file instead of Google Sheets",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Fortune Job Scraper")
    print(f"Started at: {datetime.utcnow().isoformat()}")
    print("=" * 60)

    # Initialize sheets client
    if args.local_csv:
        print(f"\nUsing local CSV: {args.local_csv}")
        companies = load_companies_from_csv(args.local_csv)
        sheets_client = None
        args.dry_run = True  # Force dry run with local CSV
    else:
        try:
            sheets_client = SheetsClient(credentials_path="credentials.json")
            print("\nConnected to Google Sheets")
            companies = sheets_client.get_companies()
        except Exception as e:
            print(f"\nError connecting to Google Sheets: {e}")
            print("Use --local-csv to test with a local CSV file")
            return

    # Filter companies
    if args.company:
        companies = [c for c in companies if c["company_name"].lower() == args.company.lower()]
        if not companies:
            print(f"Company '{args.company}' not found")
            return

    # Test mode: only process first 3
    if args.test and len(companies) > 3:
        companies = companies[:3]
        print(f"\n[TEST MODE] Processing only {len(companies)} companies")

    print(f"\nFound {len(companies)} companies to process")

    # Process companies
    stats = await process_companies(
        companies=companies,
        sheets_client=sheets_client,
        dry_run=args.dry_run,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Companies processed: {stats['companies_processed']}")
    print(f"Companies with errors: {stats['companies_with_errors']}")
    print(f"Total jobs found: {stats['total_jobs_found']}")
    print(f"Initial load jobs (new companies): {stats.get('initial_load_jobs', 0)}")
    print(f"New job postings (existing companies): {stats['new_jobs_added']}")
    print(f"Existing jobs updated: {stats['existing_jobs_updated']}")
    print(f"Completed at: {datetime.utcnow().isoformat()}")
    print("=" * 60)


def load_companies_from_csv(csv_path: str) -> List[Dict[str, str]]:
    """Load companies from a local CSV file."""
    import csv

    companies = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            companies.append({
                "company_name": row.get("Company Name", ""),
                "career_url": row.get("Career Search URL", ""),
                "platform_type": row.get("Platform Type", ""),
                "status": "active",
            })
    return companies


if __name__ == "__main__":
    asyncio.run(main())
