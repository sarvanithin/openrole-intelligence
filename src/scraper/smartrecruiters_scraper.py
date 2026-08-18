"""
SmartRecruiters career site scraper.
Uses the SmartRecruiters API for reliable JSON data extraction.

SmartRecruiters URL patterns:
- jobs.smartrecruiters.com/{company}
- jobs.smartrecruiters.com/{company}/job/{id}
- API: api.smartrecruiters.com/v1/companies/{company}/postings
"""
import asyncio
import re
from typing import List, Optional

import requests
from playwright.async_api import async_playwright

from config import PAGE_LOAD_TIMEOUT_MS
from scraper.base_scraper import BaseScraper, Job


class SmartRecruitersScraper(BaseScraper):
    """
    Scraper for SmartRecruiters career sites.
    Prioritizes API calls for reliability, falls back to browser scraping.
    """

    API_BASE = "https://api.smartrecruiters.com/v1/companies"
    
    # SmartRecruiters-specific selectors
    JOB_SELECTORS = [
        'a[href*="/job/"]',
        '.job-item a',
        '.opening-item a',
        '[data-test*="job"] a',
        '.job-list a',
        'article.job a',
    ]

    def __init__(self, company_name: str, career_url: str):
        super().__init__(company_name, career_url)
        self.company_id = self._extract_company_id(career_url)

    def _extract_company_id(self, url: str) -> Optional[str]:
        """Extract the SmartRecruiters company ID from URL."""
        patterns = [
            r'jobs\.smartrecruiters\.com/([^/\?]+)',
            r'careers\.smartrecruiters\.com/([^/\?]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    async def scrape(self) -> List[Job]:
        """Scrape jobs using API first, then fallback to browser."""
        # Try API first (most reliable)
        if self.company_id:
            jobs = await self._scrape_via_api()
            if jobs:
                return jobs
            print(f"  [SmartRecruiters] API failed, falling back to browser")

        # Fallback to browser scraping
        return await self._scrape_via_browser()

    async def _scrape_via_api(self) -> List[Job]:
        """Scrape using SmartRecruiters API."""
        all_jobs: List[Job] = []
        offset = 0
        limit = 100
        
        try:
            while True:
                url = f"{self.API_BASE}/{self.company_id}/postings?limit={limit}&offset={offset}"
                print(f"  [SmartRecruiters] Fetching from API (offset={offset})")
                
                response = requests.get(url, timeout=30)
                if response.status_code != 200:
                    print(f"  [SmartRecruiters] API returned {response.status_code}")
                    break
                
                data = response.json()
                content = data.get("content", [])
                
                if not content:
                    break
                
                print(f"  [SmartRecruiters] Got {len(content)} postings")
                
                for posting in content:
                    title = posting.get("name", "")
                    
                    # Check if matches keywords
                    matched_keywords = self.matches_keywords(title)
                    if not matched_keywords:
                        continue
                    
                    job_id = f"SR_{posting.get('id', '')}"
                    
                    # Build job URL
                    ref = posting.get("ref", "")
                    job_url = f"https://jobs.smartrecruiters.com/{self.company_id}/{ref}" if ref else ""
                    
                    # Extract location
                    location_data = posting.get("location", {})
                    location = location_data.get("city", "") if isinstance(location_data, dict) else ""
                    
                    all_jobs.append(Job(
                        job_id=job_id,
                        job_title=title,
                        job_url=job_url,
                        company_name=self.company_name,
                        company_career_url=self.career_url,
                        location=location,
                        keywords_matched=matched_keywords,
                    ))
                
                # Check for more pages
                total = data.get("totalFound", 0)
                offset += limit
                if offset >= total:
                    break
            
            print(f"  [SmartRecruiters] Found {len(all_jobs)} matching jobs via API")
            return all_jobs
            
        except Exception as e:
            print(f"  [SmartRecruiters] API error: {e}")
            return []

    async def _scrape_via_browser(self) -> List[Job]:
        """Fallback browser scraping for SmartRecruiters."""
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

                for selector in self.JOB_SELECTORS:
                    elements = await page.query_selector_all(selector)
                    
                    for element in elements:
                        href = await element.get_attribute('href')
                        if not href or href in seen_urls:
                            continue
                        
                        job_url = self.normalize_url(href)
                        if '/job/' not in job_url.lower():
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
                print(f"  [SmartRecruiters] Browser error: {e}")
            finally:
                await browser.close()

        print(f"  [SmartRecruiters] Found {len(all_jobs)} matching jobs via browser")
        return all_jobs

    def _extract_job_id(self, url: str) -> str:
        """Extract SmartRecruiters job ID from URL."""
        match = re.search(r'/job/([a-zA-Z0-9-]+)', url)
        if match:
            return f"SR_{match.group(1)}"
        return self.generate_job_id(url, "")
