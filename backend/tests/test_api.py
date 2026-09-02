"""API tests for the Smart Personal Finance Tracker backend."""
import pytest
from fastapi.testclient import TestClient

from app.database import reset_db
from app.main import app, infer_month, money_to_cents, parse_transactions_csv

client = TestClient(app)

SAMPLE_CSV = """date,description,amount
2026-07-01,Payroll Deposit,3200.00
2026-07-01,Apartment Rent,-1450.00
2026-07-02,Trader Joes,-86.42
2026-07-05,Blue Bottle Coffee,-6.75
2026-07-08,Whole Foods Market,-112.37
2026-07-09,Amazon Marketplace,-65.20
2026-07-10,Chipotle,-14.88
2026-07-16,Safeway,-73.18
2026-07-22,DoorDash,-37.94
2026-07-28,Whole Foods Market,-95.13
2026-07-29,One-Time Electronics Store,-899.00
"""

RECURRING_CSV = """date,description,amount
2026-05-03,Netflix Subscription,-15.99
2026-06-03,Netflix Subscription,-15.99
2026-07-03,Netflix Subscription,-15.99
2026-05-15,Gym Membership,-44.00
2026-06-15,Gym Membership,-44.00
2026-07-15,Gym Membership,-46.00
2026-07-20,Random Shop,-20.00
"""


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setenv("FINANCE_DB_PATH", str(tmp_path / "finance.sqlite3"))
    reset_db()


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_transactions_and_monthly_summary():
    response = client.post(
        "/transactions/upload",
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported"] == 11
    assert payload["duplicates_skipped"] == 0

    response = client.get("/summary?month=2026-07")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_income"] == 3200.0
    assert payload["total_spending"] == 2840.87
    assert payload["transaction_count"] == 11
    assert payload["categories"][0] == {"category": "Housing", "total": 1450.0}


def test_preview_transactions_does_not_import_and_marks_duplicates():
    response = client.post(
        "/transactions/preview",
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"] == "sample.csv"
    assert payload["file_type"] == "csv"
    assert payload["row_count"] == 11
    assert payload["importable_count"] == 11
    assert payload["duplicate_count"] == 0
    assert payload["first_transaction_date"] == "2026-07-01"
    assert payload["last_transaction_date"] == "2026-07-29"
    assert payload["total_spending"] == 2840.87
    assert payload["total_income"] == 3200.0
    assert payload["rows"][0]["description"] == "Payroll Deposit"
    assert payload["rows"][0]["duplicate"] is False

    empty_summary = client.get("/summary?month=2026-07").json()
    assert empty_summary["transaction_count"] == 0

    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    duplicate_preview = client.post(
        "/transactions/preview",
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    ).json()
    assert duplicate_preview["importable_count"] == 0
    assert duplicate_preview["duplicate_count"] == 11
    assert all(row["duplicate"] for row in duplicate_preview["rows"])


def test_duplicate_upload_skips_existing_transactions():
    first = client.post(
        "/transactions/upload",
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    )
    second = client.post(
        "/transactions/upload",
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["imported"] == 0
    assert second.json()["duplicates_skipped"] == 11

    summary = client.get("/summary?month=2026-07").json()
    assert summary["transaction_count"] == 11
    assert summary["total_spending"] == 2840.87


def test_upload_history_records_import_counts_and_duplicates():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.get("/uploads")

    assert response.status_code == 200
    uploads = response.json()
    assert len(uploads) == 2
    assert uploads[0]["filename"] == "sample.csv"
    assert uploads[0]["file_type"] == "csv"
    assert uploads[0]["parsed_count"] == 11
    assert uploads[0]["imported_count"] == 0
    assert uploads[0]["duplicates_skipped"] == 11
    assert uploads[0]["first_transaction_date"] == "2026-07-01"
    assert uploads[0]["last_transaction_date"] == "2026-07-29"
    assert uploads[1]["imported_count"] == 11
    assert uploads[1]["duplicates_skipped"] == 0


def test_transactions_filter_by_month_category_and_search():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.get(
        "/transactions",
        params={
            "month": "2026-07",
            "category": "Food & Grocery",
            "search": "whole",
            "limit": 10,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["description"] for item in payload] == [
        "Whole Foods Market",
        "Whole Foods Market",
    ]
    assert {item["category"] for item in payload} == {"Food & Grocery"}

    rejected = client.get("/transactions", params={"category": "Mystery"})
    assert rejected.status_code == 400
    assert "category must be one of" in rejected.json()["detail"]


def test_transaction_export_matches_filters():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.get(
        "/transactions/export",
        params={
            "month": "2026-07",
            "category": "Dining",
            "search": "coffee",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="transactions-2026-07.csv"'
    assert response.text.splitlines()[0] == "date,description,category,amount,source_file"
    assert "2026-07-05,Blue Bottle Coffee,Dining,-6.75,sample.csv" in response.text
    assert "Chipotle" not in response.text


def test_data_export_backup_includes_local_finance_records():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Trader Joes"
    )
    client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Dining", "remember": True},
    )
    client.put(
        "/budgets",
        json={"month": "2026-07", "category": "Dining", "amount": 200},
    )

    response = client.get("/data/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "finance-backup-" in response.headers["content-disposition"]
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["counts"] == {
        "transactions": 11,
        "uploads": 1,
        "merchant_rules": 1,
        "budgets": 1,
        "ask_history": 0,
        "months": 1,
    }
    assert payload["summary"]["total_spending"] == 2840.87
    assert payload["months"][0]["month"] == "2026-07"
    assert payload["uploads"][0]["filename"] == "sample.csv"
    assert payload["merchant_rules"][0]["merchant"] == "Trader Joes"
    assert payload["budgets"][0]["category"] == "Dining"
    assert any(
        item["description"] == "Trader Joes" and item["category"] == "Dining"
        for item in payload["transactions"]
    )


def test_update_transaction_category_recalculates_summary():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "One-Time Electronics Store"
    )

    response = client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Other"},
    )

    assert response.status_code == 200
    assert response.json()["category"] == "Other"

    totals = {
        item["category"]: item["total"]
        for item in client.get("/categories?month=2026-07").json()
    }
    assert totals["Other"] == 899.0
    assert totals["Shopping"] == 65.2


def test_remembered_merchant_rule_applies_to_future_imports():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Trader Joes"
    )

    response = client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Dining", "remember": True},
    )

    assert response.status_code == 200
    assert response.json()["category"] == "Dining"

    rules = client.get("/merchant-rules").json()
    assert rules == [{
        "id": rules[0]["id"],
        "merchant": "Trader Joes",
        "merchant_key": "trader joes",
        "category": "Dining",
        "updated_at": rules[0]["updated_at"],
    }]

    client.post("/transactions/upload", files={"file": ("next.csv", SAMPLE_CSV, "text/csv")})
    transactions = client.get("/transactions?limit=50").json()
    next_trader_joes = next(
        item for item in transactions
        if item["description"] == "Trader Joes" and item["source_file"] == "next.csv"
    )
    assert next_trader_joes["category"] == "Dining"

    delete_response = client.delete(f"/merchant-rules/{rules[0]['id']}")
    assert delete_response.status_code == 200
    assert client.get("/merchant-rules").json() == []


def test_unknown_transaction_category_is_rejected():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = client.get("/transactions").json()[0]

    response = client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Mystery"},
    )

    assert response.status_code == 400
    assert "category must be one of" in response.json()["detail"]


