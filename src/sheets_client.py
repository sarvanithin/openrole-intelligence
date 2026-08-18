"""
Google Sheets API client for reading company URLs and writing job listings.
Includes retry logic with exponential backoff for reliability.
"""
import json
import os
import time
import ssl
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (
    COMPANIES_SHEET_ID,
    JOBS_SHEET_ID,
    COMPANIES_SHEET_NAME,
    JOBS_SHEET_NAME,
)


def retry_with_backoff(
    func: Callable,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
):
    """
    Execute a function with exponential backoff retry logic.
    
    Handles common transient errors:
    - SSL/TLS errors (EOF, connection reset)
    - HTTP 5xx errors
    - Rate limiting (429)
    - Connection errors
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return func()
        except ssl.SSLError as e:
            last_exception = e
            print(f"  SSL error on attempt {attempt + 1}/{max_retries}: {e}")
        except HttpError as e:
            last_exception = e
            status = e.resp.status if hasattr(e, 'resp') else 0
            # Retry on 5xx errors and rate limiting
            if status >= 500 or status == 429:
                print(f"  HTTP {status} error on attempt {attempt + 1}/{max_retries}")
            else:
                raise  # Don't retry client errors
        except (ConnectionError, ConnectionResetError, BrokenPipeError) as e:
            last_exception = e
            print(f"  Connection error on attempt {attempt + 1}/{max_retries}: {e}")
        except Exception as e:
            # Check for SSL-related errors in the message
            if 'EOF' in str(e) or 'ssl' in str(e).lower() or 'connection' in str(e).lower():
                last_exception = e
                print(f"  Transient error on attempt {attempt + 1}/{max_retries}: {e}")
            else:
                raise  # Don't retry unknown errors
        
        if attempt < max_retries - 1:
            delay = min(base_delay * (2 ** attempt), max_delay)
            print(f"  Retrying in {delay:.1f}s...")
            time.sleep(delay)
    
    raise last_exception


class SheetsClient:
    """Client for interacting with Google Sheets API with retry logic."""

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    BATCH_SIZE = 100  # Max rows per API call

    def __init__(self, credentials_path: Optional[str] = None):
        """
        Initialize the Sheets client.

        Args:
            credentials_path: Path to credentials.json file.
                             If None, uses GOOGLE_CREDENTIALS env var.
        """
        self.credentials = self._load_credentials(credentials_path)
        self.service = build("sheets", "v4", credentials=self.credentials)
        self.sheets = self.service.spreadsheets()

    def _load_credentials(self, credentials_path: Optional[str] = None):
        """Load Google credentials from file or environment variable."""
        if credentials_path and os.path.exists(credentials_path):
            return service_account.Credentials.from_service_account_file(
                credentials_path, scopes=self.SCOPES
            )

        # Try environment variable (for GitHub Actions)
        creds_json = os.getenv("GOOGLE_CREDENTIALS")
        if creds_json:
            creds_info = json.loads(creds_json)
            return service_account.Credentials.from_service_account_info(
                creds_info, scopes=self.SCOPES
            )

        raise ValueError(
            "No credentials found. Provide credentials_path or set GOOGLE_CREDENTIALS env var."
        )

    def get_companies(self, sheet_id: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Fetch company URLs from the companies sheet.

        Returns:
            List of dicts with keys: company_name, career_url, platform_type, status
        """
        sheet_id = sheet_id or COMPANIES_SHEET_ID
        if not sheet_id:
            raise ValueError("COMPANIES_SHEET_ID not configured")

        def _fetch():
            result = self.sheets.values().get(
                spreadsheetId=sheet_id,
                range=f"{COMPANIES_SHEET_NAME}!A:E",
            ).execute()
            return result

        try:
            result = retry_with_backoff(_fetch)
            values = result.get("values", [])
            if not values:
                return []

            # Skip header row
            companies = []
            for row in values[1:]:
                if len(row) >= 2:
                    companies.append({
                        "company_name": row[0] if len(row) > 0 else "",
                        "career_url": row[1] if len(row) > 1 else "",
                        "platform_type": row[2] if len(row) > 2 else "",
                        "last_scraped": row[3] if len(row) > 3 else "",
                        "status": row[4] if len(row) > 4 else "active",
                    })

            return companies

        except Exception as e:
            print(f"Error fetching companies: {e}")
            raise

    def get_existing_job_ids(self, sheet_id: Optional[str] = None) -> set:
        """
        Fetch all existing job IDs from the jobs sheet for deduplication.

        Returns:
            Set of job IDs already in the sheet.
        """
        sheet_id = sheet_id or JOBS_SHEET_ID
        if not sheet_id:
            raise ValueError("JOBS_SHEET_ID not configured")

        def _fetch():
            result = self.sheets.values().get(
                spreadsheetId=sheet_id,
                range=f"{JOBS_SHEET_NAME}!A:A",
            ).execute()
            return result

        try:
            result = retry_with_backoff(_fetch)
            values = result.get("values", [])
            # Skip header, extract job IDs
            return {row[0] for row in values[1:] if row}

        except Exception as e:
            print(f"Error fetching existing jobs: {e}")
            raise

    def get_scraped_companies(self, sheet_id: Optional[str] = None) -> set:
        """
        Get the set of company names that already have jobs in the sheet.
        Used to determine if a company is being scraped for the first time.

        Returns:
            Set of company names that have at least one job in the sheet.
        """
        sheet_id = sheet_id or JOBS_SHEET_ID
        if not sheet_id:
            return set()

        def _fetch():
            result = self.sheets.values().get(
                spreadsheetId=sheet_id,
                range=f"{JOBS_SHEET_NAME}!C:C",  # Company name column
            ).execute()
            return result

        try:
            result = retry_with_backoff(_fetch)
            values = result.get("values", [])
            # Skip header, extract company names, ignore separator rows
            companies = set()
            for row in values[1:]:
                if row and row[0] and not row[0].startswith("==="):
                    companies.add(row[0])
            return companies

        except Exception as e:
            print(f"Warning: Could not fetch scraped companies: {e}")
            return set()


    def append_jobs(
        self, 
        jobs: List[Dict[str, Any]], 
        sheet_id: Optional[str] = None,
        is_initial_load: bool = False,
        company_name: str = "",
    ) -> int:
        """
        Append new jobs to the jobs sheet in batches with retry logic.
        Adds a timestamp separator row before the jobs for easy identification.

        Args:
            jobs: List of job dicts with keys matching sheet columns.
            sheet_id: Optional sheet ID override.
            is_initial_load: True if this is the first scrape for a company.
            company_name: Company name for initial load separator.

        Returns:
            Number of jobs appended.
        """
        sheet_id = sheet_id or JOBS_SHEET_ID
        if not sheet_id:
            raise ValueError("JOBS_SHEET_ID not configured")

        if not jobs:
            return 0

        now = datetime.utcnow()
        now_str = now.isoformat()
        
        # Calculate quarter time range (6-hour blocks)
        hour = now.hour
        quarter_start = (hour // 6) * 6
        quarter_end = quarter_start + 6
        quarter_name = f"Q{(hour // 6) + 1}"  # Q1, Q2, Q3, Q4
        
        # Create timestamp separator row
        # Using '---' prefix instead of '===' to avoid Google Sheets formula error
        # Different text for initial load vs new jobs
        if is_initial_load:
            separator_text = f"--- INITIAL LOAD: {company_name} | {len(jobs)} existing roles | {now.strftime('%b %d, %Y')} {quarter_start:02d}:00-{quarter_end:02d}:00 UTC ({quarter_name}) ---"
            status_marker = "initial_load"
        else:
            separator_text = f"--- NEW JOBS {quarter_name}: {now.strftime('%b %d, %Y')} {quarter_start:02d}:00-{quarter_end:02d}:00 UTC | {len(jobs)} new postings ---"
            status_marker = "separator"
        
        separator_row = [[
            separator_text,  # job_id column - visible marker
            "",  # job_title
            "",  # company_name
            "",  # job_url
            "",  # career_url
            "",  # location
            "",  # posted_date
            now_str,  # date_added
            "",  # last_seen
            "",  # keywords
            status_marker,  # status - marks this as a separator row
        ]]

        # Convert jobs to rows
        rows = []
        for job in jobs:
            posted_date = job.get("posted_date", "")
            rows.append([
                job.get("job_id", ""),
                job.get("job_title", ""),
                job.get("company_name", ""),
                job.get("job_url", ""),
                job.get("company_career_url", ""),
                job.get("location", ""),
                posted_date if posted_date else "Not Available",  # posted_date
                now_str,  # date_added
                now_str,  # last_seen
                ", ".join(job.get("keywords_matched", [])),
                "active",
            ])

        # First, append the separator row
        def _append_separator():
            result = self.sheets.values().append(
                spreadsheetId=sheet_id,
                range=f"{JOBS_SHEET_NAME}!A:K",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": separator_row},
            ).execute()
            return result
        
        try:
            retry_with_backoff(_append_separator)
            print(f"    Added timestamp separator: {separator_text}")
        except Exception as e:
            print(f"    Warning: Could not add separator row: {e}")

        total_appended = 0
        
        # Process jobs in batches to avoid timeouts
        for i in range(0, len(rows), self.BATCH_SIZE):
            batch = rows[i:i + self.BATCH_SIZE]
            
            def _append_batch():
                result = self.sheets.values().append(
                    spreadsheetId=sheet_id,
                    range=f"{JOBS_SHEET_NAME}!A:K",
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": batch},
                ).execute()
                return result

            try:
                result = retry_with_backoff(_append_batch)
                updates = result.get("updates", {})
                batch_count = updates.get("updatedRows", 0)
                total_appended += batch_count
                print(f"    Wrote batch {i // self.BATCH_SIZE + 1}: {batch_count} jobs")
            except Exception as e:
                print(f"    Error writing batch {i // self.BATCH_SIZE + 1}: {e}")
                # Continue with next batch even if this one fails
                continue

        return total_appended

    def update_company_status(
        self,
        company_name: str,
        status: str,
        sheet_id: Optional[str] = None,
    ):
        """Update the status and last_scraped time for a company."""
        sheet_id = sheet_id or COMPANIES_SHEET_ID
        if not sheet_id:
            return

        def _update():
            # First, find the row for this company
            result = self.sheets.values().get(
                spreadsheetId=sheet_id,
                range=f"{COMPANIES_SHEET_NAME}!A:A",
            ).execute()

            values = result.get("values", [])
            row_index = None
            for i, row in enumerate(values):
                if row and row[0] == company_name:
                    row_index = i + 1  # 1-indexed
                    break

            if row_index:
                now = datetime.utcnow().isoformat()
                self.sheets.values().update(
                    spreadsheetId=sheet_id,
                    range=f"{COMPANIES_SHEET_NAME}!D{row_index}:E{row_index}",
                    valueInputOption="RAW",
                    body={"values": [[now, status]]},
                ).execute()

        try:
            retry_with_backoff(_update)
        except Exception as e:
            print(f"Error updating company status: {e}")

    def update_job_last_seen(
        self, job_ids: List[str], sheet_id: Optional[str] = None
    ):
        """Update the last_seen timestamp for existing jobs."""
        sheet_id = sheet_id or JOBS_SHEET_ID
        if not sheet_id or not job_ids:
            return

        def _update():
            # Get all job IDs with their row numbers
            result = self.sheets.values().get(
                spreadsheetId=sheet_id,
                range=f"{JOBS_SHEET_NAME}!A:A",
            ).execute()

            values = result.get("values", [])
            now = datetime.utcnow().isoformat()

            # Build batch update
            requests = []
            for i, row in enumerate(values[1:], start=2):  # Skip header, 1-indexed
                if row and row[0] in job_ids:
                    requests.append({
                        # Column I is last_seen; column H is date_added.
                        "range": f"{JOBS_SHEET_NAME}!I{i}",
                        "values": [[now]],
                    })

            if requests:
                self.sheets.values().batchUpdate(
                    spreadsheetId=sheet_id,
                    body={
                        "valueInputOption": "RAW",
                        "data": requests,
                    },
                ).execute()

        try:
            retry_with_backoff(_update)
        except Exception as e:
            print(f"Error updating job last_seen: {e}")
