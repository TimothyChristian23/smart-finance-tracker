"""FastAPI backend for the Smart Personal Finance Tracker."""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO, StringIO
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.categorization import CATEGORY_OPTIONS, categories_from_question, categorize_transaction
from app.database import (
    available_months,
    category_totals,
    cents_to_dollars,
    delete_merchant_rule,
    detect_anomalies,
    insert_transactions,
    largest_expenses,
    list_merchant_rules,
    list_transactions,
    monthly_summary,
    monthly_trends,
    reset_db,
    spending_for_categories,
    top_merchants,
    update_transaction_category,
)

app = FastAPI(title="Smart Personal Finance Tracker API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class UploadResponse(BaseModel):
    filename: str
    imported: int
    duplicates_skipped: int = 0


class TransactionResponse(BaseModel):
    id: int
    date: str
    description: str
    amount: float
    category: str
    source_file: str | None = None


class CategoryUpdateRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=80)
    remember: bool = False


class MerchantRuleResponse(BaseModel):
    id: int
    merchant: str
    merchant_key: str
    category: str
    updated_at: str


class SummaryCategory(BaseModel):
    category: str
    total: float


class SummaryResponse(BaseModel):
    month: str | None
    total_spending: float
    total_income: float
    net: float
    transaction_count: int
    categories: list[SummaryCategory]


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


class AskResponse(BaseModel):
    answer: str
    amount: float | None = None
    categories: list[str] = Field(default_factory=list)
    month: str | None = None
    intent: str = "unknown"
    data: list[dict] = Field(default_factory=list)


MONTH_ALIASES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/transactions/upload", response_model=UploadResponse)
async def upload_transactions(file: UploadFile = File(...)) -> UploadResponse:
    """Upload a CSV or text-based PDF bank statement and import transactions."""
    filename = Path(file.filename or "transactions.csv").name
    suffix = Path(filename).suffix.lower()
    content = await file.read()
    if suffix == ".csv":
        try:
            rows = parse_transactions_csv(content.decode("utf-8-sig"), filename)
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="CSV file must be UTF-8 text.") from exc
    elif suffix == ".pdf":
        rows = parse_transactions_pdf(content, filename)
    else:
        raise HTTPException(status_code=400, detail="Only CSV or text-based PDF uploads are supported.")

    result = insert_transactions(rows)
    return UploadResponse(
        filename=filename,
        imported=result["inserted"],
        duplicates_skipped=result["skipped"],
    )


@app.get("/transactions", response_model=list[TransactionResponse])
async def transactions(limit: int = 200) -> list[dict]:
    return list_transactions(limit=limit)


@app.patch("/transactions/{transaction_id}/category", response_model=TransactionResponse)
async def update_category(transaction_id: int, request: CategoryUpdateRequest) -> dict:
    category = validate_category(request.category)
    transaction = update_transaction_category(transaction_id, category, remember=request.remember)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return transaction


@app.get("/category-options", response_model=list[str])
async def category_options() -> list[str]:
    return CATEGORY_OPTIONS


@app.get("/merchant-rules", response_model=list[MerchantRuleResponse])
async def merchant_rules() -> list[dict]:
    return list_merchant_rules()


@app.delete("/merchant-rules/{rule_id}")
async def remove_merchant_rule(rule_id: int) -> dict:
    if not delete_merchant_rule(rule_id):
        raise HTTPException(status_code=404, detail="Merchant rule not found.")
    return {"message": "Merchant rule deleted."}


@app.get("/summary", response_model=SummaryResponse)
async def summary(month: str | None = None) -> dict:
    return monthly_summary(month=validate_month(month))


@app.get("/months")
async def months() -> list[dict]:
    return available_months()


@app.get("/categories")
async def categories(month: str | None = None) -> list[dict]:
    return category_totals(month=validate_month(month))


@app.get("/trends")
async def trends(limit: int = 12) -> list[dict]:
    return monthly_trends(limit=bounded_limit(limit, maximum=36))


@app.get("/merchants")
async def merchants(month: str | None = None, limit: int = 10) -> list[dict]:
    return top_merchants(month=validate_month(month), limit=bounded_limit(limit))


@app.get("/anomalies", response_model=list[dict])
async def anomalies(limit: int = 10, month: str | None = None) -> list[dict]:
    return detect_anomalies(limit=bounded_limit(limit), month=validate_month(month))


