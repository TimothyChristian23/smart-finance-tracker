"""API tests for the Smart Personal Finance Tracker backend."""
import json

import pytest
from fastapi.testclient import TestClient

from app import ai_categorization
from app.categorization import clean_merchant_description, merchant_key
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

MESSY_MERCHANT_CSV = """date,description,amount
2026-08-01,POS DEBIT TRADER JOE'S #1234 SAN FRANCISCO CA,-42.10
2026-08-02,TST*BLUE BOTTLE COFFEE 07/05 CA,-6.75
2026-08-03,PAYPAL *HULU 4029357733 CA,-14.99
2026-08-04,ACH CREDIT PAYROLL DEPOSIT ID 928372,3200.00
"""

CUSTOM_MAPPING_CSV = """Posted,Payee,Outflow,Inflow,Bucket,Wallet
10/01/2026,Farmers Market,42.37,,Food & Grocery,Travel Checking
10/02/2026,Payroll Deposit,,3200.00,Income,Travel Checking
"""


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setenv("FINANCE_DB_PATH", str(tmp_path / "finance.sqlite3"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_CATEGORY_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    reset_db()


def save_custom_csv_preset() -> dict:
    response = client.put(
        "/csv-mapping-presets",
        json={
            "name": "Travel Checking",
            "date_column": "Posted",
            "description_column": "Payee",
            "debit_column": "Outflow",
            "credit_column": "Inflow",
            "category_column": "Bucket",
            "account_column": "Wallet",
        },
    )
    assert response.status_code == 200
    return response.json()


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


def test_preview_includes_category_explanations_and_saved_rules():
    response = client.post(
        "/transactions/preview",
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    )

    assert response.status_code == 200
    trader_joes = next(
        row for row in response.json()["rows"]
        if row["description"] == "Trader Joes"
    )
    assert trader_joes["category"] == "Food & Grocery"
    assert trader_joes["suggested_category"] == "Food & Grocery"
    assert trader_joes["category_confidence"] == 0.92
    assert trader_joes["category_confidence_label"] == "high"
    assert trader_joes["category_source"] == "keyword_rule"
    assert trader_joes["category_source_label"] == "Merchant keyword"
    assert trader_joes["matched_terms"] == ["trader joe"]
    assert "trader joe" in trader_joes["category_reason"]

    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Trader Joes"
    )
    client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Dining", "remember": True},
    )

    saved_rule_preview = client.post(
        "/transactions/preview",
        files={"file": ("next.csv", SAMPLE_CSV, "text/csv")},
    ).json()
    saved_rule_row = next(
        row for row in saved_rule_preview["rows"]
        if row["description"] == "Trader Joes"
    )
    assert saved_rule_row["category"] == "Dining"
    assert saved_rule_row["suggested_category"] == "Dining"
    assert saved_rule_row["category_confidence"] == 0.99
    assert saved_rule_row["category_source"] == "saved_rule"
    assert "Saved merchant rule" in saved_rule_row["category_reason"]


def test_parse_transactions_csv_cleans_noisy_merchant_descriptions():
    rows = parse_transactions_csv(MESSY_MERCHANT_CSV, "messy.csv")

    assert [row["description"] for row in rows] == [
        "Trader Joes",
        "Blue Bottle Coffee",
        "Hulu",
        "Payroll Deposit",
    ]
    assert [row["category"] for row in rows] == [
        "Food & Grocery",
        "Dining",
        "Subscriptions",
        "Income",
    ]
    assert clean_merchant_description("CHECKCARD 1234 TRADER JOES STORE 45 CA") == "Trader Joes"
    assert merchant_key("POS DEBIT TRADER JOE'S #1234 SAN FRANCISCO CA") == "trader joes"


