import logging
import re
import time

import requests

from page_contacts import scrape_contact_and_domain_from_job_page

logger = logging.getLogger(__name__)

_APOLLO_BASE = "https://api.apollo.io/api/v1"

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
    """True if any meaningful word from expected appears in returned."""
    if not expected or not returned:
        return True
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
    return {"found": False, "first_name": "", "last_name": "", "email": "", "title": "", "linkedin_url": "", "source": ""}


def _apollo_headers(api_key: str) -> dict:
    return {"Content-Type": "application/json"}


def _apollo_params(api_key: str) -> dict:
    """Apollo currently uses api_key as a URL query parameter."""
    return {"api_key": api_key}


# ---------------------------------------------------------------------------
# Hunter.io
# ---------------------------------------------------------------------------

def _hunt_contact(domain: str, hunter_key: str, company_name: str = "") -> dict | None:
    """Hunter.io domain search — best contact by title priority."""
    if not hunter_key:
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
            headers={"Authorization": f"Bearer {hunter_key}"},
            timeout=15,
        )
        if resp.status_code == 401:
            logger.warning("Hunter.io: invalid API key")
            return None
        if not resp.ok:
            logger.info(f"Hunter.io domain search failed ({resp.status_code}) for {label}")
            return None

        data = resp.json().get("data", {})
        emails = data.get("emails", [])
        discovered_domain = data.get("domain", "") or domain

        if not emails:
            logger.info(f"Hunter.io: no emails found for {label}")
            return None

        # Verify company name matches
        discovered_org = data.get("organization", "") or discovered_domain
        if company_name and not _names_match(company_name, discovered_org):
            logger.warning(f"Hunter.io: company mismatch — expected '{company_name}', got '{discovered_org}'. Skipping.")
            return None

        candidates = [(_title_tier(e.get("position", "")), e) for e in emails]
        title_matches = [c for c in candidates if c[0] < 99]
        best = sorted(title_matches, key=lambda x: x[0])[0][1] if title_matches else max(emails, key=lambda e: e.get("confidence", 0))

        if not best.get("value"):
            return None

        logger.info(f"Hunter.io domain [found]: {label} — {best['value']} ({best.get('position', '')})")
        return {
            "found": True,
            "first_name": best.get("first_name", ""),
            "last_name": best.get("last_name", ""),
            "email": best["value"],
            "title": best.get("position", ""),
            "linkedin_url": best.get("linkedin", ""),
            "source": "hunter",
            "_domain": discovered_domain,
        }
    except Exception as e:
        logger.debug(f"Hunter.io domain search error for {label}: {e}")
        return None


def _hunter_find_email(domain: str, first_name: str, last_name: str, hunter_key: str) -> str:
    """Hunter.io email-finder: get email for a known person at a domain. Costs 1 credit."""
    if not hunter_key or not domain or not first_name or not last_name:
        return ""
    try:
        resp = requests.get(
            "https://api.hunter.io/v2/email-finder",
            params={"domain": domain, "first_name": first_name, "last_name": last_name},
            headers={"Authorization": f"Bearer {hunter_key}"},
            timeout=15,
        )
        if not resp.ok:
            logger.info(f"Hunter.io email-finder failed ({resp.status_code}) for {first_name} {last_name} @ {domain}")
            return ""
        email = resp.json().get("data", {}).get("email", "")
        if email:
            logger.info(f"Hunter.io email-finder [found]: {first_name} {last_name} → {email}")
        return email or ""
    except Exception as e:
        logger.debug(f"Hunter.io email-finder error: {e}")
        return ""


# ---------------------------------------------------------------------------
# Apollo.io
# ---------------------------------------------------------------------------

def _apollo_find_domain(company_name: str, apollo_key: str) -> str:
    """Apollo org search by company name → returns primary domain."""
    try:
        resp = requests.post(
            f"{_APOLLO_BASE}/mixed_companies/search",
            params={"api_key": apollo_key},
            json={"q_organization_name": company_name, "page": 1, "per_page": 5},
            timeout=15,
        )
        if not resp.ok:
            logger.info(f"Apollo org search failed ({resp.status_code}) for '{company_name}': {resp.text[:200]}")
            return ""
        orgs = resp.json().get("organizations", [])
        if not orgs:
            logger.info(f"Apollo org search: no results for '{company_name}'")
            return ""
        logger.info(f"Apollo org search: {len(orgs)} result(s) for '{company_name}': {[o.get('name') for o in orgs]}")
        for org in orgs:
            org_name = org.get("name", "")
            org_domain = org.get("primary_domain", "")
            if org_domain and _names_match(company_name, org_name):
                logger.info(f"Apollo org search: matched '{org_name}' → {org_domain}")
                return org_domain
        logger.info(f"Apollo org search: no name match for '{company_name}'")
    except Exception as e:
        logger.info(f"Apollo org search error for '{company_name}': {e}")
    return ""