def test_category_review_queue_suggests_uncertain_updates():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})

    response = client.get("/categories/review?month=2026-07")

    assert response.status_code == 200
    payload = response.json()
    by_description = {
        item["transaction"]["description"]: item
        for item in payload
    }
    assert set(by_description) == {"Gym Membership", "Random Shop"}
    assert by_description["Gym Membership"]["current_category"] == "Other"
    assert by_description["Gym Membership"]["suggested_category"] == "Health"
    assert by_description["Gym Membership"]["confidence"] == 0.74
    assert by_description["Gym Membership"]["action"] == "update"
    assert by_description["Random Shop"]["suggested_category"] == "Shopping"

    gym_id = by_description["Gym Membership"]["transaction"]["id"]
    update_response = client.patch(
        f"/transactions/{gym_id}/category",
        json={"category": "Health", "remember": True},
    )
    assert update_response.status_code == 200

    updated_queue = client.get("/categories/review?month=2026-07").json()
    assert "Gym Membership" not in {
        item["transaction"]["description"]
        for item in updated_queue
    }


def test_pdf_upload_imports_text_statement_rows():
    pdf_bytes = make_pdf_bytes([
        "Account Statement",
        "Date Description Amount Balance",
        "2026-09-01 Payroll Deposit 3200.00 4200.00",
        "2026-09-02 Trader Joes -86.42 4113.58",
        "09/05/2026 Blue Bottle Coffee ($6.75) 4106.83",
        "2026-09-10 Apartment Rent -1450.00 2656.83",
    ])

    response = client.post(
        "/transactions/upload",
        files={"file": ("statement.pdf", pdf_bytes, "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported"] == 4
    assert payload["duplicates_skipped"] == 0

    summary = client.get("/summary?month=2026-09").json()
    assert summary["total_income"] == 3200.0
    assert summary["total_spending"] == 1543.17
    assert summary["transaction_count"] == 4
    assert summary["categories"][0] == {"category": "Housing", "total": 1450.0}


def test_ask_food_question_uses_exact_transaction_totals():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.post("/ask", json={"question": "How much did I spend on food in 2026-07?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["amount"] == 426.67
    assert payload["categories"] == ["Food & Grocery", "Dining"]
    assert "You spent $426.67" in payload["answer"]


def test_ask_history_records_recent_answers():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    food_question = "How much did I spend on food in 2026-07?"
    income_question = "What was my income in 2026-07?"

    client.post("/ask", json={"question": food_question})
    client.post("/ask", json={"question": income_question})

    response = client.get("/ask/history")

    assert response.status_code == 200
    payload = response.json()
    assert [item["question"] for item in payload] == [income_question, food_question]
    assert payload[0]["answer"] == "Your income for 2026-07 was $3,200.00."
    assert payload[0]["amount"] == 3200.0
    assert payload[0]["month"] == "2026-07"
    assert payload[0]["intent"] == "income"
    assert payload[1]["amount"] == 426.67
    assert payload[1]["categories"] == ["Food & Grocery", "Dining"]

    limited = client.get("/ask/history?limit=1").json()
    assert [item["question"] for item in limited] == [income_question]

    backup = client.get("/data/export").json()
    assert backup["counts"]["ask_history"] == 2
    assert backup["ask_history"][0]["question"] == income_question


def test_anomalies_include_large_category_outlier():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.get("/anomalies")

    assert response.status_code == 200
    descriptions = [item["description"] for item in response.json()]
    assert "One-Time Electronics Store" in descriptions


def test_analytics_endpoints_return_months_categories_trends_and_merchants():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    months = client.get("/months").json()
    categories = client.get("/categories?month=2026-07").json()
    trends = client.get("/trends").json()
    merchants = client.get("/merchants?month=2026-07&limit=2").json()
    largest = client.get("/expenses/largest?month=2026-07&limit=1").json()

    assert months[0]["month"] == "2026-07"
    assert months[0]["total_spending"] == 2840.87
    assert categories[:2] == [
        {"category": "Housing", "total": 1450.0, "transaction_count": 1},
        {"category": "Shopping", "total": 964.2, "transaction_count": 2},
    ]
    assert trends == [months[0]]
    assert merchants[0] == {
        "merchant": "Apartment Rent",
        "category": "Housing",
        "total": 1450.0,
        "transaction_count": 1,
    }
    assert largest[0]["description"] == "Apartment Rent"


def test_budget_progress_uses_live_category_spending():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.put(
        "/budgets",
        json={"month": "2026-07", "category": "Food & Grocery", "amount": 300},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["category"] == "Food & Grocery"
    assert payload["amount"] == 300.0
    assert payload["spent"] == 367.1
    assert payload["remaining"] == -67.1
    assert payload["percent_used"] == 122.4
    assert payload["status"] == "over"

    updated = client.put(
        "/budgets",
        json={"month": "2026-07", "category": "Food & Grocery", "amount": 500},
    ).json()
    assert updated["id"] == payload["id"]
    assert updated["remaining"] == 132.9
    assert updated["status"] == "on_track"

    budgets = client.get("/budgets?month=2026-07").json()
    assert len(budgets) == 1
    assert budgets[0]["amount"] == 500.0

    delete_response = client.delete(f"/budgets/{payload['id']}")
    assert delete_response.status_code == 200
    assert client.get("/budgets?month=2026-07").json() == []


def test_budget_api_rejects_unknown_category():
    response = client.put(
        "/budgets",
        json={"month": "2026-07", "category": "Mystery", "amount": 300},
    )

    assert response.status_code == 400
    assert "category must be one of" in response.json()["detail"]


def test_budget_recommendations_use_history_and_existing_budgets():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})
    client.put("/budgets", json={"month": "2026-08", "category": "Other", "amount": 40})

    response = client.get("/budgets/recommendations?month=2026-08")

    assert response.status_code == 200
    payload = response.json()
    categories = {item["category"]: item for item in payload}
    assert set(categories) == {"Other", "Subscriptions"}
    assert categories["Other"]["recommended_amount"] == 60.0
    assert categories["Other"]["baseline_average"] == 51.33
    assert categories["Other"]["recurring_amount"] == 44.67
    assert categories["Other"]["existing_budget"] == 40.0
    assert categories["Other"]["difference_from_existing"] == 20.0
    assert categories["Other"]["action"] == "raise"
    assert categories["Other"]["confidence"] == "high"
    assert categories["Subscriptions"]["recommended_amount"] == 20.0
    assert categories["Subscriptions"]["recurring_amount"] == 15.99


def test_monthly_insights_composes_report_signals():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    client.put("/budgets", json={"month": "2026-07", "category": "Housing", "amount": 1200})

    response = client.get("/insights/monthly?month=2026-07")

    assert response.status_code == 200
    payload = response.json()
    assert payload["month"] == "2026-07"
    assert payload["summary"]["total_spending"] == 2840.87
    assert payload["top_category"]["category"] == "Housing"
    assert payload["top_merchant"]["merchant"] == "Apartment Rent"
    assert payload["largest_expense"]["description"] == "Apartment Rent"
    assert payload["over_budget_count"] == 1
    assert payload["anomaly_count"] == 2
    assert "Spending was $2,840.87" in payload["highlights"][0]
    assert any("Housing is $250.00 over budget." == item for item in payload["risks"])
    assert any("Review over-budget categories: Housing." == item for item in payload["next_actions"])


def test_monthly_forecast_projects_upcoming_recurring_charges():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})

    response = client.get("/forecast/monthly?month=2026-08")

    assert response.status_code == 200
    payload = response.json()
    assert payload["month"] == "2026-08"
    assert payload["status"] == "negative_cash_flow"
    assert payload["confidence"] == "low"
    assert payload["actual_spending"] == 0
    assert payload["projected_spending"] == 60.66
    assert payload["projected_net"] == -60.66
    assert payload["upcoming_recurring_total"] == 60.66
    assert {item["merchant"] for item in payload["upcoming_recurring"]} == {
        "Gym Membership",
        "Netflix Subscription",
    }
    assert "Upcoming recurring charges add $60.66." in payload["notes"]


def test_recurring_endpoint_detects_monthly_charges():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})

    response = client.get("/recurring")

    assert response.status_code == 200
    payload = response.json()
    merchants = {item["merchant"]: item for item in payload}
    assert set(merchants) == {"Gym Membership", "Netflix Subscription"}

    netflix = merchants["Netflix Subscription"]
    assert netflix["average_amount"] == 15.99
    assert netflix["total_amount"] == 47.97
    assert netflix["occurrences"] == 3
    assert netflix["cadence"] == "monthly"
    assert netflix["first_seen"] == "2026-05-03"
    assert netflix["last_seen"] == "2026-07-03"
    assert netflix["next_expected_date"] == "2026-08-03"