def test_saved_merchant_rule_matches_noisy_descriptor_variants():
    client.post("/transactions/upload", files={"file": ("messy.csv", MESSY_MERCHANT_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions", params={"month": "2026-08"}).json()
        if item["description"] == "Trader Joes"
    )

    response = client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Dining", "remember": True},
    )
    assert response.status_code == 200

    next_csv = """date,description,amount
2026-09-01,CHECKCARD 1234 TRADER JOES STORE 45 CA,-20.00
"""
    upload_response = client.post(
        "/transactions/upload",
        files={"file": ("next.csv", next_csv, "text/csv")},
    )

    assert upload_response.status_code == 200
    assert upload_response.json()["imported"] == 1
    imported = client.get(
        "/transactions",
        params={"month": "2026-09", "search": "Trader Joes"},
    ).json()
    assert imported[0]["description"] == "Trader Joes"
    assert imported[0]["category"] == "Dining"

    rules = client.get("/merchant-rules").json()
    assert rules[0]["merchant"] == "Trader Joes"
    assert rules[0]["merchant_key"] == "trader joes"
    assert rules[0]["category"] == "Dining"


def test_reviewed_import_preserves_edited_categories_and_logs_upload():
    response = client.post(
        "/transactions/import-reviewed",
        json={
            "filename": "reviewed.csv",
            "file_type": "csv",
            "account_name": "Rewards Card",
            "rows": [
                {
                    "date": "2026-07-02",
                    "description": "Trader Joes",
                    "amount": -86.42,
                    "category": "Dining",
                    "account_name": None,
                },
                {
                    "date": "2026-07-03",
                    "description": "Payroll Deposit",
                    "amount": 3200.00,
                    "category": "Income",
                    "account_name": None,
                },
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "filename": "reviewed.csv",
        "account_name": "Rewards Card",
        "imported": 2,
        "duplicates_skipped": 0,
    }

    transactions = client.get("/transactions?limit=10").json()
    by_description = {item["description"]: item for item in transactions}
    assert by_description["Trader Joes"]["category"] == "Dining"
    assert by_description["Trader Joes"]["account_name"] == "Rewards Card"
    assert by_description["Trader Joes"]["source_file"] == "reviewed.csv"

    uploads = client.get("/uploads").json()
    assert uploads[0]["filename"] == "reviewed.csv"
    assert uploads[0]["parsed_count"] == 2
    assert uploads[0]["imported_count"] == 2
    assert uploads[0]["account_name"] == "Rewards Card"


def test_reviewed_import_user_category_overrides_saved_merchant_rule():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Trader Joes"
    )
    client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Dining", "remember": True},
    )

    response = client.post(
        "/transactions/import-reviewed",
        json={
            "filename": "reviewed.csv",
            "file_type": "csv",
            "rows": [
                {
                    "date": "2026-08-02",
                    "description": "Trader Joes",
                    "amount": -50.00,
                    "category": "Food & Grocery",
                    "account_name": "Cash",
                },
            ],
        },
    )

    assert response.status_code == 200
    reviewed = client.get(
        "/transactions",
        params={"month": "2026-08", "search": "Trader Joes"},
    ).json()
    assert reviewed[0]["category"] == "Food & Grocery"
    assert reviewed[0]["account_name"] == "Cash"


def test_reviewed_import_validates_rows():
    response = client.post(
        "/transactions/import-reviewed",
        json={
            "filename": "reviewed.csv",
            "file_type": "csv",
            "rows": [
                {
                    "date": "not-a-date",
                    "description": "Mystery Store",
                    "amount": -12.34,
                    "category": "Mystery",
                },
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "rows[1]: invalid date 'not-a-date'"
    assert client.get("/transactions").json() == []


def test_preview_reports_csv_row_errors_without_importing():
    csv_content = """Date,Description,Amount
2026-07-01,Payroll Deposit,3200.00
bad-date,Trader Joes,-86.42
2026-07-03,Apartment Rent,-1450.00
2026-07-04,Missing Amount,
"""

    response = client.post(
        "/transactions/preview",
        files={"file": ("mixed.csv", csv_content, "text/csv")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["row_count"] == 2
    assert payload["importable_count"] == 2
    assert payload["duplicate_count"] == 0
    assert payload["total_income"] == 3200.0
    assert payload["total_spending"] == 1450.0
    assert [row["description"] for row in payload["rows"]] == ["Payroll Deposit", "Apartment Rent"]
    assert payload["errors"][0] == "Row 3: invalid date 'bad-date'"
    assert payload["errors"][1].startswith("Row 5: missing required column")

    empty_summary = client.get("/summary?month=2026-07").json()
    assert empty_summary["transaction_count"] == 0

    upload_response = client.post(
        "/transactions/upload",
        files={"file": ("mixed.csv", csv_content, "text/csv")},
    )
    assert upload_response.status_code == 400
    assert upload_response.json()["detail"] == "Row 3: invalid date 'bad-date'"


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


def test_import_quality_report_summarizes_uploaded_statement_health():
    empty = client.get("/imports/quality?month=2026-07").json()
    assert empty["status"] == "empty"
    assert empty["transaction_count"] == 0
    assert empty["notes"] == ["No imported transactions found for this view."]

    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.get("/imports/quality?month=2026-07")

    assert response.status_code == 200
    payload = response.json()
    assert payload["month"] == "2026-07"
    assert payload["status"] == "needs_review"
    assert payload["transaction_count"] == 11
    assert payload["upload_count"] == 2
    assert payload["duplicates_skipped"] == 11
    assert payload["latest_upload"]["imported_count"] == 0
    assert payload["latest_upload"]["duplicates_skipped"] == 11
    assert payload["anomaly_count"] >= 1
    assert payload["anomalies"][0]["description"] == "One-Time Electronics Store"
    assert any("duplicate" in note for note in payload["notes"])


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
    assert response.text.splitlines()[0] == "date,description,category,amount,source_file,account_name"
    assert "2026-07-05,Blue Bottle Coffee,Dining,-6.75,sample.csv," in response.text
    assert "Chipotle" not in response.text


def test_statement_uploads_can_be_labeled_and_filtered_by_account():
    preview_response = client.post(
        "/transactions/preview",
        data={"account_name": " Chase Checking "},
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    )

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["rows"][0]["account_name"] == "Chase Checking"

    response = client.post(
        "/transactions/upload",
        data={"account_name": " Chase Checking "},
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["account_name"] == "Chase Checking"
    assert client.get("/accounts").json() == ["Chase Checking"]

    transactions = client.get("/transactions", params={"account": "Chase Checking", "limit": 20}).json()
    assert len(transactions) == 11
    assert {item["account_name"] for item in transactions} == {"Chase Checking"}

    uploads = client.get("/uploads").json()
    assert uploads[0]["account_name"] == "Chase Checking"

    export_response = client.get(
        "/transactions/export",
        params={"account": "Chase Checking", "search": "rent"},
    )
    assert "2026-07-01,Apartment Rent,Housing,-1450.00,sample.csv,Chase Checking" in export_response.text

    backup = client.get("/data/export").json()
    assert backup["transactions"][0]["account_name"] == "Chase Checking"
    assert backup["uploads"][0]["account_name"] == "Chase Checking"


def test_create_manual_transaction_recalculates_analytics():
    response = client.post(
        "/transactions",
        json={
            "date": "2026-08-14",
            "description": "Cash Farmers Market",
            "amount": -42.37,
            "category": "Food & Grocery",
            "account_name": "Cash",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["date"] == "2026-08-14"
    assert payload["description"] == "Cash Farmers Market"
    assert payload["amount"] == -42.37
    assert payload["category"] == "Food & Grocery"
    assert payload["account_name"] == "Cash"
    assert payload["source_file"] == "manual"

    summary = client.get("/summary?month=2026-08").json()
    assert summary["total_spending"] == 42.37
    assert summary["total_income"] == 0
    assert summary["net"] == -42.37
    assert summary["transaction_count"] == 1
    assert summary["categories"] == [{"category": "Food & Grocery", "total": 42.37}]

    assert client.get("/accounts").json() == ["Cash"]
    filtered = client.get("/transactions", params={"account": "Cash"}).json()
    assert len(filtered) == 1
    assert filtered[0]["description"] == "Cash Farmers Market"


def test_create_manual_transaction_validates_inputs():
    bad_date = client.post(
        "/transactions",
        json={
            "date": "not-a-date",
            "description": "Cash Farmers Market",
            "amount": -42.37,
            "category": "Food & Grocery",
        },
    )
    bad_category = client.post(
        "/transactions",
        json={
            "date": "2026-08-14",
            "description": "Cash Farmers Market",
            "amount": -42.37,
            "category": "Mystery",
        },
    )
    blank_description = client.post(
        "/transactions",
        json={
            "date": "2026-08-14",
            "description": "   ",
            "amount": -42.37,
            "category": "Food & Grocery",
        },
    )

    assert bad_date.status_code == 400
    assert bad_date.json()["detail"] == "invalid date 'not-a-date'"
    assert bad_category.status_code == 400
    assert "category must be one of" in bad_category.json()["detail"]
    assert blank_description.status_code == 400
    assert blank_description.json()["detail"] == "description cannot be empty."
    assert client.get("/transactions").json() == []


def test_account_summary_groups_totals_by_account():
    client.post(
        "/transactions/upload",
        data={"account_name": "Chase Checking"},
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    )
    client.post(
        "/transactions",
        json={
            "date": "2026-07-14",
            "description": "Cash Lunch",
            "amount": -42.00,
            "category": "Dining",
            "account_name": "Cash",
        },
    )
    client.post(
        "/transactions",
        json={
            "date": "2026-07-15",
            "description": "Cash Gift",
            "amount": 125.00,
            "category": "Income",
            "account_name": "Cash",
        },
    )

    response = client.get("/accounts/summary?month=2026-07")

    assert response.status_code == 200
    by_account = {item["account_name"]: item for item in response.json()}
    assert set(by_account) == {"Cash", "Chase Checking"}
    assert by_account["Chase Checking"]["total_spending"] == 2840.87
    assert by_account["Chase Checking"]["total_income"] == 3200.0
    assert by_account["Chase Checking"]["net"] == 359.13
    assert by_account["Chase Checking"]["transaction_count"] == 11
    assert by_account["Cash"]["total_spending"] == 42.0
    assert by_account["Cash"]["total_income"] == 125.0
    assert by_account["Cash"]["net"] == 83.0
    assert by_account["Cash"]["transaction_count"] == 2

    bad_month = client.get("/accounts/summary?month=July")
    assert bad_month.status_code == 400
    assert bad_month.json()["detail"] == "month must use YYYY-MM format."


def test_update_transaction_details_recalculates_analytics():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Trader Joes"
    )

    response = client.patch(
        f"/transactions/{transaction['id']}",
        json={
            "date": "2026-07-03",
            "description": "Trader Joe's Market",
            "amount": -90.12,
            "category": "Dining",
            "account_name": "Rewards Card",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["date"] == "2026-07-03"
    assert payload["description"] == "Trader Joe's Market"
    assert payload["amount"] == -90.12
    assert payload["category"] == "Dining"
    assert payload["account_name"] == "Rewards Card"
    assert payload["source_file"] == "sample.csv"

    summary = client.get("/summary?month=2026-07").json()
    assert summary["total_spending"] == 2844.57

    dining = next(item for item in client.get("/categories?month=2026-07").json() if item["category"] == "Dining")
    assert dining["total"] == 149.69

    filtered = client.get("/transactions", params={"account": "Rewards Card"}).json()
    assert len(filtered) == 1
    assert filtered[0]["description"] == "Trader Joe's Market"


def test_transaction_splits_reallocate_category_analytics_and_budget_qna():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Amazon Marketplace"
    )

    response = client.put(
        f"/transactions/{transaction['id']}/splits",
        json={
            "splits": [
                {"category": "Dining", "amount": 25.20, "note": "Lunch supplies"},
                {"category": "Subscriptions", "amount": 40.00},
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_split"] is True
    assert payload["split_total"] == 65.2
    assert payload["unsplit_amount"] == 0
    assert [split["category"] for split in payload["splits"]] == ["Dining", "Subscriptions"]
    assert payload["splits"][0]["note"] == "Lunch supplies"
    assert client.get(f"/transactions/{transaction['id']}/splits").json()[1]["amount"] == 40.0

    summary = client.get("/summary?month=2026-07").json()
    categories = {item["category"]: item["total"] for item in summary["categories"]}
    assert summary["total_spending"] == 2840.87
    assert categories["Shopping"] == 899.0
    assert categories["Dining"] == 84.77
    assert categories["Subscriptions"] == 40.0

    client.put("/budgets", json={"month": "2026-07", "category": "Dining", "amount": 80})
    budget = client.get("/budgets?month=2026-07").json()[0]
    assert budget["spent"] == 84.77
    assert budget["status"] == "over"

    answer = client.post("/ask", json={"question": "How much did I spend on dining in July 2026?"}).json()
    assert answer["intent"] == "category_spending"
    assert answer["amount"] == 84.77

    filtered = client.get("/transactions", params={"month": "2026-07", "category": "Subscriptions"}).json()
    assert {item["description"] for item in filtered} == {"Amazon Marketplace"}

    cleared = client.delete(f"/transactions/{transaction['id']}/splits").json()
    assert cleared["is_split"] is False
    assert cleared["splits"] == []
    restored_categories = {
        item["category"]: item["total"]
        for item in client.get("/categories?month=2026-07").json()
    }
    assert restored_categories["Shopping"] == 964.2


def test_transaction_splits_validate_totals_category_uniqueness_and_expenses():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    expense = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Amazon Marketplace"
    )
    income = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Payroll Deposit"
    )

    mismatch = client.put(
        f"/transactions/{expense['id']}/splits",
        json={
            "splits": [
                {"category": "Dining", "amount": 20.00},
                {"category": "Shopping", "amount": 20.00},
            ],
        },
    )
    duplicate_categories = client.put(
        f"/transactions/{expense['id']}/splits",
        json={
            "splits": [
                {"category": "Dining", "amount": 25.20},
                {"category": "Dining", "amount": 40.00},
            ],
        },
    )
    income_split = client.put(
        f"/transactions/{income['id']}/splits",
        json={"splits": [{"category": "Income", "amount": 3200.00}]},
    )

    assert mismatch.status_code == 400
    assert mismatch.json()["detail"] == "Split amounts must equal the transaction expense amount."
    assert duplicate_categories.status_code == 400
    assert duplicate_categories.json()["detail"] == "split categories must be unique."
    assert income_split.status_code == 400
    assert income_split.json()["detail"] == "Only expense transactions can be split."
    assert client.put("/transactions/999999/splits", json={"splits": [{"category": "Dining", "amount": 1}]}).status_code == 404


def test_update_transaction_details_validates_inputs():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = client.get("/transactions").json()[0]

    bad_date = client.patch(f"/transactions/{transaction['id']}", json={"date": "not-a-date"})
    bad_category = client.patch(f"/transactions/{transaction['id']}", json={"category": "Mystery"})

    assert bad_date.status_code == 400
    assert bad_date.json()["detail"] == "invalid date 'not-a-date'"
    assert bad_category.status_code == 400
    assert "category must be one of" in bad_category.json()["detail"]
    assert client.patch("/transactions/999999", json={"description": "Missing"}).status_code == 404


def test_delete_transaction_recalculates_analytics():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Amazon Marketplace"
    )

    response = client.delete(f"/transactions/{transaction['id']}")

    assert response.status_code == 200
    assert response.json() == {"message": "Transaction deleted."}
    assert client.delete(f"/transactions/{transaction['id']}").status_code == 404

    summary = client.get("/summary?month=2026-07").json()
    assert summary["transaction_count"] == 10
    assert summary["total_spending"] == 2775.67

    descriptions = [
        item["description"]
        for item in client.get("/transactions", params={"limit": 20}).json()
    ]
    assert "Amazon Marketplace" not in descriptions


def test_data_export_backup_includes_local_finance_records():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Trader Joes"
    )
    client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Dining", "remember": False},
    )
    client.post(f"/categories/review/{transaction['id']}/ignore")
    client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Dining", "remember": True},
    )
    client.put(
        "/budgets",
        json={"month": "2026-07", "category": "Dining", "amount": 200},
    )
    client.post("/recurring/ignored", json={"merchant": "Netflix Subscription"})
    save_custom_csv_preset()
    anomaly = next(
        item for item in client.get("/anomalies?month=2026-07").json()
        if item["description"] == "One-Time Electronics Store"
    )
    client.post(f"/anomalies/{anomaly['id']}/ignore")

    response = client.get("/data/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "finance-backup-" in response.headers["content-disposition"]
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["counts"] == {
        "transactions": 11,
        "transaction_splits": 0,
        "uploads": 1,
        "merchant_rules": 1,
        "category_review_ignores": 1,
        "recurring_ignores": 1,
        "anomaly_ignores": 1,
        "csv_import_presets": 1,
        "budgets": 1,
        "ask_history": 0,
        "months": 1,
    }
    assert payload["summary"]["total_spending"] == 2840.87
    assert payload["months"][0]["month"] == "2026-07"
    assert payload["uploads"][0]["filename"] == "sample.csv"
    assert payload["merchant_rules"][0]["merchant"] == "Trader Joes"
    assert payload["category_review_ignores"][0]["merchant"] == "Trader Joes"
    assert payload["category_review_ignores"][0]["current_category"] == "Dining"
    assert payload["category_review_ignores"][0]["suggested_category"] == "Food & Grocery"
    assert payload["recurring_ignores"][0]["merchant"] == "Netflix Subscription"
    assert payload["anomaly_ignores"][0]["transaction"]["description"] == "One-Time Electronics Store"
    assert payload["csv_import_presets"][0]["name"] == "Travel Checking"
    assert payload["budgets"][0]["category"] == "Dining"
    assert any(
        item["description"] == "Trader Joes" and item["category"] == "Dining"
        for item in payload["transactions"]
    )


def test_data_import_restores_backup_and_recalculates_analytics():
    client.post(
        "/transactions/upload",
        data={"account_name": "Chase Checking"},
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    )
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Trader Joes"
    )
    client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Dining", "remember": False},
    )
    client.post(f"/categories/review/{transaction['id']}/ignore")
    client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Dining", "remember": True},
    )
    client.put("/budgets", json={"month": "2026-07", "category": "Dining", "amount": 200})
    client.post("/recurring/ignored", json={"merchant": "Netflix Subscription"})
    save_custom_csv_preset()
    anomaly = next(
        item for item in client.get("/anomalies?month=2026-07").json()
        if item["description"] == "One-Time Electronics Store"
    )
    client.post(f"/anomalies/{anomaly['id']}/ignore")
    client.post("/ask", json={"question": "How much did I spend on food in 2026-07?"})
    backup = client.get("/data/export").json()

    client.delete("/data", params={"confirmation": "RESET"})
    assert client.get("/summary?month=2026-07").json()["transaction_count"] == 0

    response = client.post(
        "/data/import",
        data={"confirmation": "RESTORE"},
        files={"file": ("backup.json", json.dumps(backup), "application/json")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": "Backup restored.",
        "counts": {
            "transactions": 11,
            "transaction_splits": 0,
            "uploads": 1,
            "merchant_rules": 1,
            "category_review_ignores": 1,
            "recurring_ignores": 1,
            "anomaly_ignores": 1,
            "csv_import_presets": 1,
            "budgets": 1,
            "ask_history": 1,
        },
    }

    summary = client.get("/summary?month=2026-07").json()
    assert summary["total_spending"] == 2840.87
    assert summary["transaction_count"] == 11
    assert client.get("/uploads").json()[0]["account_name"] == "Chase Checking"
    assert client.get("/merchant-rules").json()[0]["merchant"] == "Trader Joes"
    assert client.get("/categories/review/ignored").json()[0]["merchant"] == "Trader Joes"
    assert client.get("/recurring/ignored").json()[0]["merchant"] == "Netflix Subscription"
    assert client.get("/anomalies/ignored").json()[0]["transaction"]["description"] == "One-Time Electronics Store"
    assert client.get("/csv-mapping-presets").json()[0]["name"] == "Travel Checking"
    assert client.get("/budgets?month=2026-07").json()[0]["category"] == "Dining"
    assert client.get("/ask/history").json()[0]["question"] == "How much did I spend on food in 2026-07?"
    assert client.get("/accounts/summary?month=2026-07").json()[0]["account_name"] == "Chase Checking"


def test_data_backup_restores_transaction_splits():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Amazon Marketplace"
    )
    client.put(
        f"/transactions/{transaction['id']}/splits",
        json={
            "splits": [
                {"category": "Dining", "amount": 25.20, "note": "Lunch supplies"},
                {"category": "Subscriptions", "amount": 40.00},
            ],
        },
    )

    backup = client.get("/data/export").json()

    assert backup["counts"]["transaction_splits"] == 2
    backed_up_transaction = next(
        item for item in backup["transactions"]
        if item["description"] == "Amazon Marketplace"
    )
    assert [split["category"] for split in backed_up_transaction["splits"]] == ["Dining", "Subscriptions"]

    client.delete("/data", params={"confirmation": "RESET"})
    response = client.post(
        "/data/import",
        data={"confirmation": "RESTORE"},
        files={"file": ("backup.json", json.dumps(backup), "application/json")},
    )

    assert response.status_code == 200
    assert response.json()["counts"]["transaction_splits"] == 2
    restored_transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Amazon Marketplace"
    )
    assert restored_transaction["is_split"] is True
    assert restored_transaction["splits"][0]["note"] == "Lunch supplies"
    categories = {
        item["category"]: item["total"]
        for item in client.get("/summary?month=2026-07").json()["categories"]
    }
    assert categories["Dining"] == 84.77
    assert categories["Subscriptions"] == 40.0


def test_data_import_requires_confirmation_and_valid_backup():
    client.post(
        "/transactions",
        json={
            "date": "2026-08-14",
            "description": "Cash Farmers Market",
            "amount": -42.37,
            "category": "Food & Grocery",
            "account_name": "Cash",
        },
    )

    rejected = client.post(
        "/data/import",
        files={"file": ("backup.json", "{}", "application/json")},
    )
    bad_json = client.post(
        "/data/import",
        data={"confirmation": "RESTORE"},
        files={"file": ("backup.json", "{not json", "application/json")},
    )
    unsupported = client.post(
        "/data/import",
        data={"confirmation": "RESTORE"},
        files={"file": ("backup.json", json.dumps({"schema_version": 99}), "application/json")},
    )
    malformed = client.post(
        "/data/import",
        data={"confirmation": "RESTORE"},
        files={
            "file": (
                "backup.json",
                json.dumps({
                    "schema_version": 1,
                    "transactions": [{
                        "date": "bad-date",
                        "description": "Cash Farmers Market",
                        "amount": -42.37,
                        "category": "Food & Grocery",
                    }],
                }),
                "application/json",
            )
        },
    )

    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "Type RESTORE to replace local finance data from a backup."
    assert bad_json.status_code == 400
    assert bad_json.json()["detail"] == "Backup file must contain valid JSON."
    assert unsupported.status_code == 400
    assert unsupported.json()["detail"] == "Unsupported backup schema version."
    assert malformed.status_code == 400
    assert malformed.json()["detail"] == "transactions[1].date must use YYYY-MM-DD format."

    summary = client.get("/summary?month=2026-08").json()
    assert summary["transaction_count"] == 1
    assert summary["total_spending"] == 42.37


def test_clear_all_data_requires_confirmation_and_removes_local_records():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    transaction = next(
        item for item in client.get("/transactions").json()
        if item["description"] == "Trader Joes"
    )
    client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Dining", "remember": False},
    )
    client.post(f"/categories/review/{transaction['id']}/ignore")
    client.patch(
        f"/transactions/{transaction['id']}/category",
        json={"category": "Dining", "remember": True},
    )
    client.put(
        "/budgets",
        json={"month": "2026-07", "category": "Dining", "amount": 200},
    )
    client.post("/recurring/ignored", json={"merchant": "Netflix Subscription"})
    save_custom_csv_preset()
    anomaly = next(
        item for item in client.get("/anomalies?month=2026-07").json()
        if item["description"] == "One-Time Electronics Store"
    )
    client.post(f"/anomalies/{anomaly['id']}/ignore")
    client.post("/ask", json={"question": "How much did I spend on food in 2026-07?"})

    rejected = client.delete("/data")

    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "Type RESET to clear all local finance data."
    assert client.get("/data/export").json()["counts"]["transactions"] == 11

    response = client.delete("/data", params={"confirmation": "RESET"})

    assert response.status_code == 200
    assert response.json() == {"message": "All local finance data cleared."}
    backup = client.get("/data/export").json()
    assert backup["counts"] == {
        "transactions": 0,
        "transaction_splits": 0,
        "uploads": 0,
        "merchant_rules": 0,
        "category_review_ignores": 0,
        "recurring_ignores": 0,
        "anomaly_ignores": 0,
        "csv_import_presets": 0,
        "budgets": 0,
        "ask_history": 0,
        "months": 0,
    }
    assert client.get("/transactions").json() == []
    assert client.get("/uploads").json() == []
    assert client.get("/merchant-rules").json() == []
    assert client.get("/categories/review/ignored").json() == []
    assert client.get("/recurring/ignored").json() == []
    assert client.get("/anomalies/ignored").json() == []
    assert client.get("/csv-mapping-presets").json() == []
    assert client.get("/budgets?month=2026-07").json() == []
    assert client.get("/ask/history").json() == []


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


def test_direct_merchant_rule_can_apply_to_existing_and_future_imports():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.put(
        "/merchant-rules",
        json={
            "merchant": "AMAZON MKTPLACE PMTS 123456 CA",
            "category": "Subscriptions",
            "apply_existing": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["updated_transactions"] == 1
    assert payload["rule"] == {
        "id": payload["rule"]["id"],
        "merchant": "Amazon Marketplace",
        "merchant_key": "amazon marketplace",
        "category": "Subscriptions",
        "updated_at": payload["rule"]["updated_at"],
    }

    amazon = next(
        item for item in client.get("/transactions", params={"search": "Amazon"}).json()
        if item["source_file"] == "sample.csv"
    )
    assert amazon["category"] == "Subscriptions"

    next_csv = """date,description,amount
2026-08-03,CARD PURCHASE AMAZON MKTPLACE PMTS 998877 WA,-24.99
"""
    client.post("/transactions/upload", files={"file": ("next.csv", next_csv, "text/csv")})
    next_amazon = next(
        item for item in client.get("/transactions", params={"month": "2026-08", "search": "Amazon"}).json()
        if item["source_file"] == "next.csv"
    )
    assert next_amazon["description"] == "Amazon Marketplace"
    assert next_amazon["category"] == "Subscriptions"


def test_direct_merchant_rule_validates_category():
    response = client.put(
        "/merchant-rules",
        json={"merchant": "Amazon", "category": "Mystery"},
    )

    assert response.status_code == 400
    assert "category must be one of" in response.json()["detail"]


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
    assert by_description["Gym Membership"]["confidence"] == 0.77
    assert by_description["Gym Membership"]["confidence_label"] == "medium"
    assert by_description["Gym Membership"]["category_source"] == "category_signals"
    assert by_description["Gym Membership"]["matched_terms"] == ["gym"]
    assert by_description["Gym Membership"]["action"] == "update"
    assert by_description["Random Shop"]["suggested_category"] == "Shopping"
    assert by_description["Random Shop"]["category_source_label"] == "Category signals"

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


def test_category_review_suggestions_can_be_dismissed_and_restored():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})
    queue = client.get("/categories/review?month=2026-07").json()
    gym = next(
        item for item in queue
        if item["transaction"]["description"] == "Gym Membership"
    )

    response = client.post(f"/categories/review/{gym['transaction']['id']}/ignore")

    assert response.status_code == 200
    ignored = response.json()
    assert ignored["merchant"] == "Gym Membership"
    assert ignored["current_category"] == "Other"
    assert ignored["suggested_category"] == "Health"
    assert ignored["category_source"] == "category_signals"
    assert ignored["category_source_label"] == "Category signals"

    updated_queue = client.get("/categories/review?month=2026-07").json()
    assert "Gym Membership" not in {
        item["transaction"]["description"]
        for item in updated_queue
    }
    assert client.get("/imports/quality?month=2026-07").json()["review_count"] == 1
    ignored_list = client.get("/categories/review/ignored").json()
    assert ignored_list[0]["merchant"] == "Gym Membership"

    restore_response = client.delete(f"/categories/review/ignored/{ignored['id']}")

    assert restore_response.status_code == 200
    restored_queue = client.get("/categories/review?month=2026-07").json()
    assert "Gym Membership" in {
        item["transaction"]["description"]
        for item in restored_queue
    }
    assert client.get("/categories/review/ignored").json() == []
    assert client.delete(f"/categories/review/ignored/{ignored['id']}").status_code == 404
    assert client.post("/categories/review/999999/ignore").status_code == 404


def test_ai_categorization_status_and_disabled_review_guard():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})
    preview = client.post(
        "/transactions/preview",
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    ).json()

    status = client.get("/ai/categorization/status")
    assert status.status_code == 200
    assert status.json() == {
        "enabled": False,
        "model": "gpt-5-nano",
        "message": "Set OPENAI_API_KEY to enable AI category suggestions.",
    }

    response = client.post("/categories/review/ai?month=2026-07")
    assert response.status_code == 400
    assert response.json()["detail"] == "AI categorization is disabled. Set OPENAI_API_KEY to enable it."

    disabled_preview = client.post("/transactions/preview/ai", json={"rows": preview["rows"]})
    assert disabled_preview.status_code == 400
    assert disabled_preview.json()["detail"] == "AI categorization is disabled. Set OPENAI_API_KEY to enable it."