def _apollo_find_person(apollo_key: str, domain: str = "", company_name: str = "") -> dict | None:
    """
    Apollo people search — finds best-titled person at the company.
    Searches by domain if available, falls back to company name.
    Returns name + title only (no email — use Hunter email-finder for that).
    """
    if not apollo_key or (not domain and not company_name):
        return None

    if domain:
        search_filter = {"q_organization_domains_list[]": domain}
        label = domain
    else:
        search_filter = {"q_organization_name": company_name}
        label = company_name

    try:
        resp = requests.post(
            f"{_APOLLO_BASE}/mixed_people/api_search",
            params={"api_key": apollo_key},
            json={
                **search_filter,
                "person_seniorities[]": ["owner", "founder", "c_suite", "vp", "director"],
                "page": 1,
                "per_page": 10,
            },
            timeout=15,
        )
        if not resp.ok:
            logger.info(f"Apollo people search failed ({resp.status_code}) for {label}: {resp.text[:200]}")
            return None

        people = resp.json().get("people", [])
        if not people:
            logger.info(f"Apollo people search: no people found for {label}")
            return None

        logger.info(f"Apollo people search: {len(people)} person(s) found for {label}")

        # Pick best by title priority
        best = sorted(people, key=lambda p: _title_tier(p.get("title", "") or ""))[0]
        logger.info(f"Apollo people search [found]: {best.get('name', '')} — {best.get('title', '')} @ {label}")
        org = best.get("organization") or {}
        org_domain = (org.get("primary_domain", "") if isinstance(org, dict) else "") or ""
        return {
            "first_name": best.get("first_name", ""),
            "last_name": best.get("last_name", ""),
            "title": best.get("title", ""),
            "linkedin_url": best.get("linkedin_url", ""),
            "_org_domain": org_domain,
        }
    except Exception as e:
        logger.debug(f"Apollo people search error for {label}: {e}")
        return None


# ---------------------------------------------------------------------------
# Enrichment orchestration
# ---------------------------------------------------------------------------

def enrich_one(
    company: dict,
    hunter_api_key: str = "",
    apollo_api_key: str = "",
) -> dict:
    """Enrich a single company with a contact. Cascade: job page → Hunter → Apollo+Hunter."""
    name = company.get("company_name", "")
    job_url = company.get("job_url", "")
    contact = None
    domain = company.get("domain", "")

    # Step 1: Scrape job page directly
    if job_url:
        contact, scraped_domain = scrape_contact_and_domain_from_job_page(job_url)
        if scraped_domain and not domain:
            domain = scraped_domain
            company["domain"] = domain

    # Step 2: Hunter.io domain search (domain or company name)
    if not contact:
        result = _hunt_contact(domain, hunter_api_key, company_name=name)
        if result:
            if not domain and result.get("_domain"):
                domain = result["_domain"]
                company["domain"] = domain
            contact = result

    # Step 3: Apollo finds the person → Hunter gets their email
    if not contact:
        # 3a: Get domain via Apollo org search if still unknown
        if not domain and apollo_api_key:
            domain = _apollo_find_domain(name, apollo_api_key)
            if domain:
                company["domain"] = domain

        # 3b: Find best-titled person — by domain if known, else by company name directly
        if apollo_api_key:
            person = _apollo_find_person(apollo_api_key, domain=domain, company_name=name)
            if person and person.get("first_name") and person.get("last_name"):
                # 3c: Need a domain for Hunter email-finder — extract from Apollo result if missing
                if not domain:
                    org = person.get("_org_domain", "")
                    if org:
                        domain = org
                        company["domain"] = domain
                email = _hunter_find_email(domain, person["first_name"], person["last_name"], hunter_api_key)
                if email:
                    contact = {
                        "found": True,
                        "first_name": person["first_name"],
                        "last_name": person["last_name"],
                        "email": email,
                        "title": person.get("title", ""),
                        "linkedin_url": person.get("linkedin_url", ""),
                        "source": "apollo+hunter",
                    }
                else:
                    logger.info(f"Apollo found person but Hunter couldn't get email for {name}")

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
