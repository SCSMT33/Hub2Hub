import re
import logging
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

PRIORITY_TITLES = [
    "cto", "chief technology", "vp engineering", "head of engineering",
    "vp technology", "co-founder", "cofounder", "founder", "ceo",
    "lead developer", "lead engineer", "engineering manager", "coo",
]

# Domains that appear on job pages but belong to third-party services, not the company
_NOISE_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "tiktok.com",
    "microsoft.com", "microsoftonline.com", "live.com", "outlook.com",
    "mozilla.org", "firefox.com",
    "google.com", "googleapis.com", "gstatic.com", "googletagmanager.com",
    "doubleclick.net", "gravatar.com",
    "cookieinformation.com", "cookiebot.com", "onetrust.com", "trustarc.com",
    "cookiepro.com", "consentmanager.net",
    "intercom.com", "intercom.io", "hotjar.com", "segment.com",
    "stripe.com", "zendesk.com", "hubspot.com", "salesforce.com",
    "apple.com", "cloudflare.com", "schema.org", "w3.org",
    "jsdelivr.net", "unpkg.com", "github.com", "gitlab.com",
    "crunchbase.com", "angel.co", "wellfound.com", "glassdoor.com",
    "indeed.com", "workable.com", "lever.co", "greenhouse.io",
    "ashbyhq.com", "recruitee.com", "teamtailor.com",
    "thehub.io",
}

_GENERIC_LOCALS = {
    "noreply", "no-reply", "info", "support", "hello", "contact",
    "jobs", "career", "careers", "admin", "privacy", "legal", "team",
}


def _is_noise(domain: str) -> bool:
    return any(domain == nd or domain.endswith("." + nd) for nd in _NOISE_DOMAINS)


def _title_score(text: str) -> int:
    t = text.lower()
    for i, kw in enumerate(PRIORITY_TITLES):
        if kw in t:
            return len(PRIORITY_TITLES) - i
    return 0


def _name_from_email(local: str) -> tuple[str, str]:
    """
    Derive first/last name from email local part. Returns ("", "") when ambiguous.

    Rules:
    - Has separator (. _ -): use parts. Skip if first part is 1 char (initial).
      e.g. olli.kallioinen → Olli, Kallioinen ✅
           j.markus        → skip (j is an initial) ✅
    - No separator, single word:
      Skip if camelCase or starts with pattern suggesting initials (JMarkus).
      Skip if contains digits.
      Otherwise capitalise as first name.
      e.g. peneloppe → Peneloppe ✅
           JMarkus   → skip (uppercase after pos 0) ✅
           jmarkus   → skip (ambiguous — could be j+markus) ✅
    """
    parts = re.split(r"[._\-]", local)
    parts = [p for p in parts if p]

    if len(parts) >= 2:
        if len(parts[0]) <= 1:
            return "", ""  # initial before separator (j.markus)
        if any(c.isdigit() for c in parts[0]):
            return "", ""
        first = parts[0].capitalize()
        last = " ".join(p.capitalize() for p in parts[1:] if p)
        return first, last

    # Single word — only use if it unambiguously looks like one first name
    word = parts[0] if parts else ""
    if not word or len(word) < 3 or len(word) > 15:
        return "", ""
    if any(c.isdigit() for c in word):
        return "", ""
    # Uppercase letter after position 0 → camelCase or initials pattern → skip
    if any(c.isupper() for c in word[1:]):
        return "", ""
    # First char lowercase followed by what could be a surname (6+ chars) → skip
    if word[0].islower() and len(word) >= 6:
        return "", ""
    return word.capitalize(), ""
    """TheHub is React/Next.js — Playwright so JS has fully rendered."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(job_url, wait_until="networkidle", timeout=25000)
            page.wait_for_timeout(1500)
            html = page.content()
            browser.close()
        return html
    except Exception as e:
        logger.debug(f"Playwright fetch failed for {job_url}: {e}")
        return ""


def scrape_contact_and_domain_from_job_page(job_url: str) -> tuple[dict | None, str]:
    """
    Scan the job page for a real company email.
    Logic: find all emails → throw away noise-domain and generic-local ones
           → pick the best (prefer priority titles in surrounding text).
    Domain is derived from the winning email, so link-based domain
    detection is no longer needed and can't be fooled by third-party links.
    """
    html = _scrape_html(job_url)
    if not html:
        return None, ""

    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")

    candidates = []
    for email in EMAIL_RE.findall(text):
        domain = email.split("@")[-1].lower().removeprefix("www.")
        local = email.split("@")[0].lower()

        if _is_noise(domain):
            continue
        if any(g in local for g in _GENERIC_LOCALS):
            continue

        idx = text.find(email)
        context = text[max(0, idx - 200): idx + 200]
        candidates.append({"email": email, "domain": domain, "context": context,
                            "score": _title_score(context)})

    if not candidates:
        logger.info(f"No company email found on job page: {job_url}")
        return None, ""

    best = max(candidates, key=lambda c: c["score"])
    company_domain = best["domain"]

    # Try to extract name + title from surrounding text (e.g. "Olli Kallioinen, Founder & CEO")
    name_match = re.search(
        r"([A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)?)[,\s]+([A-Z][\w\s&/]{2,45})",
        best["context"]
    )
    first_name, last_name, title = "", "", ""
    if name_match:
        parts = name_match.group(1).strip().split()
        first_name = parts[0]
        last_name = " ".join(parts[1:])
        title = re.split(r"\s{2,}|[|•·–—]", name_match.group(2))[0].strip()[:60]

    # Fall back to parsing the email local part for name clues
    if not first_name:
        local_part = best["email"].split("@")[0]
        first_name, last_name = _name_from_email(local_part)

    contact = {
        "found": True,
        "first_name": first_name,
        "last_name": last_name,
        "email": best["email"],
        "title": title,
        "linkedin_url": "",
        "source": "job_page",
    }
    logger.info(f"Contact from job page: {best['email']}")
    return contact, company_domain


def scrape_contact_from_job_page(job_url: str) -> dict | None:
    contact, _ = scrape_contact_and_domain_from_job_page(job_url)
    return contact
