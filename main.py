import logging
import sys
from datetime import datetime

import schedule
import time

from config import load_config
from scraper import scrape_jobs
from scorer import filter_and_score
from apollo import enrich_contacts, enrich_one
from hubspot import push_to_hubspot, push_one_new, dry_run_preview, HubSpotClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def ts() -> str:
    return datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")


def test_hubspot_connection(cfg: dict):
    """Verify HubSpot connection by pinging the contacts API."""
    print(f"\n{ts()} Testing HubSpot connection...")
    if not cfg.get("HUBSPOT_API_KEY"):
        print(f"{ts()} ERROR: No HUBSPOT_API_KEY in config.env")
        return

    import requests
    resp = requests.get(
        "https://api.hubapi.com/crm/v3/objects/contacts?limit=1",
        headers={"Authorization": f"Bearer {cfg['HUBSPOT_API_KEY']}"},
        timeout=10,
    )
    if resp.status_code in (200, 204):
        print(f"{ts()} HubSpot connection OK")
    elif resp.status_code == 403:
        print(f"{ts()} HubSpot connection FAILED — API key rejected (check scopes)")
    elif resp.status_code == 401:
        print(f"{ts()} HubSpot connection FAILED — invalid API key")
    else:
        print(f"{ts()} HubSpot connection OK (status {resp.status_code})")


def run_test_one(cfg: dict, interactive: bool = True):
    """Scrape, score all, enrich + push only the first qualified lead."""
    print(f"\n{ts()} ONE LEAD MODE — will push 1 lead only.\n")
    print(f"{ts()} Starting TheHub scrape...")

    companies = scrape_jobs(ignore_seen=True)
    print(f"{ts()} Found {len(companies)} companies")

    if not companies:
        print(f"{ts()} No companies found on TheHub.")
        if interactive:
            input("\nPress Enter to close...")
        return

    qualified = filter_and_score(companies, cfg["GEMINI_API_KEY"])
    print(f"{ts()} {len(qualified)} passed AI scoring")

    if not qualified:
        print(f"{ts()} No qualified leads found.")
        if interactive:
            input("\nPress Enter to close...")
        return

    hunter_key = cfg.get("HUNTER_API_KEY", "")
    apollo_key = cfg.get("APOLLO_API_KEY", "")

    print(f"\n{ts()} Searching for a new lead with a contact email...\n")
    pushed = False
    for company in qualified:
        name = company["company_name"]
        print(f"{ts()} Trying: {name}...")

        enrich_one(company, hunter_api_key=hunter_key, apollo_api_key=apollo_key)

        contact = company.get("contact", {})
        if not contact.get("found"):
            time.sleep(1)
            continue

        # Attempt push — push_one_new returns 'exists' if already in HubSpot
        result = push_one_new(company, cfg["HUBSPOT_API_KEY"], cfg["HUBSPOT_OWNER_ID"])
        if result == "exists":
            print(f"{ts()} Skipping {name} — contact already in HubSpot.")
            time.sleep(1)
            continue
        if result == "error":
            print(f"{ts()} Push failed for {name}, trying next lead.")
            time.sleep(1)
            continue

        # Successfully pushed a new lead
        print(f"\n{ts()} New lead pushed to HubSpot:")
        print(f"  Company : {name}  [{company['score'].upper()}]")
        print(f"  Hiring  : {company['job_title']}")
        print(f"  Contact : {contact['first_name']} {contact['last_name']} (via {contact.get('source', '?')})")
        print(f"  Title   : {contact['title'] or '—'}")
        print(f"  Email   : {contact['email']}")
        pushed = True
        break

    if not pushed:
        print(f"\n{ts()} No new leads found — all contacts already in HubSpot or no emails found.")

    if interactive:
        input("\nPress Enter to close...")


def run_pipeline(cfg: dict, dry_run: bool = False):
    """Used by the scheduler (fully automated) and --dry-run (preview only)."""
    print(f"{ts()} Starting TheHub scrape...")

    companies = scrape_jobs(ignore_seen=dry_run)
    print(f"{ts()} Found {len(companies)} companies")

    if not companies:
        print(f"{ts()} Nothing new to process. Next run at 08:00 tomorrow.")
        return

    qualified = filter_and_score(companies, cfg["GEMINI_API_KEY"])
    print(f"{ts()} {len(qualified)} passed AI scoring")

    enriched = enrich_contacts(
        qualified,
        hunter_api_key=cfg.get("HUNTER_API_KEY", ""),
        apollo_api_key=cfg.get("APOLLO_API_KEY", ""),
        dry_run=dry_run,
    )
    contacts_found = sum(1 for c in enriched if c.get("contact", {}).get("found"))
    print(f"{ts()} {contacts_found} contacts found")

    if dry_run:
        print(f"\n{ts()} DRY RUN — lead preview:\n")
        print("=" * 60)
        for company in enriched:
            dry_run_preview(company)
        print("=" * 60)
        print(f"\n{ts()} Dry run complete. {len(enriched)} leads ready.")
        print(f"{ts()} Use --run-now for the interactive push workflow.")
    else:
        pushed = push_to_hubspot(enriched, cfg["HUBSPOT_API_KEY"], cfg["HUBSPOT_OWNER_ID"])
        print(f"{ts()} {pushed} records pushed to HubSpot")
        print(f"{ts()} Done. Next run at 08:00 tomorrow.")