def test_ai_category_review_uses_mocked_openai_response(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_CATEGORY_MODEL", "test-model")
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})
    captured = {}

    def fake_openai_response(payload, api_key, timeout):
        captured["payload"] = payload
        captured["api_key"] = api_key
        captured["timeout"] = timeout
        request_data = json.loads(payload["input"][1]["content"][0]["text"])
        suggestions = []
        for transaction in request_data["transactions"]:
            suggestions.append({
                "transaction_id": transaction["id"],
                "category": "Health" if transaction["merchant"] == "Gym Membership" else "Shopping",
                "confidence": 0.91,
                "reason": f"{transaction['merchant']} looks like a known expense type.",
                "matched_terms": [transaction["merchant"]],
            })
        return {"output_text": json.dumps({"suggestions": suggestions})}

    monkeypatch.setattr(ai_categorization, "_post_openai_response", fake_openai_response)

    response = client.post("/categories/review/ai?month=2026-07&limit=6")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["model"] == "test-model"
    assert "to OpenAI" in payload["warning"]
    assert captured["api_key"] == "test-key"
    assert captured["timeout"] == 20
    assert captured["payload"]["store"] is False
    assert captured["payload"]["max_output_tokens"] == 700
    request_data = json.loads(captured["payload"]["input"][1]["content"][0]["text"])
    assert "Income" not in request_data["allowed_categories"]
    assert {item["merchant"] for item in request_data["transactions"]} == {"Gym Membership", "Random Shop"}

    by_description = {
        item["transaction"]["description"]: item
        for item in payload["suggestions"]
    }
    assert by_description["Gym Membership"]["suggested_category"] == "Health"
    assert by_description["Gym Membership"]["category_source"] == "ai_suggestion"
    assert by_description["Gym Membership"]["category_source_label"] == "AI suggestion"
    assert by_description["Gym Membership"]["confidence_label"] == "high"
    assert by_description["Random Shop"]["suggested_category"] == "Shopping"


