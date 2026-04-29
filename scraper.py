import json
import time
import random
import logging
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

SEEN_FILE = Path(__file__).parent / "seen_companies.json"
BASE_URL = (
    "https://thehub.io/jobs"
    "?roles=backenddeveloper&roles=devops&roles=frontenddeveloper"
    "&roles=fullstackdeveloper&roles=mobiledevelopment&roles=uxuidesigner"
    "&roles=qualityassurance&countryCode=REMOTE&sorting=newJobs"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

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


def _parse_jobs_page(soup: BeautifulSoup) -> list[dict]:
    jobs = []
    # TheHub job cards — selectors based on their current markup
    cards = soup.select("a[data-cy='job-list-item']") or soup.select(".JobCard") or soup.select("article")

    if not cards:
        # Fallback: look for any anchor wrapping job data
        cards = soup.find_all("a", href=lambda h: h and "/jobs/" in h)

    for card in cards:
        try:
            href = card.get("href", "")
            job_url = f"https://thehub.io{href}" if href.startswith("/") else href

            # Company name
            company_el = (
                card.select_one("[data-cy='company-name']")
                or card.select_one(".company-name")
                or card.select_one("h3")
            )
            company_name = company_el.get_text(strip=True) if company_el else ""

            # Job title
            title_el = (
                card.select_one("[data-cy='job-title']")
                or card.select_one(".job-title")
                or card.select_one("h2")
            )
            job_title = title_el.get_text(strip=True) if title_el else ""

            # Company website — may appear as a link inside the card
            website_el = card.select_one("a[href*='://']:not([href*='thehub.io'])")
            company_website = website_el["href"] if website_el else ""

            # Description snippet
            desc_el = card.select_one(".description") or card.select_one("p")
            description = desc_el.get_text(strip=True)[:500] if desc_el else ""

            if company_name and job_title:
                jobs.append(
                    {
                        "company_name": company_name,
                        "company_website": company_website,
                        "domain": _extract_domain(company_website),
                        "job_title": job_title,
                        "description": description,
                        "job_url": job_url,
                    }
                )
        except Exception as e:
            logger.debug(f"Failed to parse card: {e}")
            continue

    return jobs


def _fetch_page(url: str) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return None


def scrape_jobs() -> list[dict]:
    seen = _load_seen()
    today = str(date.today())
    results = []
    seen_companies_this_run: set[str] = set()

    page = 1
    while True:
        url = BASE_URL + (f"&page={page}" if page > 1 else "")
        logger.info(f"Scraping page {page}: {url}")

        soup = _fetch_page(url)
        if soup is None:
            break

        jobs = _parse_jobs_page(soup)
        if not jobs:
            logger.info(f"No jobs found on page {page}, stopping pagination.")
            break

        for job in jobs:
            company = job["company_name"].strip().lower()

            # Skip if already seen today
            if seen.get(company) == today:
                continue
            # Skip duplicates within this run
            if company in seen_companies_this_run:
                continue

            seen_companies_this_run.add(company)
            seen[company] = today
            results.append(job)

        # Check for next page link
        next_btn = soup.select_one("a[rel='next']") or soup.select_one(".pagination__next")
        if not next_btn:
            break

        page += 1
        time.sleep(random.uniform(1, 2))

    _save_seen(seen)
    logger.info(f"Scraped {len(results)} new companies.")
    return results