def test_ask_handles_spending_income_and_ranking_questions():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    total_spending = client.post("/ask", json={"question": "What was my total spending in July 2026?"}).json()
    income = client.post("/ask", json={"question": "What was my income in 2026-07?"}).json()
    top_category = client.post("/ask", json={"question": "What was my biggest category in 2026-07?"}).json()
    top_merchant = client.post("/ask", json={"question": "Who was my top merchant in 2026-07?"}).json()
    largest = client.post("/ask", json={"question": "What was my largest expense in 2026-07?"}).json()
    anomalies = client.post("/ask", json={"question": "Any unusual charges in 2026-07?"}).json()

    assert total_spending["intent"] == "total_spending"
    assert total_spending["amount"] == 2840.87
    assert income["intent"] == "income"
    assert income["amount"] == 3200.0
    assert top_category["intent"] == "top_category"
    assert top_category["categories"] == ["Housing"]
    assert top_merchant["intent"] == "top_merchants"
    assert top_merchant["data"][0]["merchant"] == "Apartment Rent"
    assert largest["intent"] == "largest_expenses"
    assert largest["data"][0]["description"] == "Apartment Rent"
    assert anomalies["intent"] == "anomalies"
    assert anomalies["data"][0]["description"] == "One-Time Electronics Store"


