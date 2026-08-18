"""
Greenhouse career site scraper.
Uses the Greenhouse Job Board API for reliable JSON data extraction.

Greenhouse URL patterns:
- boards.greenhouse.io/{company}
- boards.greenhouse.io/{company}/jobs/{id}
- API: boards-api.greenhouse.io/v1/boards/{company}/jobs
"""
import asyncio
import re
from typing import List, Optional
from urllib.parse import urlparse, urljoin

import requests
from playwright.async_api import async_playwright, Page, Browser

from config import PAGE_LOAD_TIMEOUT_MS, MAX_PAGES_PER_COMPANY
from scraper.base_scraper import BaseScraper, Job


class GreenhouseScraper(BaseScraper):
    """
    Scraper for Greenhouse career sites.
    Prioritizes API calls for reliability, falls back to browser scraping.
    """

    API_BASE = "https://boards-api.greenhouse.io/v1/boards"
    
    # Greenhouse-specific selectors for fallback
    JOB_SELECTORS = [
        'a[data-mapped="true"]',
        '.opening a',
        '.opening-title',
        'a[href*="/jobs/"]',
        'section.level-0 a',
        '[class*="opening"] a',
        'tr.job-post a',
    ]

    def __init__(self, company_name: str, career_url: str):
        super().__init__(company_name, career_url)
        self.board_token = self._extract_board_token(career_url)

    def _extract_board_token(self, url: str) -> Optional[str]:
        """Extract the Greenhouse board token from URL."""
        patterns = [
            r'boards\.greenhouse\.io/([^/\?]+)',
            r'greenhouse\.io/embed/job_board\?for=([^&]+)',
            r'grnh\.se/([^/\?]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    async def scrape(self) -> List[Job]:
        """Scrape jobs using API first, then fallback to browser."""
        # Try API first (most reliable)
        if self.board_token:
            jobs = await self._scrape_via_api()
            if jobs:
                return jobs
            print(f"  [Greenhouse] API failed, falling back to browser for {self.company_name}")

        # Fallback to browser scraping
        return await self._scrape_via_browser()

    async def _scrape_via_api(self) -> List[Job]:
        """Scrape using Greenhouse Job Board API."""
        all_jobs: List[Job] = []
        
        try:
            url = f"{self.API_BASE}/{self.board_token}/jobs"
            print(f"  [Greenhouse] Fetching from API: {url}")
            
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                print(f"  [Greenhouse] API returned {response.status_code}")
                return []
            
            data = response.json()
            jobs_data = data.get("jobs", [])
            
            print(f"  [Greenhouse] API returned {len(jobs_data)} jobs")
            
            for job_data in jobs_data:
                title = job_data.get("title", "")
                
                # Check if matches keywords
                matched_keywords = self.matches_keywords(title)
                if not matched_keywords:
                    continue
                
                job_id = f"GH_{job_data.get('id', '')}"
                job_url = job_data.get("absolute_url", "")
                
                # Extract location
                location_data = job_data.get("location", {})
                location = location_data.get("name", "") if isinstance(location_data, dict) else str(location_data)
                
                # Extract posted date from updated_at
                updated_at = job_data.get("updated_at", "")
                posted_date = ""
                if updated_at:
                    # Format: 2024-01-15T12:00:00-00:00
                    try:
                        posted_date = updated_at.split("T")[0]  # Get just the date part
                    except:
                        posted_date = ""
                
                all_jobs.append(Job(
                    job_id=job_id,
                    job_title=title,
                    job_url=job_url,
                    company_name=self.company_name,
                    company_career_url=self.career_url,
                    location=location,
                    posted_date=posted_date,
                    keywords_matched=matched_keywords,
                ))
            
            print(f"  [Greenhouse] Found {len(all_jobs)} matching jobs via API")
            return all_jobs
            
        except Exception as e:
            print(f"  [Greenhouse] API error: {e}")
            return []

    async def _scrape_via_browser(self) -> List[Job]:
        """Fallback browser scraping for Greenhouse."""
        all_jobs: List[Job] = []
        seen_urls: set = set()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            
            page = await context.new_page()

            try:
                await page.goto(self.career_url, wait_until='networkidle', timeout=PAGE_LOAD_TIMEOUT_MS)
                await asyncio.sleep(3)

                # Try each selector
                for selector in self.JOB_SELECTORS:
                    elements = await page.query_selector_all(selector)
                    if not elements:
                        continue
                    
                    for element in elements:
                        href = await element.get_attribute('href')
                        if not href or href in seen_urls:
                            continue
                        
                        job_url = self.normalize_url(href)
                        if '/jobs/' not in job_url.lower():
                            continue
                        
                        seen_urls.add(job_url)
                        
                        title = await element.text_content()
                        title = self.clean_text(title) if title else ""
                        
                        if not title or len(title) < 3:
                            continue
                        
                        matched_keywords = self.matches_keywords(title)
                        if matched_keywords:
                            job_id = self._extract_job_id(job_url)
                            all_jobs.append(Job(
                                job_id=job_id,
                                job_title=title,
                                job_url=job_url,
                                company_name=self.company_name,
                                company_career_url=self.career_url,
                                keywords_matched=matched_keywords,
                            ))
                    
                    if all_jobs:
                        break

            except Exception as e:
                print(f"  [Greenhouse] Browser error: {e}")
            finally:
                await browser.close()

        print(f"  [Greenhouse] Found {len(all_jobs)} matching jobs via browser")
        return all_jobs

    def _extract_job_id(self, url: str) -> str:
        """Extract Greenhouse job ID from URL."""
        match = re.search(r'/jobs/(\d+)', url)
        if match:
            return f"GH_{match.group(1)}"
        return self.generate_job_id(url, "")
