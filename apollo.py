import logging
import time
import requests

from page_contacts import scrape_contact_from_job_page

logger = logging.getLogger(__name__)

HUNTER_URL = "https://api.hunter.io/v2/domain-search"

PRIORITY_TITLES = [
    "cto", "chief technology officer",
    "vp engineering", "vice president engineering",
    "head of engineering", "head of technology",
    "vp technology", "vice president technology",
    "co-founder", "cofounder",
    "founder",
    "lead developer", "lead engineer",
    "engineering manager",
]


def _score_title(title: str) -> int:
    """Return priority score for a contact title — higher is better."""
    if not title:
        return 0
    t = title.lower()
    for i, keyword in enumerate(PRIORITY_TITLES):
        if keyword in t:
            return len(PRIORITY_TITLES) - i
    return 0


def find_contact(company_name: str, domain: str, api_key: str) -> dict:
    params = {"api_key": api_key, "limit": 10}

    if domain:
        params["domain"] = domain
        search_label = domain
    elif company_name:
        params["company"] = company_name
        search_label = company_name
    else:
        return _not_found()

    try:
        resp = requests.get(HUNTER_URL, params=params, timeout=15)

        if resp.status_code == 429:
            logger.warning("Hunter.io rate limit hit — skipping contact lookup.")
            return _not_found()

        resp.raise_for_status()
        data = resp.json().get("data", {})
        emails = data.get("emails", [])

        if not emails:
            logger.info(f"No contacts found by Hunter for: {search_label}")
            return _not_found()

        # Pick the best match by title priority
        best = max(emails, key=lambda e: _score_title(e.get("position", "")))

        return {
            "found": True,
            "first_name": best.get("first_name", ""),
            "last_name": best.get("last_name", ""),
            "email": best.get("value", ""),
            "title": best.get("position", ""),
            "linkedin_url": best.get("linkedin", ""),
        }

    except requests.HTTPError as e:
        logger.error(f"Hunter HTTP error for {search_label}: {e} — {resp.text[:200]}")
        return _not_found()
    except Exception as e:
        logger.error(f"Hunter error for {search_label}: {e}")
        return _not_found()


def _not_found() -> dict:
    return {
        "found": False,
        "first_name": "",
        "last_name": "",
        "email": "",
        "title": "",
        "linkedin_url": "",
    }


def enrich_contacts(companies: list[dict], api_key: str, dry_run: bool = False) -> list[dict]:
    for i, company in enumerate(companies):
        name = company.get("company_name", "")

        if dry_run and i >= 3:
            company["contact"] = _not_found()
            logger.info(f"Contact lookup [skipped — dry run]: {name}")
            continue

        # Step 1: try scraping the job page directly (free, no credits)
        job_url = company.get("job_url", "")
        contact = None
        if job_url:
            contact = scrape_contact_from_job_page(job_url)

        # Step 2: fall back to Hunter.io
        if not contact:
            if api_key:
                domain = company.get("domain", "")
                contact = find_contact(name, domain, api_key)
                logger.info(f"Hunter [{'found' if contact['found'] else 'not found'}]: {name}")
            else:
                contact = _not_found()
        else:
            logger.info(f"Job page [found]: {name} — {contact['email']}")

        company["contact"] = contact
        time.sleep(1)

    return companies
