import os
import sys
from pathlib import Path
from dotenv import load_dotenv

CONFIG_FILE = Path(__file__).parent / "config.env"


def run_onboarding():
    print("\nWelcome to TheHub Lead Automation.")
    print("Let's get you set up. You'll only need to do this once.\n")

    hubspot_key = input("Enter your HubSpot API key: ").strip()
    apollo_key = input("Enter your Apollo API key: ").strip()
    gemini_key = input("Enter your Gemini API key: ").strip()
    owner_id = input("Enter Jan's HubSpot Owner ID (the number from HubSpot): ").strip()

    with open(CONFIG_FILE, "w") as f:
        f.write(f"HUBSPOT_API_KEY={hubspot_key}\n")
        f.write(f"APOLLO_API_KEY={apollo_key}\n")
        f.write(f"GEMINI_API_KEY={gemini_key}\n")
        f.write(f"HUBSPOT_OWNER_ID={owner_id}\n")

    print("\nConfig saved to config.env. You're all set!\n")


def load_config():
    if not CONFIG_FILE.exists():
        run_onboarding()

    load_dotenv(CONFIG_FILE)

    required = ["HUBSPOT_API_KEY", "APOLLO_API_KEY", "GEMINI_API_KEY", "HUBSPOT_OWNER_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"ERROR: Missing config values: {', '.join(missing)}")
        print(f"Delete {CONFIG_FILE} and re-run to reconfigure.")
        sys.exit(1)

    return {
        "HUBSPOT_API_KEY": os.getenv("HUBSPOT_API_KEY"),
        "APOLLO_API_KEY": os.getenv("APOLLO_API_KEY"),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
        "HUBSPOT_OWNER_ID": os.getenv("HUBSPOT_OWNER_ID"),
    }
