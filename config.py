import os
import sys
from pathlib import Path
from dotenv import load_dotenv

CONFIG_FILE = Path(__file__).parent / "config.env"


def run_onboarding(dry_run: bool = False):
    print("\nWelcome to TheHub Lead Automation.")
    print("Let's get you set up. You'll only need to do this once.\n")

    if dry_run:
        print("(Dry-run mode: HubSpot and Hunter keys are optional — press Enter to skip)\n")

    gemini_key = input("Enter your Gemini API key: ").strip()

    hunter_key = ""
    if not dry_run:
        hunter_key = input("Enter your Hunter.io API key: ").strip()
    else:
        hunter_key = input("Enter your Hunter.io API key (or press Enter to skip): ").strip()

    hubspot_key = ""
    owner_id = ""
    if not dry_run:
        hubspot_key = input("Enter your HubSpot API key: ").strip()
        owner_id = input("Enter Jan's HubSpot Owner ID [50983759]: ").strip() or "50983759"

    with open(CONFIG_FILE, "w") as f:
        f.write(f"GEMINI_API_KEY={gemini_key}\n")
        f.write(f"HUNTER_API_KEY={hunter_key}\n")
        f.write(f"HUBSPOT_API_KEY={hubspot_key}\n")
        f.write(f"HUBSPOT_OWNER_ID={owner_id}\n")

    print("\nConfig saved to config.env. You're all set!\n")


def load_config(dry_run: bool = False):
    if not CONFIG_FILE.exists():
        run_onboarding(dry_run=dry_run)

    load_dotenv(CONFIG_FILE)

    always_required = ["GEMINI_API_KEY"]
    live_required = ["HUBSPOT_API_KEY", "GEMINI_API_KEY"]

    required = always_required if dry_run else live_required
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"ERROR: Missing config values: {', '.join(missing)}")
        print(f"Delete {CONFIG_FILE} and re-run to reconfigure.")
        sys.exit(1)

    return {
        "HUBSPOT_API_KEY": os.getenv("HUBSPOT_API_KEY", ""),
        "HUNTER_API_KEY": os.getenv("HUNTER_API_KEY", ""),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
        "HUBSPOT_OWNER_ID": os.getenv("HUBSPOT_OWNER_ID", "50983759"),
    }