def test_ai_preview_categories_updates_unsaved_rows_and_totals(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_CATEGORY_MODEL", "test-model")
    preview = client.post(
        "/transactions/preview",
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    ).json()
    captured = {}

    def fake_openai_response(payload, api_key, timeout):
        captured["payload"] = payload
        captured["api_key"] = api_key
        captured["timeout"] = timeout
        request_data = json.loads(payload["input"][1]["content"][0]["text"])
        suggestions = []
        for row in request_data["transactions"]:
            suggestions.append({
                "transaction_id": row["id"],
                "category": "Dining" if row["merchant"] == "Amazon Marketplace" else row["current_category"],
                "confidence": 0.88,
                "reason": f"{row['merchant']} was checked during preview.",
                "matched_terms": [row["merchant"]],
            })
        return {"output_text": json.dumps({"suggestions": suggestions})}

    monkeypatch.setattr(ai_categorization, "_post_openai_response", fake_openai_response)

    response = client.post("/transactions/preview/ai", json={"rows": preview["rows"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["model"] == "test-model"
    assert "to OpenAI" in payload["warning"]
    assert captured["api_key"] == "test-key"
    assert captured["timeout"] == 20
    assert captured["payload"]["store"] is False
    request_data = json.loads(captured["payload"]["input"][1]["content"][0]["text"])
    assert "Income" not in request_data["allowed_categories"]
    assert all(item["amount"] > 0 for item in request_data["transactions"])
    assert "Payroll Deposit" not in {item["merchant"] for item in request_data["transactions"]}

    amazon = next(row for row in payload["rows"] if row["description"] == "Amazon Marketplace")
    assert amazon["category"] == "Dining"
    assert amazon["category_source"] == "ai_suggestion"
    assert amazon["category_source_label"] == "AI suggestion"
    assert amazon["category_confidence"] == 0.88

    categories = {item["category"]: item["total"] for item in payload["categories"]}
    assert categories["Dining"] == 124.77
    assert categories["Shopping"] == 899.0
    assert client.get("/summary?month=2026-07").json()["transaction_count"] == 0


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


def test_follow_up_questions_reuse_previous_qa_month():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    client.post("/ask", json={"question": "How much did I spend on food in 2026-07?"})

    response = client.post("/ask", json={"question": "What about housing?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "You spent $1,450.00 on Housing for 2026-07."
    assert payload["amount"] == 1450.0
    assert payload["categories"] == ["Housing"]
    assert payload["month"] == "2026-07"
    assert payload["intent"] == "category_spending"

    history = client.get("/ask/history").json()
    assert history[0]["question"] == "What about housing?"
    assert history[0]["month"] == "2026-07"


def test_anomalies_include_large_category_outlier():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.get("/anomalies")

    assert response.status_code == 200
    descriptions = [item["description"] for item in response.json()]
    assert "One-Time Electronics Store" in descriptions


def test_anomaly_ignore_hides_and_restores_outlier_everywhere():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})
    target = next(
        item for item in client.get("/anomalies?month=2026-07").json()
        if item["description"] == "One-Time Electronics Store"
    )

    response = client.post(f"/anomalies/{target['id']}/ignore")

    assert response.status_code == 200
    hidden = response.json()
    assert hidden["transaction"]["description"] == "One-Time Electronics Store"
    assert hidden["transaction"]["amount"] == -899.0
    assert hidden["transaction_key"]
    assert client.get("/anomalies/ignored").json()[0]["transaction"]["description"] == "One-Time Electronics Store"

    active = client.get("/anomalies?month=2026-07").json()
    active_descriptions = {item["description"] for item in active}
    assert "One-Time Electronics Store" not in active_descriptions
    assert "DoorDash" in active_descriptions

    insights = client.get("/insights/monthly?month=2026-07").json()
    assert insights["anomaly_count"] == len(active)

    answer = client.post("/ask", json={"question": "Any unusual charges in 2026-07?"}).json()
    assert answer["intent"] == "anomalies"
    assert all(item["description"] != "One-Time Electronics Store" for item in answer["data"])

    restore_response = client.delete(f"/anomalies/ignored/{hidden['id']}")

    assert restore_response.status_code == 200
    restored = {
        item["description"]
        for item in client.get("/anomalies?month=2026-07").json()
    }
    assert "One-Time Electronics Store" in restored
    assert client.get("/anomalies/ignored").json() == []


def test_anomaly_ignore_returns_not_found_for_missing_transaction():
    response = client.post("/anomalies/999999/ignore")

    assert response.status_code == 404
    assert response.json()["detail"] == "Transaction not found."


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


def test_top_merchants_groups_noisy_descriptor_variants():
    client.post(
        "/transactions",
        json={
            "date": "2026-08-01",
            "description": "POS DEBIT TRADER JOE'S #1234 SAN FRANCISCO CA",
            "amount": -42.10,
            "category": "Food & Grocery",
        },
    )
    client.post(
        "/transactions",
        json={
            "date": "2026-08-05",
            "description": "CHECKCARD 1234 TRADER JOES STORE 45 CA",
            "amount": -20.00,
            "category": "Food & Grocery",
        },
    )

    response = client.get("/merchants?month=2026-08&limit=1")

    assert response.status_code == 200
    assert response.json()[0] == {
        "merchant": "Trader Joes",
        "category": "Food & Grocery",
        "total": 62.1,
        "transaction_count": 2,
    }


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
    assert netflix["merchant_key"] == "netflix subscription"
    assert netflix["average_amount"] == 15.99
    assert netflix["total_amount"] == 47.97
    assert netflix["occurrences"] == 3
    assert netflix["cadence"] == "monthly"
    assert netflix["first_seen"] == "2026-05-03"
    assert netflix["last_seen"] == "2026-07-03"
    assert netflix["next_expected_date"] == "2026-08-03"


def test_recurring_bill_calendar_projects_expected_bills_by_month():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})

    response = client.get("/recurring/calendar?month=2026-08")

    assert response.status_code == 200
    payload = response.json()
    assert payload["month"] == "2026-08"
    assert payload["total_expected"] == 60.66
    assert [item["merchant"] for item in payload["items"]] == ["Netflix Subscription", "Gym Membership"]
    assert [item["date"] for item in payload["items"]] == ["2026-08-03", "2026-08-15"]
    assert payload["items"][0]["amount"] == 15.99

    future = client.get("/recurring/calendar?month=2026-09").json()
    assert [item["date"] for item in future["items"]] == ["2026-09-03", "2026-09-15"]


def test_recurring_ignore_hides_merchants_from_detection_and_forecast():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})

    response = client.post("/recurring/ignored", json={"merchant": "NETFLIX SUBSCRIPTION 1234 CA"})

    assert response.status_code == 200
    hidden = response.json()
    assert hidden["merchant"] == "Netflix Subscription"
    assert hidden["merchant_key"] == "netflix subscription"
    assert client.get("/recurring/ignored").json()[0]["merchant"] == "Netflix Subscription"

    active_merchants = {
        item["merchant"]
        for item in client.get("/recurring").json()
    }
    assert active_merchants == {"Gym Membership"}

    forecast = client.get("/forecast/monthly?month=2026-08").json()
    assert forecast["upcoming_recurring_total"] == 44.67
    assert {item["merchant"] for item in forecast["upcoming_recurring"]} == {"Gym Membership"}

    calendar = client.get("/recurring/calendar?month=2026-08").json()
    assert calendar["total_expected"] == 44.67
    assert {item["merchant"] for item in calendar["items"]} == {"Gym Membership"}

    restore_response = client.delete(f"/recurring/ignored/{hidden['id']}")
    assert restore_response.status_code == 200
    assert {
        item["merchant"]
        for item in client.get("/recurring").json()
    } == {"Gym Membership", "Netflix Subscription"}


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


