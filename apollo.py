import logging
import re
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


_STOP_WORDS = {"the", "a", "an", "and", "of", "for", "in", "at", "by", "co", "inc", "ltd", "aps"}


def _names_match(expected: str, returned: str) -> bool:
    """True if any meaningful word from expected appears in returned (case-insensitive)."""
    if not expected or not returned:
        return True  # can't verify, allow through
    exp_words = {w for w in re.sub(r"[^a-z0-9 ]", "", expected.lower()).split() if w not in _STOP_WORDS}
    ret_lower = returned.lower()
    return any(w in ret_lower for w in exp_words)


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


def _hunt_contact(domain: str, api_key: str, company_name: str = "") -> dict | None:
    """Hunter.io domain search — returns best contact by title priority.
    Falls back to company name search when domain is unknown."""
    if not api_key:
        return None
    params = {"limit": 10}
    if domain:
        params["domain"] = domain
    elif company_name:
        params["company"] = company_name
    else:
        return None

    label = domain or company_name
    try:
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params=params,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        if resp.status_code == 401:
            logger.warning("Hunter.io: invalid API key")
            return None
        if not resp.ok:
            logger.debug(f"Hunter.io search failed ({resp.status_code}) for {label}")
            return None

        data = resp.json().get("data", {})
        emails = data.get("emails", [])

        # Store the discovered domain back if we searched by company name
        if not domain and data.get("domain"):
            domain = data["domain"]

        if not emails:
            logger.info(f"Hunter.io: no emails found for {label}")
            return None

        candidates = [(_title_tier(e.get("position", "")), e) for e in emails]
        title_matches = [c for c in candidates if c[0] < 99]

        best = sorted(title_matches, key=lambda x: x[0])[0][1] if title_matches else max(emails, key=lambda e: e.get("confidence", 0))

        if not best.get("value"):
            return None

        # Cross-check: domain or company name from Hunter should match what we expect
        discovered_domain = data.get("domain", "") or domain
        discovered_org = data.get("organization", "") or discovered_domain
        if company_name and not _names_match(company_name, discovered_org):
            logger.warning(f"Hunter.io: company mismatch — expected '{company_name}', got '{discovered_org}'. Skipping.")
            return None

        logger.info(f"Hunter.io [found]: {label} — {best['value']} ({best.get('position', '')})")
        return {
            "found": True,
            "first_name": best.get("first_name", ""),
            "last_name": best.get("last_name", ""),
            "email": best["value"],
            "title": best.get("position", ""),
            "linkedin_url": best.get("linkedin", ""),
            "source": "hunter",
            "_domain": domain,
        }
    except Exception as e:
        logger.debug(f"Hunter.io error for {label}: {e}")
        return None


def _apollo_find_domain(company_name: str, api_key: str) -> str:
    """Search Apollo organizations by name to retrieve the company domain."""
    try:
        resp = requests.post(
            "https://api.apollo.io/api/v1/mixed_companies/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"q_organization_name": company_name, "page": 1, "per_page": 5},
            timeout=15,
        )
        if not resp.ok:
            logger.debug(f"Apollo org search failed ({resp.status_code}) for {company_name}")
            return ""
        orgs = resp.json().get("organizations", [])
        for org in orgs:
            org_name = org.get("name", "")
            org_domain = org.get("primary_domain", "") or org.get("website_url", "")
            if org_domain and _names_match(company_name, org_name):
                domain = org_domain.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
                logger.info(f"Apollo org search found domain for {company_name}: {domain}")
                return domain
    except Exception as e:
        logger.debug(f"Apollo org search error for {company_name}: {e}")
    return ""


def _apollo_contact(domain: str, api_key: str, company_name: str = "") -> dict | None:
    """Apollo.io people search — returns best contact by title priority.
    If no domain, searches Apollo organizations first to find it."""
    if not api_key:
        return None

    label = domain or company_name
    payload = {
        "person_titles": [
            "VP Engineering", "VP of Engineering",
            "Head of Engineering", "Head of Technology",
            "CTO", "Chief Technology Officer",
            "Director of Engineering",
            "Co-Founder", "Founder", "CEO",
        ],
        "page": 1,
        "per_page": 10,
    }
    if domain:
        payload["q_organization_domains"] = [domain]
    else:
        return None  # Can't search without a domain

    try:
        resp = requests.post(
            "https://api.apollo.io/api/v1/mixed_people/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if resp.status_code == 401:
            logger.warning("Apollo.io: invalid API key")
            return None
        if not resp.ok:
            logger.debug(f"Apollo.io search failed ({resp.status_code}) for {label}")
            return None

        people = resp.json().get("people", [])
        if not people:
            logger.info(f"Apollo.io: no people found for {label}")
            return None

        candidates = sorted(people, key=lambda p: _title_tier(p.get("title", "") or ""))

        # Cross-check: filter out people whose org doesn't match the expected company
        if company_name:
            verified = [
                p for p in candidates
                if _names_match(company_name, p.get("organization", {}).get("name", "") if isinstance(p.get("organization"), dict) else str(p.get("organization", "")))
            ]
            if not verified:
                logger.warning(f"Apollo.io: no people matched company name '{company_name}' — skipping.")
                return None
            candidates = verified

        best = candidates[0]
        email = best.get("email", "")
        if not email:
            logger.info(f"Apollo.io: person found for {label} but no email credit available")
            return None

        logger.info(f"Apollo.io [found]: {label} — {email} ({best.get('title', '')})")
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
        logger.debug(f"Apollo.io error for {label}: {e}")
        return None


def enrich_one(
    company: dict,
    hunter_api_key: str = "",
    apollo_api_key: str = "",
) -> dict:
    """Enrich a single company. Returns the company dict with 'contact' set."""
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

    # Step 2: Hunter.io (domain or company name fallback)
    if not contact:
        result = _hunt_contact(domain, hunter_api_key, company_name=name)
        if result:
            # Capture domain discovered via company-name search
            if not domain and result.get("_domain"):
                domain = result["_domain"]
                company["domain"] = domain
            contact = result

    # Step 3: Apollo.io (domain or company name → org lookup → people search)
    if not contact:
        # If Apollo finds the domain via org search, save it back
        if not domain and apollo_api_key:
            found_domain = _apollo_find_domain(name, apollo_api_key)
            if found_domain:
                domain = found_domain
                company["domain"] = domain
        contact = _apollo_contact(domain, apollo_api_key, company_name=name)

    if contact:
        logger.info(f"Contact found [{contact.get('source', 'job_page')}]: {name} — {contact['email']}")
    else:
        contact = _not_found()
        logger.info(f"No contact found: {name}")

    company["contact"] = contact
    return company


def enrich_contacts(
    companies: list[dict],
    hunter_api_key: str = "",
    apollo_api_key: str = "",
    dry_run: bool = False,
) -> list[dict]:
    for company in companies:
        enrich_one(company, hunter_api_key=hunter_api_key, apollo_api_key=apollo_api_key)
        time.sleep(1)
    return companies
