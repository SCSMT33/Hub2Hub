import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)
FAILED_LOG = Path(__file__).parent / "failed_log.txt"

HUBSPOT_BASE = "https://api.hubapi.com"

# Legal suffixes to strip when normalising company names for dedup
_LEGAL_SUFFIX = re.compile(
    r"[\s,]+(aps|a/s|as|ltd|limited|inc|llc|corp|corporation|gmbh|sas|sarl|bv|nv|se|plc|pvt|co)\.?$",
    re.IGNORECASE,
)


def _norm(name: str) -> str:
    """Normalise company name — lowercase, strip legal suffix and whitespace."""
    return _LEGAL_SUFFIX.sub("", name).strip().lower()


class HubSpotClient:
    def __init__(self, api_key: str, owner_id: str):
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.owner_id = owner_id

    def _post(self, path: str, payload: dict) -> dict | None:
        try:
            resp = requests.post(
                f"{HUBSPOT_BASE}{path}",
                json=payload,
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            logger.error(f"HubSpot HTTP error {path}: {e} — {resp.text[:300]}")
            return None
        except Exception as e:
            logger.error(f"HubSpot error {path}: {e}")
            return None

    def _search(self, object_type: str, filter_property: str, value: str) -> str | None:
        """Return existing record ID if found, else None."""
        payload = {
            "filterGroups": [{"filters": [{"propertyName": filter_property, "operator": "EQ", "value": value}]}],
            "limit": 1,
        }
        try:
            resp = requests.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/{object_type}/search",
                json=payload,
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return results[0]["id"] if results else None
        except Exception:
            return None

    def _search_contact_by_name(self, first_name: str, last_name: str) -> str | None:
        """Return existing contact ID matching first + last name, else None."""
        if not first_name or not last_name:
            return None
        payload = {
            "filterGroups": [{
                "filters": [
                    {"propertyName": "firstname", "operator": "EQ", "value": first_name},
                    {"propertyName": "lastname", "operator": "EQ", "value": last_name},
                ]
            }],
            "limit": 1,
        }
        try:
            resp = requests.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/contacts/search",
                json=payload,
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return results[0]["id"] if results else None
        except Exception:
            return None

    def create_company(self, company: dict) -> str | None:
        name = company["company_name"]
        domain = company.get("domain", "")

        # If the email domain base doesn't match the company name, prefer the domain
        # e.g. company="Punch", domain="peyya.dev" → use "Peyya"
        if domain:
            domain_base = domain.split(".")[0].lower()
            if domain_base and domain_base not in name.lower() and name.lower() not in domain_base:
                name = domain_base.capitalize()

        # 1. Dedup by domain — most reliable, catches name variations
        if domain:
            existing = self._search("companies", "domain", domain)
            if existing:
                logger.info(f"Company already in HubSpot (domain match), skipping: {name}")
                return existing

        # 2. Dedup by exact name
        existing = self._search("companies", "name", name)
        if existing:
            logger.info(f"Company already in HubSpot (name match), skipping: {name}")
            return existing

        # 3. Dedup by normalised name — catches "All Gravy" vs "All Gravy ApS"
        normed = _norm(name)
        if normed != name.lower():
            existing = self._search("companies", "name", normed)
            if existing:
                logger.info(f"Company already in HubSpot (normalised name match), skipping: {name}")
                return existing

        description = (
            f"Source: TheHub.io\n"
            f"Hiring: {company['job_title']}\n"
            f"Job posting: {company.get('job_url', '')}"
        )
        properties = {
            "name": company["company_name"],
            "domain": domain,
            "website": company.get("company_website", "") or (f"https://{domain}" if domain else ""),
            "description": description,
            "lifecyclestage": "lead",
            "hubspot_owner_id": self.owner_id,
        }
        if domain.endswith(".dk"):
            properties["country"] = "Denmark"

        result = self._post("/crm/v3/objects/companies", {"properties": properties})
        if result:
            return result.get("id")
        return None

    def create_contact(self, contact: dict, company_id: str | None, company: dict | None = None) -> str | None:
        # 1. Dedup by email
        if contact.get("email"):
            existing = self._search("contacts", "email", contact["email"])
            if existing:
                logger.info(f"Contact already in HubSpot (email match), skipping: {contact['email']}")
                return existing

        # 2. Dedup by full name — catches same person re-submitted with different email
        existing = self._search_contact_by_name(contact.get("first_name", ""), contact.get("last_name", ""))
        if existing:
            logger.info(f"Contact already in HubSpot (name match), skipping: {contact.get('first_name')} {contact.get('last_name')}")
            return existing

        domain = company.get("domain", "") if company else ""
        properties = {
            "firstname": contact["first_name"],
            "lastname": contact["last_name"],
            "email": contact["email"],
            "jobtitle": contact["title"],
            "linkedin_bio": contact["linkedin_url"],
            "website": f"https://{domain}" if domain else "",
            "company": company["company_name"] if company else "",
            "nickname": company.get("job_url", "") if company else "",
            "lifecyclestage": "lead",
            "hubspot_owner_id": self.owner_id,
        }
        if domain.endswith(".dk"):
            properties["country"] = "Denmark"

        result = self._post("/crm/v3/objects/contacts", {"properties": properties})
        if not result:
            return None

        contact_id = result.get("id")
        if contact_id and company_id:
            self._associate_contact_company(contact_id, company_id)
        if contact_id and company:
            self._add_note(contact_id, company)

        return contact_id

    def _add_note(self, contact_id: str, company: dict):
        job_url = company.get("job_url", "")
        if not job_url:
            return
        body = (
            f"Source: TheHub.io\n"
            f"Job posting: {job_url}\n"
            f"Hiring for: {company.get('job_title', '')}\n"
            f"AI Score: {company.get('score', '').upper()} — {company.get('reason', '')}"
        )
        payload = {
            "properties": {
                "hs_note_body": body,
                "hs_timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            },
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
                }
            ],
        }
        try:
            resp = requests.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/notes",
                json=payload,
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Could not add note to contact {contact_id}: {e}")

    def _associate_contact_company(self, contact_id: str, company_id: str):
        payload = {
            "inputs": [
                {
                    "from": {"id": contact_id},
                    "to": {"id": company_id},
                    "type": "contact_to_company",
                }
            ]
        }
        try:
            resp = requests.post(
                f"{HUBSPOT_BASE}/crm/v3/associations/contacts/companies/batch/create",
                json=payload,
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Failed to associate contact {contact_id} → company {company_id}: {e}")

    def create_task(self, company: dict) -> str | None:
        contact = company.get("contact", {})
        domain = company.get("domain", "")

        if contact.get("found"):
            name = f"{contact['first_name']} {contact['last_name']}".strip()
            contact_line = f"Contact: {name} ({contact['title']}) — {contact['email']}\nLinkedIn: {contact['linkedin_url']}"
        else:
            contact_line = (
                f"⚠️ No contact found via Apollo. Manual lookup needed.\n"
                f"Apollo Search: https://app.apollo.io/#/companies?q_organization_domains[]={domain}"
            )

        body = (
            f"Source: TheHub.io\n"
            f"Hiring for: {company['job_title']}\n"
            f"AI Score: {company['score']} — {company['reason']}\n"
            f"Website: {company.get('company_website', 'N/A')}\n"
            f"Job Posting: {company['job_url']}\n\n"
            f"{contact_line}"
        )

        today_dt = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)
        today_ms = int(today_dt.timestamp() * 1000)

        payload = {
            "properties": {
                "hs_task_subject": f"New Lead: {company['company_name']} is hiring a {company['job_title']}",
                "hs_task_body": body,
                "hs_task_status": "NOT_STARTED",
                "hs_task_type": "TODO",
                "hs_timestamp": today_ms,
                "hubspot_owner_id": self.owner_id,
            }
        }
        result = self._post("/crm/v3/objects/tasks", payload)
        if result:
            return result.get("id")
        return None