def test_ask_handles_account_summary_questions():
    client.post(
        "/transactions/upload",
        data={"account_name": "Chase Checking"},
        files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")},
    )
    client.post(
        "/transactions",
        json={
            "date": "2026-07-14",
            "description": "Cash Lunch",
            "amount": -42.00,
            "category": "Dining",
            "account_name": "Cash",
        },
    )

    spending = client.post(
        "/ask",
        json={"question": "How much did I spend on Chase Checking in July 2026?"},
    ).json()
    net = client.post(
        "/ask",
        json={"question": "What was my net for Cash in 2026-07?"},
    ).json()

    assert spending["intent"] == "account_summary"
    assert spending["amount"] == 2840.87
    assert spending["month"] == "2026-07"
    assert spending["data"][0]["account_name"] == "Chase Checking"
    assert "Spending for Chase Checking in 2026-07 was $2,840.87." == spending["answer"]
    assert net["intent"] == "account_summary"
    assert net["amount"] == -42.0
    assert net["data"][0]["account_name"] == "Cash"
    assert net["answer"] == "Net cash flow for Cash in 2026-07 was $-42.00."


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


def test_ask_handles_recurring_bill_calendar_questions():
    client.post("/transactions/upload", files={"file": ("recurring.csv", RECURRING_CSV, "text/csv")})

    response = client.post("/ask", json={"question": "What bills are due in August 2026?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "recurring_bill_calendar"
    assert payload["amount"] == 60.66
    assert payload["month"] == "2026-08"
    assert "Next up is Netflix Subscription on 2026-08-03" in payload["answer"]
    assert [item["merchant"] for item in payload["data"]] == ["Netflix Subscription", "Gym Membership"]


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


def test_ask_explains_transaction_category_assignment():
    client.post("/transactions/upload", files={"file": ("sample.csv", SAMPLE_CSV, "text/csv")})

    response = client.post(
        "/ask",
        json={"question": "Why was Trader Joes categorized as Food & Grocery in July 2026?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "category_explanation"
    assert payload["amount"] == 86.42
    assert payload["categories"] == ["Food & Grocery"]
    assert payload["month"] == "2026-07"
    assert "Trader Joes is currently Food & Grocery." in payload["answer"]
    assert "92% confidence" in payload["answer"]
    assert "trader joe" in payload["answer"]
    assert payload["data"][0]["transaction"]["description"] == "Trader Joes"
    assert payload["data"][0]["category_source"] == "keyword_rule"


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


def test_parse_transactions_csv_supports_common_bank_aliases_and_signs():
    csv_content = """Posting Date,Transaction Description,Debit Amount,Credit Amount,Transaction Type,Spending Category
07/01/2026,Payroll Deposit,,"$3,200.00",Credit,
07/02/2026,Trader Joes,$86.42,,Debit,
07/03/2026,Apartment Rent,-1450.00,,Debit,
07/04/2026,Blue Bottle Coffee,($6.75),,Debit,Dining
,,,,,
"""

    rows = parse_transactions_csv(csv_content, "bank-export.csv")

    assert len(rows) == 4
    assert [row["date"] for row in rows] == ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"]
    assert [row["amount_cents"] for row in rows] == [320000, -8642, -145000, -675]
    assert rows[1]["category"] == "Food & Grocery"
    assert rows[3]["category"] == "Dining"


def test_parse_transactions_csv_uses_type_for_unsigned_amounts():
    csv_content = """Trans Date,Payee,Transaction Amount,Type
07/01/26,Amazon Marketplace,65.20,Debit
07/02/26,Payroll Deposit,3200.00,Credit
"""

    rows = parse_transactions_csv(csv_content, "typed-export.csv")

    assert rows[0]["date"] == "2026-07-01"
    assert rows[0]["amount_cents"] == -6520
    assert rows[1]["amount_cents"] == 320000


def test_csv_mapping_preset_drives_preview_and_upload_for_custom_headers():
    preset = save_custom_csv_preset()
    assert preset["name"] == "Travel Checking"
    assert preset["debit_column"] == "Outflow"
    assert client.get("/csv-mapping-presets").json()[0]["name"] == "Travel Checking"

    preview = client.post(
        "/transactions/preview",
        data={"csv_preset_id": str(preset["id"])},
        files={"file": ("custom.csv", CUSTOM_MAPPING_CSV, "text/csv")},
    )

    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["row_count"] == 2
    assert preview_payload["rows"][0]["date"] == "2026-10-01"
    assert preview_payload["rows"][0]["description"] == "Farmers Market"
    assert preview_payload["rows"][0]["amount"] == -42.37
    assert preview_payload["rows"][0]["category"] == "Food & Grocery"
    assert preview_payload["rows"][0]["account_name"] == "Travel Checking"

    upload = client.post(
        "/transactions/upload",
        data={"csv_preset_id": str(preset["id"])},
        files={"file": ("custom.csv", CUSTOM_MAPPING_CSV, "text/csv")},
    )

    assert upload.status_code == 200
    assert upload.json()["imported"] == 2
    transactions = client.get("/transactions", params={"month": "2026-10", "limit": 10}).json()
    assert [item["description"] for item in transactions] == ["Payroll Deposit", "Farmers Market"]
    assert {item["account_name"] for item in transactions} == {"Travel Checking"}

    delete_response = client.delete(f"/csv-mapping-presets/{preset['id']}")
    assert delete_response.status_code == 200
    assert client.get("/csv-mapping-presets").json() == []


def test_csv_mapping_preset_validates_required_amount_and_duplicate_columns():
    missing_amount = client.put(
        "/csv-mapping-presets",
        json={
            "name": "Broken",
            "date_column": "Posted",
            "description_column": "Payee",
        },
    )
    duplicate_columns = client.put(
        "/csv-mapping-presets",
        json={
            "name": "Duplicate",
            "date_column": "Posted",
            "description_column": "Payee",
            "amount_column": "Posted",
        },
    )

    assert missing_amount.status_code == 400
    assert missing_amount.json()["detail"] == "CSV mapping must include an amount, debit, or credit column."
    assert duplicate_columns.status_code == 400
    assert duplicate_columns.json()["detail"] == "CSV mapped columns must be unique."


def test_csv_mapping_preset_missing_id_and_bad_header_are_reported():
    missing_preset = client.post(
        "/transactions/preview",
        data={"csv_preset_id": "999999"},
        files={"file": ("custom.csv", CUSTOM_MAPPING_CSV, "text/csv")},
    )

    preset = client.put(
        "/csv-mapping-presets",
        json={
            "name": "Broken Headers",
            "date_column": "Missing Date",
            "description_column": "Payee",
            "debit_column": "Outflow",
        },
    ).json()
    bad_header = client.post(
        "/transactions/preview",
        data={"csv_preset_id": str(preset["id"])},
        files={"file": ("custom.csv", CUSTOM_MAPPING_CSV, "text/csv")},
    )

    assert missing_preset.status_code == 404
    assert missing_preset.json()["detail"] == "CSV mapping preset not found."
    assert bad_header.status_code == 200
    assert bad_header.json()["errors"] == ["CSV is missing mapped column: Missing Date"]
    assert bad_header.json()["row_count"] == 0


def test_money_to_cents_handles_parentheses():
    assert money_to_cents("($42.19)") == -4219
    assert money_to_cents("42.19-") == -4219


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
