"""Run this to inspect the real card structure on TheHub."""
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

    # Walk up from the job link to find the card container with actual text
    cards = page.eval_on_selector_all(
        "a[href^='/jobs/']",
        """els => els
            .filter(el => el.getAttribute('href').length > 7 && !el.getAttribute('href').includes('?'))
            .slice(0, 3)
            .map(el => {
                // Walk up until we find a parent with meaningful text
                let node = el;
                for (let i = 0; i < 6; i++) {
                    node = node.parentElement;
                    if (!node) break;
                    const text = node.innerText.trim();
                    if (text.length > 20) {
                        return {
                            href: el.getAttribute('href'),
                            levels_up: i + 1,
                            text: text.slice(0, 300),
                            tag: node.tagName,
                            className: node.className.slice(0, 100)
                        };
                    }
                }
                return {href: el.getAttribute('href'), text: 'NOT FOUND', levels_up: -1};
            })
        """
    )

    print(f"\nFound {len(cards)} job links. Showing card structure:\n")
    for i, card in enumerate(cards):
        print(f"--- Card {i+1} ---")
        print(f"URL       : https://thehub.io{card['href']}")
        print(f"Levels up : {card['levels_up']} (tag: {card.get('tag','?')}, class: {card.get('className','?')})")
        print(f"Text      :\n{card['text']}\n")

    browser.close()
