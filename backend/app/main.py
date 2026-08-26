"""FastAPI backend for the Smart Personal Finance Tracker."""
from __future__ import annotations

import csv
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import StringIO
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.categorization import categories_from_question, categorize_transaction
from app.database import (
    cents_to_dollars,
    detect_anomalies,
    insert_transactions,
    list_transactions,
    monthly_summary,
    reset_db,
    spending_for_categories,
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


class TransactionResponse(BaseModel):
    id: int
    date: str
    description: str
    amount: float
    category: str
    source_file: str | None = None


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
    categories: list[str] = []
    month: str | None = None


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/transactions/upload", response_model=UploadResponse)
async def upload_transactions(file: UploadFile = File(...)) -> UploadResponse:
    """Upload a CSV bank statement and import transactions."""
    filename = Path(file.filename or "transactions.csv").name
    if Path(filename).suffix.lower() != ".csv":
        raise HTTPException(status_code=400, detail="Only CSV uploads are supported in this first slice.")

    content = (await file.read()).decode("utf-8-sig")
    rows = parse_transactions_csv(content, filename)
    imported = insert_transactions(rows)
    return UploadResponse(filename=filename, imported=imported)


@app.get("/transactions", response_model=list[TransactionResponse])
async def transactions(limit: int = 200) -> list[dict]:
    return list_transactions(limit=limit)


@app.get("/summary", response_model=SummaryResponse)
async def summary(month: str | None = None) -> dict:
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="month must use YYYY-MM format.")
    return monthly_summary(month=month)


@app.get("/anomalies", response_model=list[dict])
async def anomalies(limit: int = 10) -> list[dict]:
    return detect_anomalies(limit=limit)


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """Answer a simple spending question from structured transaction data."""
    question = request.question.strip()
    categories = categories_from_question(question)
    month = infer_month(question)

    if not categories:
        return AskResponse(
            answer=(
                "I can answer category spending questions such as "
                "'How much did I spend on food last month?'"
            )
        )

    spending_cents = spending_for_categories(categories, month=month)
    amount = cents_to_dollars(spending_cents)
    category_label = " and ".join(categories)
    month_label = month or "all imported data"

    return AskResponse(
        answer=f"You spent ${amount:,.2f} on {category_label} for {month_label}.",
        amount=amount,
        categories=categories,
        month=month,
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
            from datetime import datetime

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

    today = date.today()
    if "last month" in lowered:
        if today.month == 1:
            return f"{today.year - 1}-12"
        return f"{today.year}-{today.month - 1:02d}"

    if "this month" in lowered:
        return f"{today.year}-{today.month:02d}"

    return None
