import json
import logging
import requests

logger = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
PREFERRED_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash",
    "gemini-1.5-pro-latest",
    "gemini-pro",
]

PROMPT_TEMPLATE = """You are a business development assistant. Given this company and job listing, assess whether this company is a good prospect for a software development outsourcing firm.

Company: {company_name}
Hiring for: {job_title}
Description: {job_snippet}

Respond with JSON only:
{{
  "score": "high" | "medium" | "low",
  "reason": "one sentence explanation"
}}"""


def _detect_model(api_key: str) -> str | None:
    """Query the API for available models, then pick the best flash/pro variant."""
    try:
        resp = requests.get(
            f"{GEMINI_BASE}",
            params={"key": api_key},
            timeout=10,
        )
        if resp.status_code == 403:
            logger.error(f"Gemini key rejected (403) — check your API key is correct.")
            return None

        resp.raise_for_status()
        available = [m["name"].replace("models/", "") for m in resp.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]

        logger.info(f"Available Gemini models: {available}")

        # Pick best match in preference order
        for preferred in PREFERRED_MODELS:
            if preferred in available:
                logger.info(f"Using Gemini model: {preferred}")
                return preferred

        # Fall back to first available model that supports generateContent
        if available:
            logger.info(f"Using first available model: {available[0]}")
            return available[0]

        logger.error("No usable Gemini models found for this API key.")
        return None

    except Exception as e:
        logger.error(f"Could not list Gemini models: {e}")
        return None


def score_company(company: dict, api_key: str, model: str) -> dict | None:
    prompt = PROMPT_TEMPLATE.format(
        company_name=company["company_name"],
        job_title=company["job_title"],
        job_snippet=company["description"],
    )

    url = f"{GEMINI_BASE}/{model}:generateContent"
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
    model = _detect_model(gemini_api_key)
    if not model:
        logger.error(
            "\n\n*** Gemini API key problem. To fix:\n"
            "1. Go to https://aistudio.google.com/app/apikey\n"
            "2. Create a NEW API key\n"
            "3. Open config.env in Notepad and replace GEMINI_API_KEY with the new key\n"
            "4. Re-run the script ***\n"
        )
        return []

    qualified = []
    for company in companies:
        result = score_company(company, gemini_api_key, model)
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