@app.get("/expenses/largest", response_model=list[dict])
async def largest(month: str | None = None, limit: int = 10) -> list[dict]:
    return largest_expenses(month=validate_month(month), limit=bounded_limit(limit))


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """Answer a simple spending question from structured transaction data."""
    question = request.question.strip()
    normalized = question.lower()
    categories = categories_from_question(question)
    month = infer_month(question)

    if has_any(normalized, ["anomaly", "anomalies", "unusual", "weird", "outlier"]):
        items = detect_anomalies(limit=5, month=month)
        month_label = format_month_label(month)
        if not items:
            return AskResponse(
                answer=f"I did not find unusual charges for {month_label}.",
                month=month,
                intent="anomalies",
            )
        lead = items[0]
        return AskResponse(
            answer=(
                f"I found {len(items)} unusual charge(s) for {month_label}. "
                f"The largest is {lead['description']} at {format_money(abs(lead['amount']))}."
            ),
            month=month,
            intent="anomalies",
            data=items,
        )

    if looks_like_top_category_question(normalized):
        breakdown = category_totals(month=month)
        month_label = format_month_label(month)
        if not breakdown:
            return AskResponse(
                answer=f"I do not have category spending for {month_label} yet.",
                month=month,
                intent="top_category",
            )
        top_category = breakdown[0]
        return AskResponse(
            answer=(
                f"Your biggest category for {month_label} was "
                f"{top_category['category']} at {format_money(top_category['total'])}."
            ),
            amount=top_category["total"],
            categories=[top_category["category"]],
            month=month,
            intent="top_category",
            data=breakdown[:5],
        )

    if has_any(normalized, ["merchant", "vendor", "store"]):
        merchants = top_merchants(month=month, limit=5)
        month_label = format_month_label(month)
        if not merchants:
            return AskResponse(
                answer=f"I do not have merchant spending for {month_label} yet.",
                month=month,
                intent="top_merchants",
            )
        top_merchant = merchants[0]
        return AskResponse(
            answer=(
                f"Your top merchant for {month_label} was "
                f"{top_merchant['merchant']} at {format_money(top_merchant['total'])}."
            ),
            amount=top_merchant["total"],
            categories=[top_merchant["category"]],
            month=month,
            intent="top_merchants",
            data=merchants,
        )

    if looks_like_largest_expense_question(normalized):
        expenses = largest_expenses(month=month, limit=5)
        month_label = format_month_label(month)
        if not expenses:
            return AskResponse(
                answer=f"I do not have expenses for {month_label} yet.",
                month=month,
                intent="largest_expenses",
            )
        largest_expense = expenses[0]
        return AskResponse(
            answer=(
                f"Your largest expense for {month_label} was "
                f"{largest_expense['description']} at {format_money(abs(largest_expense['amount']))}."
            ),
            amount=abs(largest_expense["amount"]),
            categories=[largest_expense["category"]],
            month=month,
            intent="largest_expenses",
            data=expenses,
        )

    if re.search(r"\bincome\b|\bearned\b|\bdeposit", normalized):
        summary_data = monthly_summary(month=month)
        amount = summary_data["total_income"]
        return AskResponse(
            answer=f"Your income for {format_month_label(month)} was {format_money(amount)}.",
            amount=amount,
            month=month,
            intent="income",
            data=[summary_data],
        )

    if re.search(r"\bnet\b|\bsaved\b|\bsavings\b", normalized):
        summary_data = monthly_summary(month=month)
        amount = summary_data["net"]
        return AskResponse(
            answer=f"Your net cash flow for {format_month_label(month)} was {format_money(amount)}.",
            amount=amount,
            month=month,
            intent="net",
            data=[summary_data],
        )

    if not categories:
        if re.search(r"\bspend\b|\bspent\b|\bspending\b|\bexpenses?\b", normalized):
            summary_data = monthly_summary(month=month)
            amount = summary_data["total_spending"]
            return AskResponse(
                answer=f"You spent {format_money(amount)} for {format_month_label(month)}.",
                amount=amount,
                month=month,
                intent="total_spending",
                data=[summary_data],
            )

        return AskResponse(
            answer=(
                "I can answer spending, income, net cash flow, category, merchant, "
                "largest expense, and anomaly questions."
            ),
            month=month,
        )

    spending_cents = spending_for_categories(categories, month=month)
    amount = cents_to_dollars(spending_cents)
    category_label = " and ".join(categories)
    month_label = format_month_label(month)

    return AskResponse(
        answer=f"You spent {format_money(amount)} on {category_label} for {month_label}.",
        amount=amount,
        categories=categories,
        month=month,
        intent="category_spending",
    )


@app.delete("/transactions")
async def clear_transactions() -> dict:
    reset_db()
    return {"message": "Transactions cleared."}


def parse_transactions_csv(content: str, source_file: str) -> list[dict]:
    """Parse common CSV statement columns into normalized transaction rows."""
    reader = csv.DictReader(StringIO(content))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV file is missing headers.")

    rows = []
    for row_number, raw_row in enumerate(reader, start=2):
        try:
            parsed_date = parse_date(get_column(raw_row, ["date", "transaction date", "posted date"]))
            description = get_column(raw_row, ["description", "merchant", "name", "memo"])
            amount_cents = parse_amount(raw_row)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Row {row_number}: {exc}") from exc

        category = get_column(raw_row, ["category"], required=False)
        rows.append({
            "date": parsed_date,
            "description": description,
            "amount_cents": amount_cents,
            "category": category or categorize_transaction(description, amount_cents),
            "source_file": source_file,
        })

    if not rows:
        raise HTTPException(status_code=400, detail="CSV file does not contain any transactions.")

    return rows


def parse_transactions_pdf(content: bytes, source_file: str) -> list[dict]:
    """Parse transaction rows from a text-based PDF statement."""
    try:
        reader = PdfReader(BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Could not read PDF text. Scanned image PDFs are not supported yet.",
        ) from exc

    return parse_transactions_text(text, source_file)


