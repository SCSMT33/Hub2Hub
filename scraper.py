import json
import time
import random
import logging
from datetime import date
from pathlib import Path

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


def _scrape_page(page, url: str) -> list[dict]:
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_selector("a[href^='/jobs/']", timeout=15000)
        page.wait_for_timeout(1500)
    except PWTimeout:
        logger.warning(f"Timeout loading: {url}")
        return []

    # Walk up one level to div.card__content which holds the text
    cards_data = page.eval_on_selector_all(
        "a[href^='/jobs/']",
        """els => els
            .filter(el => {
                const href = el.getAttribute('href');
                return href.length > 7 && !href.includes('?');
            })
            .map(el => {
                const card = el.parentElement;
                const text = card ? card.innerText.trim() : '';
                const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                const jobTitle = lines[0] || '';
                // Line 2: "CompanyName Remote Full-time" — company is everything before "Remote"
                const secondLine = lines[1] || '';
                const remoteIdx = secondLine.search(/\\bRemote\\b|\\bOn-site\\b|\\bHybrid\\b/i);
                const companyName = remoteIdx > 0
                    ? secondLine.slice(0, remoteIdx).trim()
                    : secondLine.split(' ')[0];
                const lowerText = text.toLowerCase();
                const unpaid = lowerText.includes('unpaid') ||
                               lowerText.includes('equity only') ||
                               lowerText.includes('equity-only');
                return {
                    href: el.getAttribute('href'),
                    job_title: jobTitle,
                    company_name: companyName,
                    unpaid: unpaid,
                };
            })
            .filter(c => c.job_title && c.company_name)
        """
    )

    jobs = []
    for card in cards_data:
        jobs.append({
            "company_name": card["company_name"],
            "company_website": "",
            "domain": "",
            "job_title": card["job_title"],
            "description": f"Hiring: {card['job_title']}" + (" [COMPENSATION: UNPAID/EQUITY-ONLY]" if card.get("unpaid") else ""),
            "job_url": f"https://thehub.io{card['href']}",
        })

    logger.info(f"Found {len(jobs)} jobs on {url}")
    return jobs


def scrape_jobs(ignore_seen: bool = False) -> list[dict]:
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
                if not ignore_seen and seen.get(company) == today:
                    continue
                if company in seen_companies_this_run:
                    continue
                seen_companies_this_run.add(company)
                if not ignore_seen:
                    seen[company] = today
                results.append(job)

            # Check for next page
            next_link = pw_page.query_selector(f"a[href*='page={page_num + 1}']")
            if not next_link or page_num >= 10:
                break

            page_num += 1
            time.sleep(random.uniform(1, 2))

        browser.close()

    _save_seen(seen)
    logger.info(f"Scraped {len(results)} new companies.")
    return results
