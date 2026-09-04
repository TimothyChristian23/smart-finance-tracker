"""Optional OpenAI-assisted transaction category suggestions."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from app.categorization import (
    CATEGORY_OPTIONS,
    clean_merchant_description,
    confidence_label,
    explain_category_source,
)

load_dotenv()
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DEFAULT_AI_CATEGORY_MODEL = "gpt-5-nano"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
AI_CATEGORY_SOURCE = "ai_suggestion"


class AICategorizationNotConfigured(RuntimeError):
    """Raised when AI categorization is requested without an API key."""


class AICategorizationError(RuntimeError):
    """Raised when an AI categorization request fails or returns invalid data."""


def ai_categorization_status() -> dict:
    """Return whether OpenAI-backed categorization can be used."""
    model = ai_category_model()
    enabled = bool(openai_api_key())
    return {
        "enabled": enabled,
        "model": model,
        "message": (
            f"Ready to use {model} for category suggestions."
            if enabled
            else "Set OPENAI_API_KEY to enable AI category suggestions."
        ),
    }


def suggest_category_reviews_with_ai(review_items: list[dict]) -> list[dict]:
    """Ask OpenAI for category suggestions for existing review queue items."""
    api_key = openai_api_key()
    if not api_key:
        raise AICategorizationNotConfigured("AI categorization is disabled. Set OPENAI_API_KEY to enable it.")

    candidates = _candidate_transactions(review_items)
    if not candidates:
        return []

    categories = expense_categories()
    payload = _category_suggestion_payload(candidates, categories, ai_category_model())
    response = _post_openai_response(payload, api_key=api_key, timeout=20)
    suggestions = _parse_response_suggestions(response)
    by_transaction_id = _validated_suggestions(suggestions, candidates, categories)

    ai_items = []
    for item in review_items:
        transaction_id = item["transaction"]["id"]
        suggestion = by_transaction_id.get(transaction_id)
        if not suggestion:
            continue
        ai_items.append(_review_item_from_ai(item, suggestion))
    return ai_items


def openai_api_key() -> str:
    """Read the OpenAI API key from the server environment."""
    return os.getenv("OPENAI_API_KEY", "").strip()


def ai_category_model() -> str:
    """Read the model used for category suggestions."""
    return (
        os.getenv("OPENAI_CATEGORY_MODEL", os.getenv("OPENAI_MODEL", DEFAULT_AI_CATEGORY_MODEL)).strip()
        or DEFAULT_AI_CATEGORY_MODEL
    )


def expense_categories() -> list[str]:
    """Return categories the AI can choose for expense transactions."""
    return [category for category in CATEGORY_OPTIONS if category != "Income"]


def _candidate_transactions(review_items: list[dict]) -> list[dict]:
    candidates = []
    for item in review_items:
        transaction = item["transaction"]
        if transaction["amount"] >= 0 or transaction.get("is_split"):
            continue
        candidates.append({
            "id": transaction["id"],
            "date": transaction["date"],
            "description": transaction["description"],
            "merchant": clean_merchant_description(transaction["description"]),
            "amount": round(abs(transaction["amount"]), 2),
            "account_name": transaction.get("account_name") or "",
            "current_category": item["current_category"],
            "local_suggested_category": item["suggested_category"],
            "local_reason": item["reason"],
        })
    return candidates


def _category_suggestion_payload(candidates: list[dict], categories: list[str], model: str) -> dict:
    return {
        "model": model,
        "store": False,
        "max_output_tokens": 700,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You categorize personal finance expense transactions. "
                            "Choose exactly one allowed category for each transaction. "
                            "Use Other when the merchant is too ambiguous. "
                            "Do not return income categories for expenses."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "allowed_categories": categories,
                                "transactions": candidates,
                            },
                            separators=(",", ":"),
                        ),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "finance_category_suggestions",
                "description": "Category suggestions for personal finance expense transactions.",
                "strict": True,
                "schema": _category_suggestion_schema(categories),
            }
        },
    }


def _category_suggestion_schema(categories: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "suggestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "transaction_id": {"type": "integer"},
                        "category": {"type": "string", "enum": categories},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                        "matched_terms": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["transaction_id", "category", "confidence", "reason", "matched_terms"],
                },
            }
        },
        "required": ["suggestions"],
    }


def _post_openai_response(payload: dict, api_key: str, timeout: int) -> dict:
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AICategorizationError(_openai_error_message(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise AICategorizationError(f"Could not reach OpenAI API: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise AICategorizationError("OpenAI API returned invalid JSON.") from exc


def _openai_error_message(status_code: int, detail: str) -> str:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        payload = {}
    message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
    return f"OpenAI API returned {status_code}: {message or 'request failed.'}"


def _parse_response_suggestions(response: dict) -> list[dict]:
    text = _response_output_text(response)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AICategorizationError("OpenAI category response was not valid JSON.") from exc

    suggestions = payload.get("suggestions")
    if not isinstance(suggestions, list):
        raise AICategorizationError("OpenAI category response did not include suggestions.")
    return suggestions


def _response_output_text(response: dict) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]

    parts = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
    text = "".join(parts).strip()
    if not text:
        raise AICategorizationError("OpenAI category response did not include output text.")
    return text


def _validated_suggestions(suggestions: list[dict], candidates: list[dict], categories: list[str]) -> dict[int, dict]:
    candidate_ids = {item["id"] for item in candidates}
    valid_categories = set(categories)
    validated = {}
    for raw in suggestions:
        if not isinstance(raw, dict):
            continue
        try:
            transaction_id = int(raw["transaction_id"])
            category = str(raw["category"])
            confidence = float(raw["confidence"])
        except (KeyError, TypeError, ValueError):
            continue
        if transaction_id not in candidate_ids or category not in valid_categories:
            continue

        reason = " ".join(str(raw.get("reason", "")).split())[:180] or "AI suggested this category from the merchant text."
        matched_terms = [
            " ".join(str(term).split())[:60]
            for term in raw.get("matched_terms", [])
            if " ".join(str(term).split())
        ][:5]
        validated[transaction_id] = {
            "category": category,
            "confidence": max(0, min(confidence, 1)),
            "reason": reason,
            "matched_terms": matched_terms,
        }
    return validated


def _review_item_from_ai(item: dict, suggestion: dict) -> dict:
    confidence = round(suggestion["confidence"], 2)
    suggested_category = suggestion["category"]
    current_category = item["current_category"]
    return {
        "transaction": item["transaction"],
        "current_category": current_category,
        "suggested_category": suggested_category,
        "confidence": confidence,
        "confidence_label": confidence_label(confidence),
        "category_source": AI_CATEGORY_SOURCE,
        "category_source_label": explain_category_source(AI_CATEGORY_SOURCE),
        "matched_terms": suggestion["matched_terms"],
        "reason": suggestion["reason"],
        "action": "update" if suggested_category != current_category else "review",
    }