def push_to_hubspot(companies: list[dict], api_key: str, owner_id: str) -> int:
    client = HubSpotClient(api_key, owner_id)
    pushed = 0

    for company in companies:
        try:
            company_id = client.create_company(company)
            if not company_id:
                raise RuntimeError("Failed to create company record")

            contact = company.get("contact", {})
            if contact.get("found"):
                client.create_contact(contact, company_id, company=company)

            pushed += 1
            logger.info(f"Pushed to HubSpot: {company['company_name']}")

        except Exception as e:
            logger.error(f"HubSpot push failed for {company['company_name']}: {e}")
            _log_failure(company["company_name"], str(e))

    return pushed


def dry_run_preview(company: dict):
    contact = company.get("contact", {})
    domain = company.get("domain", "")

    if contact and contact.get("found"):
        name = f"{contact['first_name']} {contact['last_name']}".strip()
        contact_line = (
            f"  Contact  : {name}\n"
            f"  Position : {contact['title'] or 'N/A'}\n"
            f"  Email    : {contact['email'] or 'N/A'}"
        )
    else:
        contact_line = (
            f"  Contact  : Not found\n"
            f"  Position : —\n"
            f"  Email    : —"
        )

    print(
        f"Company  : {company['company_name']}\n"
        f"Hiring   : {company['job_title']}\n"
        f"Score    : {company['score'].upper()} — {company['reason']}\n"
        f"Job URL  : {company['job_url']}\n"
        f"{contact_line}\n"
        f"{'-' * 50}"
    )


def _log_failure(company_name: str, reason: str):
    with open(FAILED_LOG, "a") as f:
        f.write(f"{date.today()} | {company_name} | {reason}\n")
