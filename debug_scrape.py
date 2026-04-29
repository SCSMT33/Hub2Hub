"""Run this once to dump TheHub's rendered HTML so we can find the right selectors."""
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
    print("Page loaded. Waiting 3 extra seconds for JS...")
    page.wait_for_timeout(3000)

    html = page.content()
    with open("debug_page.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Saved {len(html)} bytes to debug_page.html")

    # Print all unique href patterns to spot job links
    links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    job_links = [l for l in links if l and "job" in l.lower()]
    print(f"\nFound {len(job_links)} links containing 'job':")
    for l in job_links[:20]:
        print(" ", l)

    browser.close()