def test_ask_handles_budget_questions():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    client.put("/budgets", json={"month": "2026-07", "category": "Housing", "amount": 1200})
    client.put("/budgets", json={"month": "2026-07", "category": "Shopping", "amount": 1200})

    response = client.post("/ask", json={"question": "Am I over budget in July 2026?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "budgets"
    assert payload["amount"] == 250.0
    assert payload["categories"] == ["Housing"]
    assert "Housing is $250.00 over" in payload["answer"]


def test_ask_handles_budget_recommendation_questions():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})

    response = client.post("/ask", json={"question": "What budgets do you recommend for August 2026?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "budget_recommendations"
    assert payload["amount"] == 60.0
    assert payload["month"] == "2026-08"
    assert payload["categories"] == ["Other", "Subscriptions"]
    assert "I recommend starting with Other at $60.00" in payload["answer"]
    assert payload["data"][0]["action"] == "create"


def test_ask_handles_monthly_report_questions():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    client.put("/budgets", json={"month": "2026-07", "category": "Housing", "amount": 1200})

    response = client.post("/ask", json={"question": "Give me my monthly report for July 2026"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "monthly_insights"
    assert payload["amount"] == 2840.87
    assert payload["categories"] == ["Housing"]
    assert payload["month"] == "2026-07"
    assert "For 2026-07, you spent $2,840.87" in payload["answer"]
    assert "Housing is $250.00 over budget." in payload["answer"]
    assert payload["data"][0]["top_category"]["category"] == "Housing"


def test_ask_handles_forecast_questions():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})

    response = client.post("/ask", json={"question": "What am I projected to spend in August 2026?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "monthly_forecast"
    assert payload["amount"] == 60.66
    assert payload["month"] == "2026-08"
    assert "projected spending is $60.66" in payload["answer"]
    assert "Upcoming recurring charges add $60.66." in payload["answer"]
    assert payload["data"][0]["upcoming_recurring_total"] == 60.66


def test_ask_returns_cited_evidence_for_broad_questions():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.post("/ask", json={"question": "Tell me about Amazon in July 2026"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "evidence_answer"
    assert payload["amount"] == 65.2
    assert payload["month"] == "2026-07"
    assert payload["data"][0]["description"] == "Amazon Marketplace"
    assert "I found 1 relevant transaction" in payload["answer"]
    assert any(citation["type"] == "summary" for citation in payload["citations"])
    assert any(citation["title"] == "Amazon Marketplace" for citation in payload["citations"])


def test_ask_explains_contextual_category_questions_with_evidence():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.post("/ask", json={"question": "Why was shopping high in July 2026?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "evidence_answer"
    assert payload["amount"] == 964.2
    assert payload["categories"] == ["Shopping"]
    assert {item["description"] for item in payload["data"]} == {
        "Amazon Marketplace",
        "One-Time Electronics Store",
    }
    assert any(citation["title"] == "One-Time Electronics Store" for citation in payload["citations"])


def test_ask_handles_upload_history_questions():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.post("/ask", json={"question": "What files have I uploaded?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "upload_history"
    assert payload["data"][0]["filename"] == "sample.csv"
    assert "sample.csv" in payload["answer"]


def test_ask_handles_recurring_charge_questions():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})

    response = client.post("/ask", json={"question": "What subscriptions do I have?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "recurring_charges"
    assert payload["amount"] == 15.99
    assert "Netflix Subscription" in payload["answer"]
    assert len(payload["data"]) == 2


def test_parse_transactions_csv_supports_debit_credit_columns():
    csv_content = """Date,Description,Debit,Credit
07/01/2026,Bookstore,12.30,
07/02/2026,Refund,,4.10
"""

    rows = parse_transactions_csv(csv_content, "sample.csv")

    assert rows[0]["amount_cents"] == -1230
    assert rows[1]["amount_cents"] == 410


def test_money_to_cents_handles_parentheses():
    assert money_to_cents("($42.19)") == -4219


def test_infer_month_supports_named_months():
    assert infer_month("How much did I spend in July 2026?") == "2026-07"


def make_pdf_bytes(lines: list[str]) -> bytes:
    """Create a tiny text PDF fixture for parser tests."""
    stream_lines = ["BT", "/F1 12 Tf", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            stream_lines.append("0 -16 Td")
        stream_lines.append(f"({escape_pdf_text(line)}) Tj")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines).encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    parts = [b"%PDF-1.4\n"]
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(sum(len(part) for part in parts))
        parts.append(f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")

    xref_offset = sum(len(part) for part in parts)
    parts.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    parts.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        parts.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    parts.append(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return b"".join(parts)


def escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
