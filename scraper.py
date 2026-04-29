import json
import time
import random
import logging
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SEEN_FILE = Path(__file__).parent / "seen_companies.json"
BASE_URL = (
    "https://thehub.io/jobs"
    "?roles=backenddeveloper&roles=devops&roles=frontenddeveloper"
    "&roles=fullstackdeveloper&roles=mobiledevelopment&roles=uxuidesigner"
    "&roles=qualityassurance&countryCode=REMOTE&sorting=newJobs"
)

logger = logging.getLogger(__name__)


def _load_seen() -> dict:
    if SEEN_FILE.exists():
        with open(SEEN_FILE) as f:
            return json.load(f)
    return {}


def _save_seen(seen: dict):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2)


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        return parsed.netloc.lstrip("www.")
    except Exception:
        return url


def _parse_jobs_from_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    jobs = []

    # TheHub renders job cards as <a> tags linking to /jobs/<slug>
    cards = soup.find_all("a", href=lambda h: h and h.startswith("/jobs/") and len(h) > 7)

    for card in cards:
        try:
            href = card.get("href", "")
            job_url = f"https://thehub.io{href}"

            # All visible text nodes in the card
            texts = [t.strip() for t in card.stripped_strings if t.strip()]

            if len(texts) < 2:
                continue

            # First text is usually job title, second is company name
            # (order can vary — we pick the longest as title)
            job_title = texts[0]
            company_name = ""
            description = ""

            # Find company name: look for a span/div that isn't the title
            company_el = (
                card.select_one("[class*='company']")
                or card.select_one("[class*='employer']")
                or card.select_one("span")
            )
            if company_el:
                company_name = company_el.get_text(strip=True)

            # If we still have no company, use second text block
            if not company_name and len(texts) > 1:
                company_name = texts[1]

            # Description: any paragraph text
            desc_el = card.select_one("p")
            if desc_el:
                description = desc_el.get_text(strip=True)[:500]
            elif len(texts) > 3:
                description = " ".join(texts[2:5])[:500]

            if job_title and company_name and job_title != company_name:
                jobs.append({
                    "company_name": company_name,
                    "company_website": "",
                    "domain": "",
                    "job_title": job_title,
                    "description": description,
                    "job_url": job_url,
                })
        except Exception as e:
            logger.debug(f"Card parse error: {e}")
            continue

    return jobs


def _scrape_with_playwright() -> list[dict]:
    all_jobs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })

        page_num = 1
        while True:
            url = BASE_URL + (f"&page={page_num}" if page_num > 1 else "")
            logger.info(f"Scraping page {page_num}: {url}")

            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                # Wait for job cards to appear
                page.wait_for_selector("a[href^='/jobs/']", timeout=15000)
            except PWTimeout:
                logger.warning(f"Timeout waiting for jobs on page {page_num}, stopping.")
                break

            html = page.content()
            jobs = _parse_jobs_from_html(html)

            if not jobs:
                logger.info(f"No jobs found on page {page_num}, stopping pagination.")
                break

            logger.info(f"Page {page_num}: found {len(jobs)} job cards")
            all_jobs.extend(jobs)

            # Check for a next-page link
            next_link = page.query_selector("a[rel='next']")
            if not next_link:
                break

            page_num += 1
            time.sleep(random.uniform(1, 2))

        browser.close()

    return all_jobs


def scrape_jobs() -> list[dict]:
    seen = _load_seen()
    today = str(date.today())
    results = []
    seen_companies_this_run: set[str] = set()

    raw_jobs = _scrape_with_playwright()

    for job in raw_jobs:
        company = job["company_name"].strip().lower()

        if seen.get(company) == today:
            continue
        if company in seen_companies_this_run:
            continue

        seen_companies_this_run.add(company)
        seen[company] = today
        results.append(job)

    _save_seen(seen)
    logger.info(f"Scraped {len(results)} new companies.")
    return results
