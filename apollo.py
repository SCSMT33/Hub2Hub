import logging
import time

import requests

from page_contacts import scrape_contact_and_domain_from_job_page

logger = logging.getLogger(__name__)

# Title priority: lower index = higher priority
_TITLE_TIERS = [
    ["vp of engineering", "vp engineering", "vice president of engineering", "vp of technology", "vp technology"],
    ["head of engineering", "head of technology", "head of tech"],
    ["cto", "chief technology officer", "chief technical officer"],
    ["director of engineering", "director of technology", "engineering director"],
    ["co-founder", "cofounder", "technical co-founder", "founding engineer"],
    ["founder", "ceo", "chief executive officer"],
]


def _title_tier(title: str) -> int:
    t = title.lower()
    for i, keywords in enumerate(_TITLE_TIERS):
        if any(kw in t for kw in keywords):
            return i
    return 99


def _not_found() -> dict:
    return {
        "found": False,
        "first_name": "",
        "last_name": "",
        "email": "",
        "title": "",
        "linkedin_url": "",
        "source": "",
    }


def _hunt_contact(domain: str, api_key: str) -> dict | None:
    """Hunter.io domain search — returns best contact by title priority."""
    if not api_key or not domain:
        return None
    try:
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "limit": 10},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        if resp.status_code == 401:
            logger.warning("Hunter.io: invalid API key")
            return None
        if not resp.ok:
            logger.debug(f"Hunter.io domain search failed ({resp.status_code}) for {domain}")
            return None

        emails = resp.json().get("data", {}).get("emails", [])
        if not emails:
            logger.info(f"Hunter.io: no emails found for {domain}")
            return None

        # Filter to title-matched candidates; fall back to highest confidence
        candidates = [(
            _title_tier(e.get("position", "")), e
        ) for e in emails]
        title_matches = [c for c in candidates if c[0] < 99]

        if title_matches:
            best = sorted(title_matches, key=lambda x: x[0])[0][1]
        else:
            best = max(emails, key=lambda e: e.get("confidence", 0))

        if not best.get("value"):
            return None

        logger.info(f"Hunter.io [found]: {domain} — {best['value']} ({best.get('position', '')})")
        return {
            "found": True,
            "first_name": best.get("first_name", ""),
            "last_name": best.get("last_name", ""),
            "email": best["value"],
            "title": best.get("position", ""),
            "linkedin_url": best.get("linkedin", ""),
            "source": "hunter",
        }
    except Exception as e:
        logger.debug(f"Hunter.io error for {domain}: {e}")
        return None


def _apollo_contact(domain: str, api_key: str) -> dict | None:
    """Apollo.io people search — returns best contact by title priority."""
    if not api_key or not domain:
        return None
    try:
        resp = requests.post(
            "https://api.apollo.io/api/v1/mixed_people/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={
                "q_organization_domains": [domain],
                "person_titles": [
                    "VP Engineering", "VP of Engineering",
                    "Head of Engineering", "Head of Technology",
                    "CTO", "Chief Technology Officer",
                    "Director of Engineering",
                    "Co-Founder", "Founder", "CEO",
                ],
                "page": 1,
                "per_page": 10,
            },
            timeout=15,
        )
        if resp.status_code == 401:
            logger.warning("Apollo.io: invalid API key")
            return None
        if not resp.ok:
            logger.debug(f"Apollo.io search failed ({resp.status_code}) for {domain}")
            return None

        people = resp.json().get("people", [])
        if not people:
            logger.info(f"Apollo.io: no people found for {domain}")
            return None

        candidates = sorted(
            people,
            key=lambda p: _title_tier(p.get("title", "") or ""),
        )
        best = candidates[0]
        email = best.get("email", "")
        if not email:
            logger.info(f"Apollo.io: person found for {domain} but no email credit available")
            return None

        logger.info(f"Apollo.io [found]: {domain} — {email} ({best.get('title', '')})")
        return {
            "found": True,
            "first_name": best.get("first_name", ""),
            "last_name": best.get("last_name", ""),
            "email": email,
            "title": best.get("title", ""),
            "linkedin_url": best.get("linkedin_url", ""),
            "source": "apollo",
        }
    except Exception as e:
        logger.debug(f"Apollo.io error for {domain}: {e}")
        return None


def enrich_contacts(
    companies: list[dict],
    hunter_api_key: str = "",
    apollo_api_key: str = "",
    dry_run: bool = False,
) -> list[dict]:
    for company in companies:
        name = company.get("company_name", "")
        job_url = company.get("job_url", "")
        contact = None
        domain = company.get("domain", "")

        # Step 1: scrape the job page directly
        if job_url:
            contact, scraped_domain = scrape_contact_and_domain_from_job_page(job_url)
            if scraped_domain and not domain:
                domain = scraped_domain
                company["domain"] = domain

        # Step 2: Hunter.io
        if not contact and domain:
            contact = _hunt_contact(domain, hunter_api_key)

        # Step 3: Apollo.io
        if not contact and domain:
            contact = _apollo_contact(domain, apollo_api_key)

        if contact:
            logger.info(f"Contact found [{contact.get('source', 'job_page')}]: {name} — {contact['email']}")
        else:
            contact = _not_found()
            logger.info(f"No contact found: {name}")

        company["contact"] = contact
        time.sleep(1)

    return companies
