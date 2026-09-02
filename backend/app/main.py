"""FastAPI backend for the Smart Personal Finance Tracker."""
from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO, StringIO
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.categorization import CATEGORY_OPTIONS, categories_from_question, categorize_transaction
from app.database import (
    available_months,
    budget_progress,
    budget_recommendations,
    category_review_queue,
    category_totals,
    cents_to_dollars,
    create_transaction,
    delete_budget,
    delete_merchant_rule,
    delete_transaction,
    detect_anomalies,
    export_backup,
    get_transaction,
    insert_transactions,
    largest_expenses,
    list_accounts,
    list_ask_history,
    list_merchant_rules,
    list_transactions,
    list_uploads,
    monthly_forecast,
    monthly_insights,
    monthly_summary,
    question_evidence,
    monthly_trends,
    preview_import,
    record_upload,
    record_ask_history,
    recurring_charges,
    reset_all_data,
    reset_db,
    spending_for_categories,
    top_merchants,
    upsert_budget,
    update_transaction_category,
    update_transaction_details,
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
    account_name: str | None = None
    imported: int
    duplicates_skipped: int = 0


class ImportPreviewRow(BaseModel):
    date: str
    description: str
    amount: float
    category: str
    source_file: str | None = None
    account_name: str | None = None
    duplicate: bool


class ImportPreviewCategory(BaseModel):
    category: str
    total: float
    transaction_count: int


class ImportPreviewResponse(BaseModel):
    filename: str
    file_type: str
    row_count: int
    importable_count: int
    duplicate_count: int
    first_transaction_date: str | None = None
    last_transaction_date: str | None = None
    total_spending: float
    total_income: float
    net: float
    categories: list[ImportPreviewCategory]
    rows: list[ImportPreviewRow]
    errors: list[str] = Field(default_factory=list)


class UploadHistoryResponse(BaseModel):
    id: int
    filename: str
    file_type: str
    account_name: str | None = None
    parsed_count: int
    imported_count: int
    duplicates_skipped: int
    first_transaction_date: str | None = None
    last_transaction_date: str | None = None
    created_at: str


class TransactionResponse(BaseModel):
    id: int
    date: str
    description: str
    amount: float
    category: str
    source_file: str | None = None
    account_name: str | None = None


class CategoryReviewResponse(BaseModel):
    transaction: TransactionResponse
    current_category: str
    suggested_category: str
    confidence: float
    reason: str
    action: str


class CategoryUpdateRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=80)
    remember: bool = False


class TransactionCreateRequest(BaseModel):
    date: str
    description: str = Field(..., min_length=1, max_length=200)
    amount: float
    category: str = Field(..., min_length=1, max_length=80)
    account_name: str | None = Field(None, max_length=80)


class TransactionUpdateRequest(BaseModel):
    date: str | None = None
    description: str | None = Field(None, min_length=1, max_length=200)
    amount: float | None = None
    category: str | None = Field(None, min_length=1, max_length=80)
    account_name: str | None = Field(None, max_length=80)


class MerchantRuleResponse(BaseModel):
    id: int
    merchant: str
    merchant_key: str
    category: str
    updated_at: str


class BudgetUpsertRequest(BaseModel):
    month: str = Field(..., min_length=7, max_length=7)
    category: str = Field(..., min_length=1, max_length=80)
    amount: float = Field(..., gt=0)


class BudgetResponse(BaseModel):
    id: int
    month: str
    category: str
    amount: float
    spent: float
    remaining: float
    percent_used: float
    status: str
    updated_at: str


class BudgetRecommendationResponse(BaseModel):
    month: str
    category: str
    recommended_amount: float
    baseline_average: float
    recurring_amount: float
    history_months: int
    existing_budget: float | None = None
    difference_from_existing: float
    confidence: str
    action: str
    reason: str


class RecurringChargeResponse(BaseModel):
    merchant: str
    category: str
    average_amount: float
    total_amount: float
    occurrences: int
    first_seen: str
    last_seen: str
    next_expected_date: str
    cadence: str
    confidence: float


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


