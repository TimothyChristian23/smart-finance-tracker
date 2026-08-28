"""Starter transaction categorization rules."""
from __future__ import annotations

import re

CATEGORY_KEYWORDS = {
    "Income": ["payroll", "salary", "direct deposit", "deposit"],
    "Housing": ["rent", "apartment", "mortgage"],
    "Food & Grocery": ["grocery", "trader joe", "whole foods", "safeway"],
    "Dining": ["restaurant", "cafe", "coffee", "chipotle", "doordash", "uber eats"],
    "Transport": ["uber", "lyft", "transit", "metro", "fuel", "gas", "chevron"],
    "Shopping": ["amazon", "target", "walmart", "electronics"],
    "Utilities": ["electric", "utility", "water", "internet", "phone"],
    "Health": ["pharmacy", "cvs", "doctor", "clinic", "insurance"],
    "Subscriptions": ["netflix", "spotify", "hulu", "subscription"],
}

CATEGORY_OPTIONS = [*CATEGORY_KEYWORDS.keys(), "Other"]

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
    """Assign a starter category from merchant text and amount direction."""
    normalized = normalize_text(description)

    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return category

    if amount_cents > 0:
        return "Income"
    return "Other"


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
