import logging
import time
import requests

logger = logging.getLogger(__name__)

APOLLO_URL = "https://api.apollo.io/v1/people/search"
TARGET_TITLES = [
    "CTO",
    "VP Engineering",
    "Head of Engineering",
    "VP Technology",
    "Co-Founder",
    "Head of Technology",
]


def find_contact(company_name: str, domain: str, api_key: str) -> dict:
    if domain:
        payload = {
            "person_titles": TARGET_TITLES,
            "organization_domains": [domain],
            "page": 1,
            "per_page": 1,
        }
        search_label = domain
    elif company_name:
        payload = {
            "person_titles": TARGET_TITLES,
            "q_organization_name": company_name,
            "page": 1,
            "per_page": 1,
        }
        search_label = company_name
    else:
        return _not_found()

    try:
        resp = requests.post(
            APOLLO_URL,
            json=payload,
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        people = data.get("people", [])
        if not people:
            logger.info(f"No contact found for: {search_label}")
            return _not_found()

        person = people[0]
        return {
            "found": True,
            "first_name": person.get("first_name", ""),
            "last_name": person.get("last_name", ""),
            "email": person.get("email", ""),
            "title": person.get("title", ""),
            "linkedin_url": person.get("linkedin_url", ""),
        }

    except requests.HTTPError as e:
        logger.error(f"Apollo HTTP error for {search_label}: {e} — {resp.text[:200]}")
        return _not_found()
    except Exception as e:
        logger.error(f"Apollo error for {search_label}: {e}")
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
    # In dry-run mode, only call Apollo for the first company to save credits
    for i, company in enumerate(companies):
        if dry_run and i >= 1:
            company["contact"] = _not_found()
            logger.info(f"Apollo [skipped — dry run]: {company['company_name']}")
            continue

        domain = company.get("domain", "")
        name = company.get("company_name", "")
        contact = find_contact(name, domain, api_key)
        company["contact"] = contact
        status = "found" if contact["found"] else "not found"
        logger.info(f"Apollo [{status}]: {name}")
        time.sleep(1)

    return companies
