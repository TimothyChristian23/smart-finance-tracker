"""Explainable transaction categorization rules."""
from __future__ import annotations

import re

CATEGORY_KEYWORDS = {
    "Income": ["payroll", "salary", "direct deposit", "deposit"],
    "Housing": ["rent", "apartment", "mortgage"],
    "Food & Grocery": [
        "grocery",
        "trader joe",
        "whole foods",
        "safeway",
        "costco",
        "kroger",
        "aldi",
        "instacart",
        "farmers market",
    ],
    "Dining": [
        "restaurant",
        "cafe",
        "coffee",
        "chipotle",
        "doordash",
        "uber eats",
        "starbucks",
        "mcdonald",
        "panera",
    ],
    "Transport": [
        "uber",
        "lyft",
        "transit",
        "metro",
        "fuel",
        "gas",
        "chevron",
        "shell",
        "exxon",
        "parking",
        "toll",
    ],
    "Shopping": ["amazon", "target", "walmart", "electronics", "best buy", "home depot"],
    "Utilities": ["electric", "utility", "water", "internet", "phone", "comcast", "xfinity"],
    "Health": ["pharmacy", "cvs", "doctor", "clinic", "insurance", "dentist"],
    "Subscriptions": ["netflix", "spotify", "hulu", "subscription", "apple.com/bill", "youtube premium"],
}

CATEGORY_OPTIONS = [*CATEGORY_KEYWORDS.keys(), "Other"]

REVIEW_CATEGORY_KEYWORDS = {
    "Health": ["gym", "fitness", "wellness", "therapy", "medical", "vision"],
    "Subscriptions": ["membership", "plan", "premium", "plus", "monthly"],
    "Utilities": ["verizon", "at&t", "tmobile", "mobile"],
    "Transport": ["parking", "toll", "train", "bus"],
    "Dining": ["bakery", "pizza", "taco", "sushi", "deli"],
    "Shopping": ["shop", "store", "retail", "marketplace", "bookstore"],
}

CATEGORY_SOURCE_LABELS = {
    "amount_direction": "Amount direction",
    "category_signals": "Category signals",
    "fallback": "Needs review",
    "keyword_rule": "Merchant keyword",
    "saved_rule": "Saved merchant rule",
}

QUESTION_CATEGORY_KEYWORDS = {
    "food": ["Food & Grocery", "Dining"],
    "groceries": ["Food & Grocery"],
    "grocery": ["Food & Grocery"],
    "restaurants": ["Dining"],
    "dining": ["Dining"],
    "rent": ["Housing"],
    "housing": ["Housing"],
    "transport": ["Transport"],
    "transportation": ["Transport"],
    "shopping": ["Shopping"],
    "utilities": ["Utilities"],
    "subscriptions": ["Subscriptions"],
    "health": ["Health"],
}


def categorize_transaction(description: str, amount_cents: int) -> str:
    """Assign a starter category from high-confidence merchant text and amount direction."""
    normalized = normalize_text(description)

    if amount_cents > 0:
        return "Income"

    match = first_category_keyword_match(normalized, expense_category_keywords())
    if match:
        category, _keyword = match
        return category

    return "Other"


def first_category_keyword_match(
    normalized_description: str,
    keywords_by_category: dict[str, list[str]],
) -> tuple[str, str] | None:
    """Return the first category/keyword pair that appears in normalized merchant text."""
    for category, keywords in keywords_by_category.items():
        for keyword in keywords:
            if keyword_matches(normalized_description, keyword):
                return category, keyword
    return None


def expense_category_keywords() -> dict[str, list[str]]:
    """Return category keywords that should apply to spending transactions."""
    return {
        category: keywords
        for category, keywords in CATEGORY_KEYWORDS.items()
        if category != "Income"
    }


def keyword_matches(normalized_description: str, keyword: str) -> bool:
    """Match short generic terms by word boundary and merchant phrases by substring."""
    normalized_keyword = normalize_text(keyword)
    if " " in normalized_keyword or "." in normalized_keyword or "&" in normalized_keyword:
        return normalized_keyword in normalized_description
    return bool(re.search(rf"\b{re.escape(normalized_keyword)}\b", normalized_description))


