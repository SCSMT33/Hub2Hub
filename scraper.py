import json
import time
import random
import logging
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

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


def _parse_card_text(lines: list[str]) -> tuple[str, str, str]:
    """
    TheHub cards render as:
      Line 0: Job Title
      Line 1: Company Name • Remote • Full-time  (or similar)
      Line 2+: optional description snippet

    Returns (job_title, company_name, description).
    """
    job_title = lines[0].strip() if lines else ""

    company_name = ""
    description = ""

    if len(lines) > 1:
        # Company line uses bullet separators — company is the first segment
        parts = [p.strip() for p in lines[1].replace("·", "•").split("•")]
        company_name = parts[0].strip()

    if len(lines) > 2:
        description = " ".join(lines[2:5]).strip()[:500]

    return job_title, company_name, description


def _scrape_page(page, url: str) -> list[dict]:
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_selector("a[href^='/jobs/']", timeout=15000)
        # Small extra wait for any lazy-loaded content
        page.wait_for_timeout(1500)
    except PWTimeout:
        logger.warning(f"Timeout loading: {url}")
        return []

    # Use Playwright JS to extract card data directly from the live DOM
    cards_data = page.eval_on_selector_all(
        "a[href^='/jobs/']",
        """els => els
            .filter(el => el.getAttribute('href').length > 7 && !el.getAttribute('href').includes('?'))
            .map(el => ({
                href: el.getAttribute('href'),
                text: el.innerText
            }))
        """
    )

    jobs = []
    for card in cards_data:
        href = card.get("href", "")
        raw_text = card.get("text", "")

        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        if len(lines) < 2:
            continue

        job_title, company_name, description = _parse_card_text(lines)

        if not job_title or not company_name:
            continue

        jobs.append({
            "company_name": company_name,
            "company_website": "",
            "domain": "",
            "job_title": job_title,
            "description": description,
            "job_url": f"https://thehub.io{href}",
        })

    logger.info(f"Found {len(jobs)} job cards on {url}")
    return jobs


def scrape_jobs() -> list[dict]:
    seen = _load_seen()
    today = str(date.today())
    results = []
    seen_companies_this_run: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pw_page = browser.new_page()
        pw_page.set_extra_http_headers({
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

            jobs = _scrape_page(pw_page, url)
            if not jobs:
                logger.info(f"No jobs on page {page_num}, stopping.")
                break

            for job in jobs:
                company = job["company_name"].strip().lower()
                if seen.get(company) == today:
                    continue
                if company in seen_companies_this_run:
                    continue
                seen_companies_this_run.add(company)
                seen[company] = today
                results.append(job)

            # Check for next page
            next_link = pw_page.query_selector("a[href*='page=2'], a[rel='next']")
            if not next_link or page_num >= 10:
                break

            page_num += 1
            time.sleep(random.uniform(1, 2))

        browser.close()

    _save_seen(seen)
    logger.info(f"Scraped {len(results)} new companies.")
    return results
