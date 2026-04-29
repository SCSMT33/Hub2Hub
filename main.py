import logging
import sys
from datetime import datetime

import schedule
import time

from config import load_config
from scraper import scrape_jobs
from scorer import filter_and_score
from apollo import enrich_contacts
from hubspot import push_to_hubspot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def ts() -> str:
    return datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")


def run_pipeline(cfg: dict):
    print(f"{ts()} Starting TheHub scrape...")

    companies = scrape_jobs()
    print(f"{ts()} Found {len(companies)} companies")

    if not companies:
        print(f"{ts()} Nothing new to process. Next run at 08:00 tomorrow.")
        return

    qualified = filter_and_score(companies, cfg["GEMINI_API_KEY"])
    print(f"{ts()} {len(qualified)} passed AI scoring")

    enriched = enrich_contacts(qualified, cfg["APOLLO_API_KEY"])
    contacts_found = sum(1 for c in enriched if c.get("contact", {}).get("found"))
    print(f"{ts()} {contacts_found} contacts found via Apollo")

    pushed = push_to_hubspot(enriched, cfg["HUBSPOT_API_KEY"], cfg["HUBSPOT_OWNER_ID"])
    print(f"{ts()} {pushed} records pushed to HubSpot")
    print(f"{ts()} Done. Next run at 08:00 tomorrow.")


def main():
    cfg = load_config()

    # Allow --run-now flag to execute immediately without waiting for scheduler
    if "--run-now" in sys.argv:
        run_pipeline(cfg)
        return

    # Schedule daily at 08:00
    schedule.every().day.at("08:00").do(run_pipeline, cfg=cfg)

    print(f"{ts()} Scheduler started. Pipeline will run daily at 08:00.")
    print(f"{ts()} Use --run-now to trigger immediately.")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