class MonthlyInsightResponse(BaseModel):
    month: str | None
    summary: dict
    spending_delta: float | None = None
    spending_delta_percent: float | None = None
    top_category: dict | None = None
    top_merchant: dict | None = None
    largest_expense: dict | None = None
    over_budget_count: int
    near_budget_count: int
    recurring_count: int
    anomaly_count: int
    highlights: list[str]
    risks: list[str]
    next_actions: list[str]


class CashFlowForecastResponse(BaseModel):
    month: str | None
    status: str
    confidence: str
    coverage_start_date: str | None = None
    coverage_end_date: str | None = None
    days_elapsed: int
    days_in_month: int
    remaining_days: int
    actual_spending: float
    daily_spending_average: float
    run_rate_projection: float
    projected_spending: float
    projected_income: float
    projected_net: float
    budget_total: float
    budget_remaining: float
    budget_status: str
    upcoming_recurring_total: float
    upcoming_recurring: list[dict]
    notes: list[str]


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


class AskResponse(BaseModel):
    answer: str
    amount: float | None = None
    categories: list[str] = Field(default_factory=list)
    month: str | None = None
    intent: str = "unknown"
    data: list[dict] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)


class AskHistoryResponse(BaseModel):
    id: int
    question: str
    answer: str
    amount: float | None = None
    categories: list[str] = Field(default_factory=list)
    month: str | None = None
    intent: str
    created_at: str


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

DATE_COLUMNS = [
    "date",
    "transaction date",
    "posted date",
    "posting date",
    "post date",
    "date posted",
    "trans date",
    "effective date",
]
DESCRIPTION_COLUMNS = [
    "description",
    "transaction description",
    "merchant",
    "merchant name",
    "name",
    "payee",
    "memo",
    "details",
    "detail",
    "narrative",
]
CATEGORY_COLUMNS = ["category", "type category", "spending category"]
ACCOUNT_COLUMNS = ["account", "account name", "account nickname", "source account", "card", "card name"]
AMOUNT_COLUMNS = [
    "amount",
    "transaction amount",
    "net amount",
    "signed amount",
]
DEBIT_COLUMNS = [
    "debit",
    "debits",
    "debit amount",
    "withdrawal",
    "withdrawals",
    "withdrawal amount",
    "charge",
    "charges",
]
CREDIT_COLUMNS = [
    "credit",
    "credits",
    "credit amount",
    "deposit",
    "deposits",
    "deposit amount",
]
TYPE_COLUMNS = ["type", "transaction type", "debit/credit", "credit/debit"]
DEBIT_TYPES = ["debit", "withdrawal", "purchase", "charge", "payment", "pos", "check"]
CREDIT_TYPES = ["credit", "deposit", "payroll", "refund", "interest", "income"]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/transactions/upload", response_model=UploadResponse)
async def upload_transactions(
    file: UploadFile = File(...),
    account_name: str | None = Form(None),
) -> UploadResponse:
    """Upload a CSV or text-based PDF bank statement and import transactions."""
    account_label = validate_account_name(account_name)
    filename, file_type, rows = await parse_uploaded_statement(file)
    apply_account_label(rows, account_label)

    result = insert_transactions(rows)
    record_upload(filename, file_type, rows, result, account_name=account_label)
    return UploadResponse(
        filename=filename,
        account_name=account_label,
        imported=result["inserted"],
        duplicates_skipped=result["skipped"],
    )


@app.post("/transactions/preview", response_model=ImportPreviewResponse)
async def preview_transactions(
    file: UploadFile = File(...),
    limit: int = 25,
    account_name: str | None = Form(None),
) -> dict:
    """Preview parsed statement rows and duplicate estimates without importing."""
    account_label = validate_account_name(account_name)
    filename, file_type, rows, errors = await preview_uploaded_statement(file)
    apply_account_label(rows, account_label)
    preview = preview_import(rows, sample_limit=bounded_limit(limit, maximum=100))
    preview["errors"] = errors
    return {
        "filename": filename,
        "file_type": file_type,
        **preview,
    }


