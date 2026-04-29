"""Run this to verify the scraper can see job cards correctly."""
from playwright.sync_api import sync_playwright

URL = (
    "https://thehub.io/jobs"
    "?roles=backenddeveloper&roles=devops&roles=frontenddeveloper"
    "&roles=fullstackdeveloper&roles=mobiledevelopment&roles=uxuidesigner"
    "&roles=qualityassurance&countryCode=REMOTE&sorting=newJobs"
)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    print("Loading page...")
    page.goto(URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1500)

    cards = page.eval_on_selector_all(
        "a[href^='/jobs/']",
        """els => els
            .filter(el => el.getAttribute('href').length > 7 && !el.getAttribute('href').includes('?'))
            .map(el => ({href: el.getAttribute('href'), text: el.innerText}))
        """
    )

    print(f"\nFound {len(cards)} job cards.\n")
    for i, card in enumerate(cards[:5]):
        print(f"--- Card {i+1} ---")
        print(f"URL  : https://thehub.io{card['href']}")
        print(f"Text :\n{card['text']}\n")

    browser.close()