def run_interactive(cfg: dict):
    """Two-step workflow: scrape → show numbered list → user selects → push."""
    print(f"\n{ts()} Starting TheHub scrape...")

    companies = scrape_jobs(ignore_seen=True)
    print(f"{ts()} Found {len(companies)} companies")

    if not companies:
        print(f"{ts()} Nothing new today.")
        return

    qualified = filter_and_score(companies, cfg["GEMINI_API_KEY"])
    print(f"{ts()} {len(qualified)} passed AI scoring\n")

    enriched = enrich_contacts(
        qualified,
        hunter_api_key=cfg.get("HUNTER_API_KEY", ""),
        apollo_api_key=cfg.get("APOLLO_API_KEY", ""),
    )
    contacts_found = sum(1 for c in enriched if c.get("contact", {}).get("found"))

    print(f"\n{'=' * 60}")
    print(f"  LEADS READY: {len(enriched)} qualified  |  {contacts_found} with contacts")
    print(f"{'=' * 60}\n")

    for i, company in enumerate(enriched, 1):
        contact = company.get("contact", {})
        if contact.get("found"):
            name = f"{contact['first_name']} {contact['last_name']}".strip()
            contact_block = (
                f"  Contact : {name or '—'}\n"
                f"  Title   : {contact['title'] or '—'}\n"
                f"  Email   : {contact['email']}"
            )
        else:
            contact_block = "  Contact : Not found"

        print(
            f"[{i}] {company['company_name']}  [{company['score'].upper()}]\n"
            f"  Hiring  : {company['job_title']}\n"
            f"  Reason  : {company['reason']}\n"
            f"  Job URL : {company['job_url']}\n"
            f"{contact_block}\n"
        )

    print(f"{'=' * 60}")
    print("Enter numbers to push (e.g. '1 3 5'), 'all', or press Enter to skip:")

    try:
        raw = input("> ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print(f"\n{ts()} Cancelled — nothing pushed.")
        return

    if not raw or raw in ("none", "skip", "n"):
        print(f"{ts()} Nothing pushed.")
        return

    if raw == "all":
        to_push = enriched
    else:
        try:
            indices = [int(x) - 1 for x in raw.split()]
            to_push = [enriched[i] for i in indices if 0 <= i < len(enriched)]
        except ValueError:
            print(f"{ts()} Could not parse selection — nothing pushed.")
            return

    if not to_push:
        print(f"{ts()} No valid leads selected.")
        return

    if not cfg.get("HUBSPOT_API_KEY"):
        print(f"{ts()} ERROR: No HUBSPOT_API_KEY in config.env — cannot push.")
        return

    print(f"\n{ts()} Pushing {len(to_push)} lead(s) to HubSpot...")
    pushed = push_to_hubspot(to_push, cfg["HUBSPOT_API_KEY"], cfg["HUBSPOT_OWNER_ID"])
    print(f"{ts()} Done — {pushed} record(s) pushed to HubSpot.")


def list_owners(cfg: dict):
    """Print all HubSpot owners so the user can find their correct owner ID."""
    import requests
    resp = requests.get(
        "https://api.hubapi.com/crm/v3/owners",
        headers={"Authorization": f"Bearer {cfg['HUBSPOT_API_KEY']}"},
        timeout=10,
    )
    if not resp.ok:
        print(f"Failed to fetch owners: {resp.status_code} {resp.text[:200]}")
        return
    owners = resp.json().get("results", [])
    print(f"\n{'='*50}")
    print("HubSpot Owners:")
    print(f"{'='*50}")
    for o in owners:
        print(f"  ID: {o['id']}  —  {o.get('firstName', '')} {o.get('lastName', '')}  ({o.get('email', '')})")
    print(f"{'='*50}")
    print("Add HUBSPOT_OWNER_ID=<your ID> to config.env\n")


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = load_config(dry_run=dry_run)

    if "--list-owners" in sys.argv:
        list_owners(cfg)
        return

    if dry_run:
        run_pipeline(cfg, dry_run=True)
        return

    if "--auto" in sys.argv:
        # Non-interactive mode for scheduled/CI runs — pushes all qualified leads
        run_pipeline(cfg, dry_run=False)
        return

    if "--test-one" in sys.argv:
        run_test_one(cfg, interactive=True)
        return

    if "--one" in sys.argv:
        run_test_one(cfg, interactive=False)
        return

    if "--run-now" in sys.argv:
        run_interactive(cfg)
        return

    # Schedule daily at 08:00
    schedule.every().day.at("08:00").do(run_pipeline, cfg=cfg)

    print(f"{ts()} Scheduler started. Pipeline will run daily at 08:00.")
    print(f"{ts()} Use --run-now for the interactive push workflow.")
    print(f"{ts()} Use --dry-run to preview leads without pushing.")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
