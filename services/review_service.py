"""
review_service.py — yt-video

AI compliance review to score scripts before publishing.
Score ≥ 80 → public upload; < 80 → private.

Same logic as v1; imports updated for standalone yt-video package.
"""

import json
import re
from openai import OpenAI, RateLimitError, AuthenticationError
from config import config
from logger import logger
from services.cost_service import record_llm

_client = OpenAI(api_key=config.OPENAI_API_KEY)


def review_script(script: str) -> str:
    """
    Submit script for AI compliance and quality review.
    Returns: review text containing a score 0–100.
    """
    logger.info("Submitting script for AI compliance review...")

    prompt = f"""
You are a YouTube compliance and quality reviewer.

Evaluate this script:
- Clarity (0-100)
- Risky financial claims
- Engagement level
- Copyright risk
- Compliance issues

Provide:
1. Overall Score (0-100)
2. Short explanation
3. Safe to publish? (Yes/No)

Script:
{script}
"""

    try:
        response = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
    except RateLimitError:
        logger.error("OpenAI quota exceeded. Add credits at https://platform.openai.com/settings/billing")
        raise
    except AuthenticationError:
        logger.error("Invalid OpenAI API key. Check OPENAI_API_KEY in .env")
        raise

    review = response.choices[0].message.content
    if response.usage:
        record_llm("yt_video", "review", "gpt-4o-mini",
                   response.usage.prompt_tokens, response.usage.completion_tokens)
    logger.info("Review received from AI")
    logger.debug(f"Review text: {review}")
    return review


def extract_score(review_text: str) -> int:
    """Extract the overall score from the review text.

    Handles formats like:
      - "Overall Score (0-100): 85"
      - "Overall Score: **85**/100"
      - "Score: 72"
      - "85/100"
    """
    # "Overall Score (0-100): 85" — skip the range hint "(0-100)" before capturing
    match = re.search(
        r"[Oo]verall\s+[Ss]core\s*(?:\([^)]*\))?\s*[:\-]\s*\*{0,2}(\d{1,3})\*{0,2}",
        review_text,
    )
    if not match:
        # "Score: 85" or "Score: **85**/100"
        match = re.search(
            r"[Ss]core\s*(?:\([^)]*\))?\s*[:\-]\s*\*{0,2}(\d{1,3})\*{0,2}",
            review_text,
        )
    if not match:
        # "85/100" anywhere in the text
        match = re.search(r"\b(\d{1,3})\s*/\s*100", review_text)
    score = int(match.group(1)) if match else 0
    logger.info(f"Extracted review score: {score}")
    return score


def extract_safety(review_text: str) -> str:
    """Extract 'Yes' or 'No' from 'Safe to publish?' in review text."""
    match = re.search(r"Safe to publish\?\s*(Yes|No)", review_text, re.IGNORECASE)
    if not match:
        # Tolerate "Safe to publish: Yes" without the question mark
        match = re.search(r"Safe to publish\s*[:\-]\s*(Yes|No)", review_text, re.IGNORECASE)
    safety = match.group(1).capitalize() if match else "No"
    logger.info(f"Extracted safety status: {safety}")
    return safety


def log_review(topic: str, score: int, review_text: str) -> None:
    """Append review result to output/review_log.json (newline-delimited JSON)."""
    import os
    os.makedirs("output", exist_ok=True)
    data = {"topic": topic, "score": score, "review": review_text}
    with open("output/review_log.json", "a") as f:
        f.write(json.dumps(data) + "\n")
    logger.debug(f"Review logged for topic: {topic} (score={score})")
