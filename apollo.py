import logging
import requests

logger = logging.getLogger(__name__)

APOLLO_URL = "https://api.apollo.io/v1/mixed_people/search"
TARGET_TITLES = [
    "CTO",
    "VP Engineering",
    "Head of Engineering",
    "VP Technology",
    "Co-Founder",
    "Head of Technology",
]


def find_contact(domain: str, api_key: str) -> dict:
    if not domain:
        return _not_found()

    payload = {
        "api_key": api_key,
        "person_titles": TARGET_TITLES,
        "organization_domains": [domain],
        "page": 1,
        "per_page": 1,
    }

    try:
        resp = requests.post(APOLLO_URL, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        people = data.get("people", [])
        if not people:
            logger.info(f"No contact found for domain: {domain}")
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
        logger.error(f"Apollo HTTP error for {domain}: {e} — {resp.text[:200]}")
        return _not_found()
    except Exception as e:
        logger.error(f"Apollo error for {domain}: {e}")
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


def enrich_contacts(companies: list[dict], api_key: str) -> list[dict]:
    for company in companies:
        domain = company.get("domain", "")
        contact = find_contact(domain, api_key)
        company["contact"] = contact
        status = "found" if contact["found"] else "not found"
        logger.info(f"Apollo [{status}]: {company['company_name']} ({domain})")
    return companies