@app.get("/transactions", response_model=list[TransactionResponse])
async def transactions(
    limit: int = 200,
    month: str | None = None,
    category: str | None = None,
    search: str | None = None,
    account: str | None = None,
) -> list[dict]:
    return filtered_transactions(limit=limit, month=month, category=category, search=search, account=account)


@app.post("/transactions", response_model=TransactionResponse)
async def create_manual_transaction(request: TransactionCreateRequest) -> dict:
    return create_transaction(
        transaction_date=validate_transaction_date(request.date),
        description=validate_transaction_description(request.description),
        amount_cents=dollars_to_cents(request.amount),
        category=validate_category(request.category),
        account_name=validate_account_name(request.account_name),
    )


@app.get("/transactions/export")
async def export_transactions(
    limit: int = 5000,
    month: str | None = None,
    category: str | None = None,
    search: str | None = None,
    account: str | None = None,
) -> Response:
    rows = filtered_transactions(
        limit=limit,
        month=month,
        category=category,
        search=search,
        account=account,
        maximum=5000,
    )
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["date", "description", "category", "amount", "source_file", "account_name"],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "date": row["date"],
            "description": row["description"],
            "category": row["category"],
            "amount": f"{row['amount']:.2f}",
            "source_file": row["source_file"] or "",
            "account_name": row["account_name"] or "",
        })

    filename = f"transactions-{month or 'all'}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/data/export")
