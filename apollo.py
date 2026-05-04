import logging
import time

from page_contacts import scrape_contact_and_domain_from_job_page

logger = logging.getLogger(__name__)


def _not_found() -> dict:
    return {
        "found": False,
        "first_name": "",
        "last_name": "",
        "email": "",
        "title": "",
        "linkedin_url": "",
    }


def enrich_contacts(companies: list[dict], api_key: str = "", dry_run: bool = False) -> list[dict]:
    """
    Enrich each company with a contact found directly on its job page.
    No third-party email services used — job page only.
    """
    for company in companies:
        name = company.get("company_name", "")
        job_url = company.get("job_url", "")
        contact = None

        if job_url:
            contact, scraped_domain = scrape_contact_and_domain_from_job_page(job_url)
            if scraped_domain and not company.get("domain"):
                company["domain"] = scraped_domain

        if contact:
            logger.info(f"Job page [found]: {name} — {contact['email']}")
        else:
            contact = _not_found()
            logger.info(f"No email found on job page: {name}")

        company["contact"] = contact
        time.sleep(1)

    return companies
