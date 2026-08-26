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
    assert response.json()["imported"] == 11

    response = client.get("/summary?month=2026-07")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_income"] == 3200.0
    assert payload["total_spending"] == 2840.87
    assert payload["transaction_count"] == 11
    assert payload["categories"][0] == {"category": "Housing", "total": 1450.0}


def test_ask_food_question_uses_exact_transaction_totals():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.post("/ask", json={"question": "How much did I spend on food in 2026-07?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["amount"] == 426.67
    assert payload["categories"] == ["Food & Grocery", "Dining"]
    assert "You spent $426.67" in payload["answer"]


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
