import logging
from datetime import date, datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)
FAILED_LOG = Path(__file__).parent / "failed_log.txt"

HUBSPOT_BASE = "https://api.hubapi.com"


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

    def create_company(self, company: dict) -> str | None:
        note = f"Source: TheHub.io — hiring {company['job_title']}"
        payload = {
            "properties": {
                "name": company["company_name"],
                "domain": company.get("domain", ""),
                "website": company.get("company_website", ""),
                "description": note,
            }
        }
        result = self._post("/crm/v3/objects/companies", payload)
        if result:
            return result.get("id")
        return None

    def create_contact(self, contact: dict, company_id: str | None) -> str | None:
        payload = {
            "properties": {
                "firstname": contact["first_name"],
                "lastname": contact["last_name"],
                "email": contact["email"],
                "jobtitle": contact["title"],
                "linkedin_bio": contact["linkedin_url"],
            }
        }
        result = self._post("/crm/v3/objects/contacts", payload)
        if not result:
            return None

        contact_id = result.get("id")

        if contact_id and company_id:
            self._associate_contact_company(contact_id, company_id)

        return contact_id

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
                client.create_contact(contact, company_id)

            task_id = client.create_task(company)
            if not task_id:
                raise RuntimeError("Failed to create task")

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
        contact_line = f"  Contact : {name} ({contact['title']}) — {contact['email']}\n  LinkedIn: {contact['linkedin_url']}"
    else:
        contact_line = (
            f"  ⚠️  No contact found via Apollo. Manual lookup needed.\n"
            f"  Apollo : https://app.apollo.io/#/companies?q_organization_domains[]={domain}"
        )

    print(
        f"TASK SUBJECT : New Lead: {company['company_name']} is hiring a {company['job_title']}\n"
        f"  Score    : {company['score'].upper()} — {company['reason']}\n"
        f"  Website  : {company.get('company_website', 'N/A')}\n"
        f"  Job URL  : {company['job_url']}\n"
        f"{contact_line}\n"
    )


def _log_failure(company_name: str, reason: str):
    with open(FAILED_LOG, "a") as f:
        f.write(f"{date.today()} | {company_name} | {reason}\n")
