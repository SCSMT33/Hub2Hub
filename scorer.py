import json
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are a business development assistant. Given this company and job listing, assess whether this company is a good prospect for a software development outsourcing firm.

Company: {company_name}
Hiring for: {job_title}
Description: {job_snippet}

Respond with JSON only:
{{
  "score": "high" | "medium" | "low",
  "reason": "one sentence explanation"
}}"""


def init_gemini(api_key: str):
    genai.configure(api_key=api_key)


def score_company(company: dict) -> dict | None:
    prompt = PROMPT_TEMPLATE.format(
        company_name=company["company_name"],
        job_title=company["job_title"],
        job_snippet=company["description"],
    )

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        raw = response.text.strip()

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
    init_gemini(gemini_api_key)
    qualified = []

    for company in companies:
        result = score_company(company)
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