def parse_transactions_text(content: str, source_file: str) -> list[dict]:
    """Parse transaction-like rows from extracted statement text."""
    has_balance_column = bool(re.search(r"\bbalance\b", content, flags=re.IGNORECASE))
    rows = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        normalized = re.sub(r"\s+", " ", line).strip()
        if not normalized:
            continue

        try:
            row = parse_statement_text_line(normalized, has_balance_column)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"PDF line {line_number}: {exc}") from exc
        if row:
            row["source_file"] = source_file
            rows.append(row)

    if not rows:
        raise HTTPException(
            status_code=400,
            detail=(
                "PDF did not contain recognizable transaction rows. Expected rows like "
                "'2026-07-02 Trader Joes -86.42'."
            ),
        )

    return rows


def parse_statement_text_line(line: str, has_balance_column: bool = False) -> dict | None:
    """Parse one extracted PDF text line into a normalized transaction row."""
    row_match = re.match(
        r"^(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\s+(?P<body>.+)$",
        line,
    )
    if not row_match:
        return None

    body = row_match.group("body").strip()
    amount_match = re.search(
        r"(?P<first>\(?-?\$?\d[\d,]*\.\d{2}\)?|\$?\(\d[\d,]*\.\d{2}\))"
        r"(?:\s+(?P<second>\(?-?\$?\d[\d,]*\.\d{2}\)?|\$?\(\d[\d,]*\.\d{2}\)))?$",
        body,
    )
    if not amount_match:
        return None

    amount_group = "first" if has_balance_column and amount_match.group("second") else "second"
    amount_text = amount_match.group(amount_group) or amount_match.group("first")
    description_end = amount_match.start(amount_group) if amount_match.group(amount_group) else amount_match.start("first")
    description = body[:description_end].strip()
    if not description:
        raise ValueError("missing transaction description")

    amount_cents = money_to_cents(amount_text)
    return {
        "date": parse_date(row_match.group("date")),
        "description": description,
        "amount_cents": amount_cents,
        "category": categorize_transaction(description, amount_cents),
    }


def get_column(raw_row: dict, candidates: list[str], required: bool = True) -> str:
    normalized = {key.strip().lower(): value for key, value in raw_row.items() if key}
    for candidate in candidates:
        value = normalized.get(candidate)
        if value and value.strip():
            return value.strip()
    if required:
        raise ValueError(f"missing required column: {'/'.join(candidates)}")
    return ""


def parse_date(value: str) -> str:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            if fmt == "%Y-%m-%d":
                return date.fromisoformat(value).isoformat()
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"invalid date '{value}'")


def parse_amount(raw_row: dict) -> int:
    debit = get_column(raw_row, ["debit", "withdrawal"], required=False)
    credit = get_column(raw_row, ["credit", "deposit"], required=False)
    if debit or credit:
        if debit:
            return -money_to_cents(debit)
        return money_to_cents(credit)

    amount = get_column(raw_row, ["amount"])
    return money_to_cents(amount)


def money_to_cents(value: str) -> int:
    cleaned = value.strip().replace("$", "").replace(",", "")
    is_parenthesized = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount '{value}'") from exc
    if is_parenthesized:
        amount = -amount
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def infer_month(question: str) -> str | None:
    """Infer a YYYY-MM month from common question wording."""
    lowered = question.lower()
    explicit_month = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", lowered)
    if explicit_month:
        return explicit_month.group(0)

    named_month = re.search(
        r"\b("
        + "|".join(MONTH_ALIASES.keys())
        + r")\s+(20\d{2})\b",
        lowered,
    )
    if named_month:
        return f"{named_month.group(2)}-{MONTH_ALIASES[named_month.group(1)]:02d}"

    today = date.today()
    if "last month" in lowered:
        if today.month == 1:
            return f"{today.year - 1}-12"
        return f"{today.year}-{today.month - 1:02d}"

    if "this month" in lowered:
        return f"{today.year}-{today.month:02d}"

    return None


def validate_month(month: str | None) -> str | None:
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="month must use YYYY-MM format.")
    return month


def validate_category(category: str) -> str:
    normalized = category.strip().lower()
    for option in CATEGORY_OPTIONS:
        if option.lower() == normalized:
            return option
    raise HTTPException(
        status_code=400,
        detail=f"category must be one of: {', '.join(CATEGORY_OPTIONS)}.",
    )


def bounded_limit(limit: int, maximum: int = 100) -> int:
    return max(1, min(limit, maximum))


def has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def looks_like_top_category_question(question: str) -> bool:
    return (
        has_any(question, ["category", "categories"])
        and has_any(question, ["biggest", "largest", "top", "most"])
    )


def looks_like_largest_expense_question(question: str) -> bool:
    return (
        has_any(question, ["largest", "biggest", "highest", "top"])
        and has_any(question, ["expense", "charge", "purchase", "transaction"])
    )


def format_month_label(month: str | None) -> str:
    return month or "all imported data"


def format_money(value: float) -> str:
    return f"${value:,.2f}"
