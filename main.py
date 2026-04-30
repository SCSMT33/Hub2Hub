import logging
import sys
from datetime import datetime

import schedule
import time

from config import load_config
from scraper import scrape_jobs
from scorer import filter_and_score
from apollo import enrich_contacts
from hubspot import push_to_hubspot, dry_run_preview

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def ts() -> str:
    return datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")


def run_pipeline(cfg: dict, dry_run: bool = False):
    print(f"{ts()} Starting TheHub scrape...")

    companies = scrape_jobs()
    print(f"{ts()} Found {len(companies)} companies")

    if not companies:
        print(f"{ts()} Nothing new to process. Next run at 08:00 tomorrow.")
        return

    qualified = filter_and_score(companies, cfg["GEMINI_API_KEY"])
    print(f"{ts()} {len(qualified)} passed AI scoring")

    if cfg.get("HUNTER_API_KEY"):
        enriched = enrich_contacts(qualified, cfg["HUNTER_API_KEY"], dry_run=dry_run)
        contacts_found = sum(1 for c in enriched if c.get("contact", {}).get("found"))
        print(f"{ts()} {contacts_found} contacts found via Hunter.io")
    else:
        enriched = qualified
        print(f"{ts()} Apollo skipped (no key provided)")

    if dry_run:
        preview = enriched[:1]
        print(f"\n{ts()} DRY RUN — pushing 1 lead to HubSpot as a test:\n")
        print("=" * 60)
        dry_run_preview(preview[0])
        print("=" * 60)
        if cfg.get("HUBSPOT_API_KEY"):
            pushed = push_to_hubspot(preview, cfg["HUBSPOT_API_KEY"], cfg["HUBSPOT_OWNER_ID"])
            print(f"\n{ts()} {pushed} test record pushed to HubSpot.")
        else:
            print(f"\n{ts()} No HubSpot key — skipping push.")
        print(f"{ts()} Run without --dry-run to process all {len(enriched)} leads.")
    else:
        pushed = push_to_hubspot(enriched, cfg["HUBSPOT_API_KEY"], cfg["HUBSPOT_OWNER_ID"])
        print(f"{ts()} {pushed} records pushed to HubSpot")
        print(f"{ts()} Done. Next run at 08:00 tomorrow.")


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = load_config(dry_run=dry_run)

    if "--run-now" in sys.argv or dry_run:
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
