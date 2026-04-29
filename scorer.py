import json
import logging
import requests

logger = logging.getLogger(__name__)

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)

PROMPT_TEMPLATE = """You are a business development assistant. Given this company and job listing, assess whether this company is a good prospect for a software development outsourcing firm.

Company: {company_name}
Hiring for: {job_title}
Description: {job_snippet}

Respond with JSON only:
{{
  "score": "high" | "medium" | "low",
  "reason": "one sentence explanation"
}}"""


def score_company(company: dict, api_key: str) -> dict | None:
    prompt = PROMPT_TEMPLATE.format(
        company_name=company["company_name"],
        job_title=company["job_title"],
        job_snippet=company["description"],
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }

    try:
        resp = requests.post(
            GEMINI_URL,
            params={"key": api_key},
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

        # Strip markdown code fences if present
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
    qualified = []

    for company in companies:
        result = score_company(company, gemini_api_key)
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
