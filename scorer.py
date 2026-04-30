import json
import logging
import requests

logger = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com"
PREFERRED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-001",
]

PROMPT_TEMPLATE = """You are a business development assistant. Assess whether this company is a good prospect for a software development outsourcing firm.

Scoring rules:
- Score "high" or "medium" if the company is hiring for ANY technical or engineering role related to code development (e.g. backend, frontend, full-stack, mobile, DevOps, QA, SRE, CTO, VP Engineering, lead developer, software engineer, etc.)
- Score "low" and disqualify immediately if the company operates in the gaming industry (video games, game studios, esports, gambling)
- Score "low" if the role has no relation to software development
- A CTO hire is always high score — it signals major tech investment

Company: {company_name}
Hiring for: {job_title}
Description: {job_snippet}

Respond with JSON only:
{{
  "score": "high" | "medium" | "low",
  "reason": "one sentence explanation"
}}"""


def _detect_model(api_key: str) -> tuple[str, str] | tuple[None, None]:
    """Try each preferred model on v1 then v1beta. Return (model, api_version)."""
    test_payload = {"contents": [{"parts": [{"text": "Say OK"}]}]}

    for version in ("v1", "v1beta"):
        for model in PREFERRED_MODELS:
            url = f"{GEMINI_BASE}/{version}/models/{model}:generateContent"
            try:
                resp = requests.post(url, params={"key": api_key}, json=test_payload, timeout=10)
                if resp.status_code == 200:
                    logger.info(f"Using Gemini model: {model} (API {version})")
                    return model, version
                elif resp.status_code == 403:
                    logger.error(f"Gemini key rejected (403): {resp.text[:200]}")
                    return None, None
            except Exception as e:
                logger.debug(f"Error testing {model} on {version}: {e}")
                continue

    logger.error(
        "\n\n*** Could not connect to Gemini. All models failed.\n"
        "To fix:\n"
        "1. Go to https://aistudio.google.com/app/apikey\n"
        "2. Create a NEW API key\n"
        "3. Open config.env in Notepad and replace GEMINI_API_KEY with the new key\n"
        "4. Re-run the script ***\n"
    )
    return None, None


def score_company(company: dict, api_key: str, model: str, api_version: str) -> dict | None:
    prompt = PROMPT_TEMPLATE.format(
        company_name=company["company_name"],
        job_title=company["job_title"],
        job_snippet=company["description"],
    )

    url = f"{GEMINI_BASE}/{api_version}/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }

    try:
        resp = requests.post(url, params={"key": api_key}, json=payload, timeout=20)
        resp.raise_for_status()
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)
        score = result.get("score", "low").lower()
        reason = result.get("reason", "")

        if score not in ("high", "medium", "low"):
            score = "low"

        return {"score": score, "reason": reason}

    except json.JSONDecodeError as e:
        logger.error(f"Gemini returned invalid JSON for {company['company_name']}: {e}")
        return None
    except Exception as e:
        logger.error(f"Gemini error for {company['company_name']}: {e}")
        return None


def filter_and_score(companies: list[dict], gemini_api_key: str) -> list[dict]:
    model, api_version = _detect_model(gemini_api_key)
    if not model:
        return []

    qualified = []
    for company in companies:
        result = score_company(company, gemini_api_key, model, api_version)
        if result is None:
            logger.warning(f"Skipping {company['company_name']} — scoring failed.")
            continue

        if result["score"] == "low":
            logger.info(f"Dropped (low score): {company['company_name']}")
            continue

        company["score"] = result["score"]
        company["reason"] = result["reason"]
        qualified.append(company)
        logger.info(f"Qualified [{result['score']}]: {company['company_name']}")

    return qualified
