import re
import logging
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

PRIORITY_TITLES = [
    "cto", "chief technology", "vp engineering", "head of engineering",
    "vp technology", "co-founder", "cofounder", "founder", "ceo",
    "lead developer", "lead engineer", "engineering manager", "coo",
]

_NOISE_DOMAINS = {
    # Social
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "tiktok.com",
    # Google / analytics
    "google.com", "googleapis.com", "gstatic.com", "googletagmanager.com",
    "doubleclick.net", "gravatar.com",
    # Cookie consent widgets
    "cookieinformation.com", "cookiebot.com", "onetrust.com", "trustarc.com",
    "cookiepro.com", "consentmanager.net",
    # Infra / CDN
    "apple.com", "cloudflare.com", "schema.org", "w3.org",
    "jsdelivr.net", "unpkg.com",
    # Dev platforms
    "github.com", "gitlab.com",
    # Business directories & ATS
    "crunchbase.com", "angel.co", "wellfound.com", "glassdoor.com",
    "indeed.com", "workable.com", "lever.co", "greenhouse.io",
    "ashbyhq.com", "recruitee.com", "teamtailor.com",
    # TheHub itself (blocks insights.thehub.io etc.)
    "thehub.io",
}


def _is_noise(domain: str) -> bool:
    return any(domain == nd or domain.endswith("." + nd) for nd in _NOISE_DOMAINS)


def _title_score(text: str) -> int:
    t = text.lower()
    for i, kw in enumerate(PRIORITY_TITLES):
        if kw in t:
            return len(PRIORITY_TITLES) - i
    return 0


def _extract_domain(html: str) -> str:
    """Return the first external non-noise domain found in page links."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if not href.startswith("http"):
            continue
        bare = urlparse(href).netloc.lower().removeprefix("www.")
        if bare and not _is_noise(bare):
            return bare
    return ""


def _extract_contact(html: str, company_domain: str) -> dict | None:
    """
    Return a contact whose email matches company_domain.
    If no match, return None — caller falls back to Hunter.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator=" ")

    candidates = []
    for email in EMAIL_RE.findall(text):
        email_domain = email.split("@")[-1].lower().removeprefix("www.")
        # Only accept emails that belong to the company itself
        if email_domain != company_domain:
            continue
        local = email.split("@")[0].lower()
        if any(x in local for x in ["noreply", "no-reply", "info", "support", "hello",
                                      "contact", "jobs", "career", "admin", "privacy"]):
            continue
        idx = text.find(email)
        context = text[max(0, idx - 200): idx + 200]
        candidates.append({"email": email, "context": context, "score": _title_score(context)})

    if not candidates:
        return None

    best = max(candidates, key=lambda c: c["score"])

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

    return {
        "found": True,
        "first_name": first_name,
        "last_name": last_name,
        "email": best["email"],
        "title": title,
        "linkedin_url": "",
        "source": "job_page",
    }


def _scrape_html(job_url: str) -> str:
    """TheHub is React/Next.js — always use Playwright so JS has rendered."""
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
    Visit job page, extract company domain, then look for an email
    that belongs to that domain. Returns (contact_or_None, domain).
    If no matching email is found, contact is None and Hunter.io takes over.
    """
    # TheHub is JS-rendered — try static first, fall back to Playwright
    html = _scrape_html(job_url)
    if not html:
        return None, ""

    domain = _extract_domain(html)
    if domain:
        logger.info(f"Domain from job page: {domain}")

    if not domain:
        return None, ""

    contact = _extract_contact(html, domain)
    if contact:
        logger.info(f"Contact from job page: {contact['email']}")
    else:
        logger.info(f"No matching email on job page — Hunter will use domain: {domain}")

    return contact, domain


def scrape_contact_from_job_page(job_url: str) -> dict | None:
    contact, _ = scrape_contact_and_domain_from_job_page(job_url)
    return contact
