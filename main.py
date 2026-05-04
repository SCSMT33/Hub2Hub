import logging
import sys
from datetime import datetime

import schedule
import time

from config import load_config
from scraper import scrape_jobs
from scorer import filter_and_score
from apollo import enrich_contacts
from hubspot import push_to_hubspot, dry_run_preview, HubSpotClient

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


def run_pipeline(cfg: dict, dry_run: bool = False):
    print(f"{ts()} Starting TheHub scrape...")

    # dry-run ignores the seen-today filter so you can re-test the same companies
    companies = scrape_jobs(ignore_seen=dry_run)
    print(f"{ts()} Found {len(companies)} companies")

    if not companies:
        print(f"{ts()} Nothing new to process. Next run at 08:00 tomorrow.")
        return

    qualified = filter_and_score(companies, cfg["GEMINI_API_KEY"])
    print(f"{ts()} {len(qualified)} passed AI scoring")

    enriched = enrich_contacts(qualified, dry_run=dry_run)
    contacts_found = sum(1 for c in enriched if c.get("contact", {}).get("found"))
    print(f"{ts()} {contacts_found} contacts found on job pages")

    if dry_run:
        # Push the first company that has a real contact found
        target = next((c for c in enriched if c.get("contact", {}).get("found")), None)
        if target and cfg.get("HUBSPOT_API_KEY"):
            print(f"\n{ts()} Pushing to HubSpot: {target['company_name']} — {target['contact']['email']}")
            pushed = push_to_hubspot([target], cfg["HUBSPOT_API_KEY"], cfg["HUBSPOT_OWNER_ID"])
            if pushed:
                print(f"{ts()} HubSpot push OK — check CRM for {target['company_name']}")
            else:
                print(f"{ts()} HubSpot push FAILED — check logs above")
        elif not target:
            print(f"\n{ts()} No contacts found on any job page — nothing pushed to HubSpot")

        print(f"\n{ts()} DRY RUN — full lead preview:\n")
        print("=" * 60)
        for company in enriched:
            dry_run_preview(company)
        print("=" * 60)
        print(f"\n{ts()} Dry run complete. {len(enriched)} leads ready.")
        print(f"{ts()} Run without --dry-run to push all leads to HubSpot.")
    else:
        pushed = push_to_hubspot(enriched, cfg["HUBSPOT_API_KEY"], cfg["HUBSPOT_OWNER_ID"])
        print(f"{ts()} {pushed} records pushed to HubSpot")
        print(f"{ts()} Done. Next run at 08:00 tomorrow.")


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = load_config(dry_run=dry_run)

    if "--run-now" in sys.argv or dry_run:
        if dry_run and cfg.get("HUBSPOT_API_KEY"):
            test_hubspot_connection(cfg)
        run_pipeline(cfg, dry_run=dry_run)
        return

    # Schedule daily at 08:00
    schedule.every().day.at("08:00").do(run_pipeline, cfg=cfg)

    print(f"{ts()} Scheduler started. Pipeline will run daily at 08:00.")
    print(f"{ts()} Use --run-now to trigger immediately.")
    print(f"{ts()} Use --dry-run to preview without pushing to HubSpot.")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
