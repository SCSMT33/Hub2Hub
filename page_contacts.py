import re
import logging
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

HR_TITLES = [
    "recruiter", "recruitment", "talent", "hr ", "h.r.", "human resources",
    "people ops", "people partner", "hiring manager", "head of people",
]

PRIORITY_TITLES = [
    "cto", "chief technology", "vp engineering", "head of engineering",
    "vp technology", "co-founder", "cofounder", "founder", "ceo",
    "lead developer", "lead engineer", "engineering manager", "coo",
]


def _is_hr(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in HR_TITLES)


def _title_score(text: str) -> int:
    t = text.lower()
    for i, kw in enumerate(PRIORITY_TITLES):
        if kw in t:
            return len(PRIORITY_TITLES) - i
    return 0


def _extract_contacts_from_html(html: str) -> dict | None:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")

    emails = EMAIL_RE.findall(text)
    if not emails:
        return None

    # Filter out noreply/info/support addresses and HR emails
    candidates = []
    for email in emails:
        local = email.split("@")[0].lower()
        if any(x in local for x in ["noreply", "no-reply", "info", "support", "hello", "contact", "jobs", "career"]):
            continue

        # Find surrounding text (200 chars around email) to get name/title
        idx = text.find(email)
        context = text[max(0, idx - 200): idx + 200]

        if _is_hr(context):
            continue

        score = _title_score(context)
        candidates.append({"email": email, "context": context, "score": score})

    if not candidates:
        return None

    # Pick highest priority title match, or first candidate
    best = max(candidates, key=lambda c: c["score"])

    # Try to extract name from context — look for "Name, Title" patterns
    name_match = re.search(
        r"([A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)?)[,\s]+([A-Z][^\n,]{3,50})",
        best["context"]
    )
    first_name, last_name, title = "", "", ""
    if name_match:
        full_name = name_match.group(1).strip()
        parts = full_name.split()
        first_name = parts[0]
        last_name = " ".join(parts[1:])
        title = name_match.group(2).strip()[:80]

    return {
        "found": True,
        "first_name": first_name,
        "last_name": last_name,
        "email": best["email"],
        "title": title,
        "linkedin_url": "",
        "source": "job_page",
    }


def scrape_contact_from_job_page(job_url: str) -> dict | None:
    """Try requests first (fast), fall back to Playwright for JS-rendered pages."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}

    try:
        resp = requests.get(job_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            result = _extract_contacts_from_html(resp.text)
            if result:
                logger.info(f"Found contact on job page (static): {result['email']}")
                return result
    except Exception as e:
        logger.debug(f"Static fetch failed for {job_url}: {e}")

    # Fall back to Playwright for JS-rendered content
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(job_url, wait_until="networkidle", timeout=20000)
            page.wait_for_timeout(1500)
            html = page.content()
            browser.close()

        result = _extract_contacts_from_html(html)
        if result:
            logger.info(f"Found contact on job page (JS): {result['email']}")
        return result

    except Exception as e:
        logger.debug(f"Playwright fetch failed for {job_url}: {e}")
        return None