def category_signal_matches(normalized_description: str) -> list[dict]:
    """Return lower-confidence category signals found in merchant text."""
    matches = []
    for category, keywords in REVIEW_CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword_matches(normalized_description, keyword):
                matches.append({"category": category, "keyword": keyword})
    return matches


def score_category_signals(matches: list[dict]) -> list[tuple[str, list[str], float]]:
    """Score lower-confidence category matches and return the strongest categories first."""
    scores: dict[str, dict] = {}
    for match in matches:
        category = match["category"]
        keyword = match["keyword"]
        weight = 0.62
        if keyword in {"gym", "fitness", "therapy", "medical", "vision"}:
            weight = 0.72
        elif keyword in {"shop", "store", "retail", "marketplace", "bookstore"}:
            weight = 0.66
        elif keyword in {"membership", "plan", "premium", "plus", "monthly"}:
            weight = 0.55

        bucket = scores.setdefault(category, {"keywords": [], "score": 0.0})
        bucket["keywords"].append(keyword)
        bucket["score"] += weight

    ranked = [
        (category, data["keywords"], data["score"])
        for category, data in scores.items()
    ]
    return sorted(ranked, key=lambda item: item[2], reverse=True)


def confidence_label(confidence: float) -> str:
    """Return a compact label for a confidence score."""
    if confidence >= 0.9:
        return "high"
    if confidence >= 0.7:
        return "medium"
    return "low"


def explain_category_source(source: str) -> str:
    """Return a user-facing source label for a category suggestion."""
    return CATEGORY_SOURCE_LABELS.get(source, "Category signal")


def suggest_category(description: str, amount_cents: int, current_category: str | None = None) -> dict:
    """Suggest a category with confidence, source, evidence, and a short reason."""
    normalized = normalize_text(description)
    current = current_category or categorize_transaction(description, amount_cents)

    if amount_cents > 0:
        return {
            "category": "Income",
            "confidence": 0.98,
            "confidence_label": "high",
            "source": "amount_direction",
            "matched_terms": ["positive amount"],
            "reason": "Positive transaction amounts are treated as income.",
        }

    strong_match = first_category_keyword_match(normalized, expense_category_keywords())
    if strong_match:
        category, keyword = strong_match
        confidence = 0.95 if category != current else 0.92
        return {
            "category": category,
            "confidence": confidence,
            "confidence_label": confidence_label(confidence),
            "source": "keyword_rule",
            "matched_terms": [keyword],
            "reason": f"Matched high-confidence merchant signal '{keyword}' for {category}.",
        }

    ranked_signals = score_category_signals(category_signal_matches(normalized))
    if ranked_signals:
        category, keywords, signal_score = ranked_signals[0]
        confidence = min(0.86, 0.68 + (signal_score * 0.12))
        return {
            "category": category,
            "confidence": round(confidence, 2),
            "confidence_label": confidence_label(confidence),
            "source": "category_signals",
            "matched_terms": keywords,
            "reason": f"Matched lower-confidence merchant signal{'s' if len(keywords) != 1 else ''}: {', '.join(keywords)}.",
        }

    confidence = 0.35 if current == "Other" else 0.62
    return {
        "category": current,
        "confidence": confidence,
        "confidence_label": confidence_label(confidence),
        "source": "fallback",
        "matched_terms": [],
        "reason": "No merchant signal matched confidently.",
    }


def categories_from_question(question: str) -> list[str]:
    """Infer one or more categories from a simple natural-language question."""
    normalized = normalize_text(question)
    for keyword, categories in QUESTION_CATEGORY_KEYWORDS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", normalized):
            return categories
    return []


def normalize_text(value: str) -> str:
    """Normalize merchant or question text for keyword matching."""
    return re.sub(r"\s+", " ", value.lower()).strip()