async def export_data() -> Response:
    backup = export_backup()
    filename = f"finance-backup-{date.today().isoformat()}.json"
    return Response(
        content=json.dumps(backup, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/accounts", response_model=list[str])
async def accounts() -> list[str]:
    return list_accounts()


@app.get("/uploads", response_model=list[UploadHistoryResponse])
async def uploads(limit: int = 20) -> list[dict]:
    return list_uploads(limit=bounded_limit(limit, maximum=100))


@app.patch("/transactions/{transaction_id}/category", response_model=TransactionResponse)
async def update_category(transaction_id: int, request: CategoryUpdateRequest) -> dict:
    category = validate_category(request.category)
    transaction = update_transaction_category(transaction_id, category, remember=request.remember)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return transaction


@app.patch("/transactions/{transaction_id}", response_model=TransactionResponse)
async def update_transaction(transaction_id: int, request: TransactionUpdateRequest) -> dict:
    current = get_transaction(transaction_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    transaction_date = validate_transaction_date(request.date) if request.date is not None else current["date"]
    description = (
        validate_transaction_description(request.description)
        if request.description is not None
        else current["description"]
    )
    amount_cents = dollars_to_cents(request.amount) if request.amount is not None else dollars_to_cents(current["amount"])
    category = validate_category(request.category) if request.category is not None else current["category"]
    account_name = (
        validate_account_name(request.account_name)
        if request.account_name is not None
        else current["account_name"]
    )

    updated = update_transaction_details(
        transaction_id,
        transaction_date=transaction_date,
        description=description,
        amount_cents=amount_cents,
        category=category,
        account_name=account_name,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return updated


@app.delete("/transactions/{transaction_id}")
async def remove_transaction(transaction_id: int) -> dict:
    if not delete_transaction(transaction_id):
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return {"message": "Transaction deleted."}


@app.get("/category-options", response_model=list[str])
async def category_options() -> list[str]:
    return CATEGORY_OPTIONS


@app.get("/categories/review", response_model=list[CategoryReviewResponse])
async def category_review(month: str | None = None, limit: int = 20) -> list[dict]:
    return category_review_queue(month=validate_month(month), limit=bounded_limit(limit))


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


@app.get("/insights/monthly", response_model=MonthlyInsightResponse)
async def insights(month: str | None = None) -> dict:
    return monthly_insights(month=validate_month(month))


@app.get("/forecast/monthly", response_model=CashFlowForecastResponse)
async def forecast(month: str | None = None) -> dict:
    return monthly_forecast(month=validate_month(month))


@app.get("/months")
async def months() -> list[dict]:
    return available_months()


@app.get("/categories")
async def categories(month: str | None = None) -> list[dict]:
    return category_totals(month=validate_month(month))


@app.get("/budgets", response_model=list[BudgetResponse])
async def budgets(month: str | None = None) -> list[dict]:
    budget_month = validate_month(month)
    if budget_month is None:
        return []
    return budget_progress(budget_month)


@app.get("/budgets/recommendations", response_model=list[BudgetRecommendationResponse])
async def recommended_budgets(month: str | None = None, limit: int = 8) -> list[dict]:
    return budget_recommendations(month=validate_month(month), limit=bounded_limit(limit))


@app.put("/budgets", response_model=BudgetResponse)
async def save_budget(request: BudgetUpsertRequest) -> dict:
    month = validate_month(request.month)
    category = validate_category(request.category)
    amount_cents = dollars_to_cents(request.amount)
    if amount_cents <= 0:
        raise HTTPException(status_code=400, detail="budget amount must be at least $0.01.")
    return upsert_budget(month, category, amount_cents)


@app.delete("/budgets/{budget_id}")
async def remove_budget(budget_id: int) -> dict:
    if not delete_budget(budget_id):
        raise HTTPException(status_code=404, detail="Budget not found.")
    return {"message": "Budget deleted."}


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


@app.get("/recurring", response_model=list[RecurringChargeResponse])
async def recurring(limit: int = 10) -> list[dict]:
    return recurring_charges(limit=bounded_limit(limit))


@app.get("/ask/history", response_model=list[AskHistoryResponse])
async def ask_history(limit: int = 10) -> list[dict]:
    return list_ask_history(limit=bounded_limit(limit, maximum=50))


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """Answer a finance question and remember the exchange locally."""
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question cannot be empty.")

    response = answer_finance_question(question)
    record_ask_history(
        question=question,
        answer=response.answer,
        amount=response.amount,
        categories=response.categories,
        month=response.month,
        intent=response.intent,
    )
    return response


def answer_finance_question(question: str) -> AskResponse:
    """Answer a simple spending question from structured transaction data."""
    normalized = question.lower()
    categories = categories_from_question(question)
    month = infer_month(question) or infer_follow_up_month(normalized)

    if looks_like_upload_history_question(normalized):
        uploads = list_uploads(limit=5)
        if not uploads:
            return AskResponse(
                answer="I do not have any uploaded statements recorded yet.",
                intent="upload_history",
            )

        latest = uploads[0]
        return AskResponse(
            answer=(
                f"I found {len(uploads)} recent upload"
                f"{'' if len(uploads) == 1 else 's'}. "
                f"The latest is {latest['filename']} with "
                f"{latest['imported_count']} imported and "
                f"{latest['duplicates_skipped']} skipped."
            ),
            intent="upload_history",
            data=uploads,
        )

    if looks_like_monthly_report_question(normalized):
        report_month = month or latest_imported_month()
        report = monthly_insights(month=report_month)
        if report["month"] is None:
            return AskResponse(
                answer="I do not have imported transactions yet, so I cannot build a monthly report.",
                intent="monthly_insights",
                data=[report],
            )

        summary_data = report["summary"]
        risk_text = report["risks"][0] if report["risks"] else "No urgent risks were flagged."
        highlight_text = report["highlights"][1] if len(report["highlights"]) > 1 else report["highlights"][0]
        return AskResponse(
            answer=(
                f"For {format_month_label(report['month'])}, you spent "
                f"{format_money(summary_data['total_spending'])} and net cash flow was "
                f"{format_money(summary_data['net'])}. {highlight_text} {risk_text}"
            ),
            amount=summary_data["total_spending"],
            categories=[report["top_category"]["category"]] if report["top_category"] else [],
            month=report["month"],
            intent="monthly_insights",
            data=[report],
        )

    if looks_like_forecast_question(normalized):
        forecast_month = month or latest_imported_month()
        forecast_data = monthly_forecast(month=forecast_month)
        if forecast_data["month"] is None:
            return AskResponse(
                answer="I do not have enough transaction data to build a forecast yet.",
                intent="monthly_forecast",
                data=[forecast_data],
            )

        recurring_text = ""
        if forecast_data["upcoming_recurring_total"]:
            recurring_text = (
                f" Upcoming recurring charges add "
                f"{format_money(forecast_data['upcoming_recurring_total'])}."
            )

        return AskResponse(
            answer=(
                f"For {format_month_label(forecast_data['month'])}, projected spending is "
                f"{format_money(forecast_data['projected_spending'])}. "
                f"Projected net cash flow is {format_money(forecast_data['projected_net'])}."
                f"{recurring_text}"
            ),
            amount=forecast_data["projected_spending"],
            month=forecast_data["month"],
            intent="monthly_forecast",
            data=[forecast_data],
        )

    if looks_like_budget_recommendation_question(normalized):
        recommendation_month = month or latest_imported_month()
        recommendations = budget_recommendations(month=recommendation_month, limit=5)
        if not recommendations:
            return AskResponse(
                answer="I do not have enough spending history to recommend budgets yet.",
                month=recommendation_month,
                intent="budget_recommendations",
            )

        lead = recommendations[0]
        return AskResponse(
            answer=(
                f"For {format_month_label(lead['month'])}, I recommend starting with "
                f"{lead['category']} at {format_money(lead['recommended_amount'])}. "
                f"I found {len(recommendations)} suggested budget "
                f"categor{'y' if len(recommendations) == 1 else 'ies'}."
            ),
            amount=lead["recommended_amount"],
            categories=[item["category"] for item in recommendations],
            month=lead["month"],
            intent="budget_recommendations",
            data=recommendations,
        )

    if has_any(normalized, ["budget", "budgets"]):
        budget_month = month or latest_imported_month()
        if budget_month is None:
            return AskResponse(
                answer="I do not have any imported months to compare against budgets yet.",
                intent="budgets",
            )

        budget_items = budget_progress(budget_month)
        if not budget_items:
            return AskResponse(
                answer=f"I do not have budgets for {format_month_label(budget_month)} yet.",
                month=budget_month,
                intent="budgets",
            )

        over_budget = [item for item in budget_items if item["status"] == "over"]
        if over_budget:
            largest_gap = max(over_budget, key=lambda item: item["spent"] - item["amount"])
            return AskResponse(
                answer=(
                    f"You are over budget in {len(over_budget)} categor"
                    f"{'y' if len(over_budget) == 1 else 'ies'} for {format_month_label(budget_month)}. "
                    f"{largest_gap['category']} is {format_money(abs(largest_gap['remaining']))} over."
                ),
                amount=abs(largest_gap["remaining"]),
                categories=[item["category"] for item in over_budget],
                month=budget_month,
                intent="budgets",
                data=budget_items,
            )

        total_remaining = sum(item["remaining"] for item in budget_items)
        return AskResponse(
            answer=(
                f"You are within all budgets for {format_month_label(budget_month)} with "
                f"{format_money(total_remaining)} remaining."
            ),
            amount=total_remaining,
            categories=[item["category"] for item in budget_items],
            month=budget_month,
            intent="budgets",
            data=budget_items,
        )

    if has_any(normalized, ["recurring", "subscription", "subscriptions", "repeat", "repeating"]):
        charges = recurring_charges(limit=5)
        if not charges:
            return AskResponse(
                answer="I did not find recurring charges yet. I need the same merchant across multiple months.",
                intent="recurring_charges",
            )

        lead = charges[0]
        return AskResponse(
            answer=(
                f"I found {len(charges)} likely recurring charge"
                f"{'' if len(charges) == 1 else 's'}. "
                f"The clearest match is {lead['merchant']} at about "
                f"{format_money(lead['average_amount'])} {lead['cadence']}."
            ),
            amount=lead["average_amount"],
            categories=[item["category"] for item in charges],
            intent="recurring_charges",
            data=charges,
        )

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

    if looks_like_contextual_question(normalized):
        evidence_answer = answer_from_evidence(question, month=month, require_match=False)
        if evidence_answer:
            return evidence_answer

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

        evidence_answer = answer_from_evidence(question, month=month, require_match=True)
        if evidence_answer:
            return evidence_answer

        return AskResponse(
            answer=(
                "I can answer spending, income, net cash flow, category, merchant, "
                "budget, forecast, recurring charge, upload history, monthly report, "
                "largest expense, anomaly, and cited evidence questions."
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


@app.delete("/data")
async def clear_all_data(confirmation: str | None = None) -> dict:
    if confirmation != "RESET":
        raise HTTPException(status_code=400, detail="Type RESET to clear all local finance data.")

    reset_all_data()
    return {"message": "All local finance data cleared."}


async def parse_uploaded_statement(file: UploadFile) -> tuple[str, str, list[dict]]:
    filename = Path(file.filename or "transactions.csv").name
    suffix = Path(filename).suffix.lower()
    content = await file.read()
    if suffix == ".csv":
        try:
            return filename, "csv", parse_transactions_csv(content.decode("utf-8-sig"), filename)
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="CSV file must be UTF-8 text.") from exc
    if suffix == ".pdf":
        return filename, "pdf", parse_transactions_pdf(content, filename)
    raise HTTPException(status_code=400, detail="Only CSV or text-based PDF uploads are supported.")


async def preview_uploaded_statement(file: UploadFile) -> tuple[str, str, list[dict], list[str]]:
    filename = Path(file.filename or "transactions.csv").name
    suffix = Path(filename).suffix.lower()
    content = await file.read()
    if suffix == ".csv":
        try:
            rows, errors = parse_transactions_csv_preview(content.decode("utf-8-sig"), filename)
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="CSV file must be UTF-8 text.") from exc
        return filename, "csv", rows, errors
    if suffix == ".pdf":
        return filename, "pdf", parse_transactions_pdf(content, filename), []
    raise HTTPException(status_code=400, detail="Only CSV or text-based PDF uploads are supported.")


def parse_transactions_csv(content: str, source_file: str) -> list[dict]:
    """Parse common CSV statement columns into normalized transaction rows."""
    rows, _errors = parse_transactions_csv_rows(content, source_file, collect_errors=False)
    return rows


def parse_transactions_csv_preview(content: str, source_file: str) -> tuple[list[dict], list[str]]:
    """Parse CSV rows for preview and collect row-level errors."""
    return parse_transactions_csv_rows(content, source_file, collect_errors=True)


def parse_transactions_csv_rows(content: str, source_file: str, collect_errors: bool = False) -> tuple[list[dict], list[str]]:
    """Parse CSV statement rows with optional row-level error collection."""
    reader = csv.DictReader(StringIO(content))
    if not reader.fieldnames:
        message = "CSV file is missing headers."
        if collect_errors:
            return [], [message]
        raise HTTPException(status_code=400, detail=message)

    rows = []
    errors = []
    for row_number, raw_row in enumerate(reader, start=2):
        if row_is_blank(raw_row):
            continue
        try:
            parsed_date = parse_date(get_column(raw_row, DATE_COLUMNS))
            description = get_column(raw_row, DESCRIPTION_COLUMNS)
            amount_cents = parse_amount(raw_row)
        except ValueError as exc:
            message = f"Row {row_number}: {exc}"
            if collect_errors:
                errors.append(message)
                continue
            raise HTTPException(status_code=400, detail=message) from exc

        category = get_column(raw_row, CATEGORY_COLUMNS, required=False)
        account_name = normalize_csv_account_name(get_column(raw_row, ACCOUNT_COLUMNS, required=False))
        rows.append({
            "date": parsed_date,
            "description": description,
            "amount_cents": amount_cents,
            "category": category or categorize_transaction(description, amount_cents),
            "source_file": source_file,
            "account_name": account_name,
        })

    if not rows:
        message = "CSV file does not contain any transactions."
        if collect_errors:
            return [], errors or [message]
        raise HTTPException(status_code=400, detail=message)

    return rows, errors


def row_is_blank(raw_row: dict) -> bool:
    return not any(str(value).strip() for value in raw_row.values() if value is not None)


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
    normalized = {normalize_header(key): value for key, value in raw_row.items() if key}
    for candidate in candidates:
        value = normalized.get(normalize_header(candidate))
        if value and value.strip():
            return value.strip()
    if required:
        raise ValueError(f"missing required column: {'/'.join(candidates)}")
    return ""


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


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
    debit = get_column(raw_row, DEBIT_COLUMNS, required=False)
    credit = get_column(raw_row, CREDIT_COLUMNS, required=False)
    if debit or credit:
        if debit:
            return -abs(money_to_cents(debit))
        return abs(money_to_cents(credit))

    amount = get_column(raw_row, AMOUNT_COLUMNS)
    amount_cents = money_to_cents(amount)
    transaction_type = get_column(raw_row, TYPE_COLUMNS, required=False).lower()
    if transaction_type:
        if has_any(transaction_type, DEBIT_TYPES):
            return -abs(amount_cents)
        if has_any(transaction_type, CREDIT_TYPES):
            return abs(amount_cents)
    return amount_cents


def money_to_cents(value: str) -> int:
    cleaned = value.strip().replace("$", "").replace(",", "")
    is_parenthesized = cleaned.startswith("(") and cleaned.endswith(")")
    has_trailing_minus = cleaned.endswith("-")
    if has_trailing_minus:
        cleaned = cleaned[:-1]
    cleaned = cleaned.strip("()")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount '{value}'") from exc
    if is_parenthesized or has_trailing_minus:
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


def validate_transaction_date(value: str) -> str:
    try:
        return parse_date(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def validate_transaction_description(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise HTTPException(status_code=400, detail="description cannot be empty.")
    if len(normalized) > 200:
        raise HTTPException(status_code=400, detail="description must be 200 characters or fewer.")
    return normalized


def validate_category(category: str) -> str:
    normalized = category.strip().lower()
    for option in CATEGORY_OPTIONS:
        if option.lower() == normalized:
            return option
    raise HTTPException(
        status_code=400,
        detail=f"category must be one of: {', '.join(CATEGORY_OPTIONS)}.",
    )


def validate_account_name(account_name: str | None) -> str | None:
    if account_name is None:
        return None

    normalized = " ".join(account_name.split())
    if not normalized:
        return None
    if len(normalized) > 80:
        raise HTTPException(status_code=400, detail="account_name must be 80 characters or fewer.")
    return normalized


def normalize_csv_account_name(account_name: str | None) -> str | None:
    if not account_name:
        return None

    normalized = " ".join(account_name.split())
    if not normalized:
        return None
    if len(normalized) > 80:
        raise ValueError("account name must be 80 characters or fewer")
    return normalized


def apply_account_label(rows: list[dict], account_name: str | None) -> None:
    if account_name is None:
        return
    for row in rows:
        row["account_name"] = account_name


def bounded_limit(limit: int, maximum: int = 100) -> int:
    return max(1, min(limit, maximum))


def filtered_transactions(
    limit: int = 200,
    month: str | None = None,
    category: str | None = None,
    search: str | None = None,
    account: str | None = None,
    maximum: int = 200,
) -> list[dict]:
    return list_transactions(
        limit=bounded_limit(limit, maximum=maximum),
        month=validate_month(month),
        category=validate_category(category) if category else None,
        search=validate_search(search),
        account_name=validate_account_name(account),
    )


def validate_search(search: str | None) -> str | None:
    if search is None:
        return None

    normalized = " ".join(search.split())
    if not normalized:
        return None
    if len(normalized) > 120:
        raise HTTPException(status_code=400, detail="search must be 120 characters or fewer.")
    return normalized


def answer_from_evidence(question: str, month: str | None = None, require_match: bool = False) -> AskResponse | None:
    context = question_evidence(question, month=month, limit=6)
    summary_data = context["summary"]
    if context["month"] is None or not summary_data["transaction_count"]:
        return None
    if require_match and not context["matches"]:
        return None

    answer = compose_evidence_answer(question, context)
    if context["matches"]:
        categories = list(dict.fromkeys(item["category"] for item in context["matches"]))
    else:
        categories = [item["category"] for item in context["top_categories"][:3]]
    amount = sum(abs(item["amount"]) for item in context["matches"]) if context["matches"] else summary_data["total_spending"]
    return AskResponse(
        answer=answer,
        amount=round(amount, 2),
        categories=categories,
        month=context["month"],
        intent="evidence_answer",
        data=context["matches"],
        citations=context["citations"],
    )


def compose_evidence_answer(question: str, context: dict) -> str:
    normalized = question.lower()
    month_label = format_month_label(context["month"])
    summary_data = context["summary"]
    matches = context["matches"]
    top_categories = context["top_categories"]
    top_merchants_list = context["top_merchants"]
    anomalies = context["anomalies"]

    if matches:
        total = sum(abs(item["amount"]) for item in matches)
        lead = max(matches, key=lambda item: abs(item["amount"]))
        return (
            f"I found {len(matches)} relevant transaction"
            f"{'' if len(matches) == 1 else 's'} for {month_label} totaling "
            f"{format_money(total)}. The largest match is {lead['description']} "
            f"at {format_money(abs(lead['amount']))} in {lead['category']}."
        )

    lead_category = top_categories[0] if top_categories else None
    lead_merchant = top_merchants_list[0] if top_merchants_list else None
    if "why" in normalized or "explain" in normalized:
        driver = (
            f"{lead_category['category']} led categories at {format_money(lead_category['total'])}"
            if lead_category
            else f"spending totaled {format_money(summary_data['total_spending'])}"
        )
        merchant_text = (
            f", with {lead_merchant['merchant']} as the top merchant at {format_money(lead_merchant['total'])}"
            if lead_merchant
            else ""
        )
        anomaly_text = f" I also found {len(anomalies)} unusual charge(s)." if anomalies else ""
        return f"For {month_label}, {driver}{merchant_text}.{anomaly_text}"

    category_text = (
        f" Top category: {lead_category['category']} at {format_money(lead_category['total'])}."
        if lead_category
        else ""
    )
    merchant_text = (
        f" Top merchant: {lead_merchant['merchant']} at {format_money(lead_merchant['total'])}."
        if lead_merchant
        else ""
    )
    return (
        f"For {month_label}, spending was {format_money(summary_data['total_spending'])}, "
        f"income was {format_money(summary_data['total_income'])}, and net cash flow was "
        f"{format_money(summary_data['net'])}.{category_text}{merchant_text}"
    )


def latest_imported_month() -> str | None:
    months = available_months()
    return months[0]["month"] if months else None


def infer_follow_up_month(question: str) -> str | None:
    """Reuse the previous Q&A month when the question is clearly contextual."""
    if not looks_like_follow_up_question(question):
        return None

    history = list_ask_history(limit=1)
    if not history:
        return None
    return history[0]["month"]


def has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def looks_like_follow_up_question(question: str) -> bool:
    return (
        question.startswith(("what about", "how about", "and ", "also "))
        or has_any(question, ["same month", "that month", "for that period"])
    )


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


def looks_like_upload_history_question(question: str) -> bool:
    return (
        has_any(question, ["upload", "uploaded", "import", "imported", "statement", "statements", "file", "files"])
        and has_any(question, ["history", "recent", "latest", "which", "what", "show", "list"])
    )


def looks_like_monthly_report_question(question: str) -> bool:
    return (
        has_any(question, ["report", "insight", "insights", "overview", "checkup", "recap"])
        and has_any(question, ["month", "monthly", "spending", "finance", "financial"])
    )


def looks_like_forecast_question(question: str) -> bool:
    return (
        has_any(question, ["forecast", "project", "projected", "projection", "pace", "run rate", "expected"])
        or "rest of the month" in question
    )


def looks_like_contextual_question(question: str) -> bool:
    return (
        has_any(question, ["why", "explain", "pattern", "patterns", "stand out", "stood out", "tell me about"])
        or has_any(question, ["evidence", "cite", "cited", "supporting", "related"])
    )


def looks_like_budget_recommendation_question(question: str) -> bool:
    return (
        has_any(question, ["budget", "budgets"])
        and has_any(question, ["recommend", "recommended", "suggest", "suggested", "should"])
    )


def format_month_label(month: str | None) -> str:
    return month or "all imported data"


def format_money(value: float) -> str:
    return f"${value:,.2f}"


def dollars_to_cents(value: float) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
