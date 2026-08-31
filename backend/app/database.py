"""SQLite storage and analytics helpers."""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from app.categorization import normalize_text

load_dotenv()


def get_db_path() -> Path:
    """Return the configured SQLite database path."""
    return Path(os.getenv("FINANCE_DB_PATH", "./data/finance.sqlite3"))


def connect() -> sqlite3.Connection:
    """Open a SQLite connection and initialize schema."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create database tables if they do not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_date TEXT NOT NULL,
            description TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            category TEXT NOT NULL,
            source_file TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_date
        ON transactions (transaction_date)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_category
        ON transactions (category)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS merchant_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_key TEXT NOT NULL UNIQUE,
            merchant_name TEXT NOT NULL,
            category TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            category TEXT NOT NULL,
            amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(month, category)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_budgets_month
        ON budgets (month)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS upload_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            file_type TEXT NOT NULL,
            parsed_count INTEGER NOT NULL,
            imported_count INTEGER NOT NULL,
            duplicates_skipped INTEGER NOT NULL,
            first_transaction_date TEXT,
            last_transaction_date TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def reset_db() -> None:
    """Delete imported transaction data."""
    with connect() as conn:
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM upload_history")
        conn.commit()


def insert_transactions(rows: list[dict]) -> dict:
    """Insert parsed transactions and return inserted/skipped counts."""
    result = {"inserted": 0, "skipped": 0}
    if not rows:
        return result

    with connect() as conn:
        for row in rows:
            category = merchant_rule_for_description(conn, row["description"]) or row["category"]
            values = (
                row["date"],
                row["description"],
                row["amount_cents"],
                category,
                row.get("source_file"),
            )
            if transaction_exists(conn, values):
                result["skipped"] += 1
                continue

            conn.execute(
                """
                INSERT INTO transactions (
                    transaction_date,
                    description,
                    amount_cents,
                    category,
                    source_file
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                values,
            )
            result["inserted"] += 1
        conn.commit()
    return result


def record_upload(filename: str, file_type: str, rows: list[dict], result: dict) -> dict:
    """Record a successful statement upload."""
    dates = sorted(row["date"] for row in rows if row.get("date"))
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO upload_history (
                filename,
                file_type,
                parsed_count,
                imported_count,
                duplicates_skipped,
                first_transaction_date,
                last_transaction_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                file_type,
                len(rows),
                result["inserted"],
                result["skipped"],
                dates[0] if dates else None,
                dates[-1] if dates else None,
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, filename, file_type, parsed_count, imported_count,
                   duplicates_skipped, first_transaction_date, last_transaction_date,
                   created_at
            FROM upload_history
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return _upload_row_to_dict(row)


def transaction_exists(conn: sqlite3.Connection, values: tuple) -> bool:
    """Return whether an equivalent transaction from the same source exists."""
    transaction_date, description, amount_cents, _category, source_file = values
    if source_file is None:
        source_sql = "source_file IS NULL"
        params = [transaction_date, description, amount_cents]
    else:
        source_sql = "source_file = ?"
        params = [transaction_date, description, amount_cents, source_file]

    row = conn.execute(
        f"""
        SELECT 1
        FROM transactions
        WHERE transaction_date = ?
          AND description = ?
          AND amount_cents = ?
          AND {source_sql}
        LIMIT 1
        """,
        params,
    ).fetchone()
    return row is not None


def merchant_rule_for_description(conn: sqlite3.Connection, description: str) -> str | None:
    """Return the learned category for an exact merchant description, if any."""
    row = conn.execute(
        """
        SELECT category
        FROM merchant_rules
        WHERE merchant_key = ?
        """,
        (normalize_text(description),),
    ).fetchone()
    return row["category"] if row else None


def list_merchant_rules() -> list[dict]:
    """Return saved merchant category rules."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, merchant_name, merchant_key, category, updated_at
            FROM merchant_rules
            ORDER BY merchant_name
            """
        ).fetchall()
    return [_merchant_rule_row_to_dict(row) for row in rows]


def update_transaction_category(transaction_id: int, category: str, remember: bool = False) -> dict | None:
    """Update one transaction category and optionally save a merchant rule."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, transaction_date, description, amount_cents, category, source_file
            FROM transactions
            WHERE id = ?
            """,
            (transaction_id,),
        ).fetchone()
        if row is None:
            return None

        merchant_key = normalize_text(row["description"])
        if remember:
            upsert_merchant_rule(conn, row["description"], category)
            conn.execute(
                """
                UPDATE transactions
                SET category = ?
                WHERE id = ?
                   OR lower(trim(description)) = ?
                """,
                (category, transaction_id, merchant_key),
            )
        else:
            conn.execute(
                """
                UPDATE transactions
                SET category = ?
                WHERE id = ?
                """,
                (category, transaction_id),
            )
        conn.commit()

        updated = conn.execute(
            """
            SELECT id, transaction_date, description, amount_cents, category, source_file
            FROM transactions
            WHERE id = ?
            """,
            (transaction_id,),
        ).fetchone()
    return _transaction_row_to_dict(updated)


def upsert_merchant_rule(conn: sqlite3.Connection, merchant_name: str, category: str) -> None:
    """Insert or update an exact merchant category rule."""
    conn.execute(
        """
        INSERT INTO merchant_rules (merchant_key, merchant_name, category)
        VALUES (?, ?, ?)
        ON CONFLICT(merchant_key) DO UPDATE SET
            merchant_name = excluded.merchant_name,
            category = excluded.category,
            updated_at = CURRENT_TIMESTAMP
        """,
        (normalize_text(merchant_name), merchant_name, category),
    )


def delete_merchant_rule(rule_id: int) -> bool:
    """Delete a merchant rule by id."""
    with connect() as conn:
        cursor = conn.execute(
            """
            DELETE FROM merchant_rules
            WHERE id = ?
            """,
            (rule_id,),
        )
        conn.commit()
    return cursor.rowcount > 0


def budget_progress(month: str) -> list[dict]:
    """Return monthly budget targets with live spending progress."""
    where_sql, params = _month_filter(month)
    with connect() as conn:
        spending_rows = conn.execute(
            f"""
            SELECT category, COALESCE(SUM(ABS(amount_cents)), 0) AS spent_cents
            FROM transactions
            {where_sql}
              {"AND" if where_sql else "WHERE"} amount_cents < 0
            GROUP BY category
            """,
            params,
        ).fetchall()
        budget_rows = conn.execute(
            """
            SELECT id, month, category, amount_cents, updated_at
            FROM budgets
            WHERE month = ?
            ORDER BY category
            """,
            (month,),
        ).fetchall()

    spending_by_category = {
        row["category"]: int(row["spent_cents"])
        for row in spending_rows
    }
    return [
        _budget_row_to_dict(row, spending_by_category.get(row["category"], 0))
        for row in budget_rows
    ]


def upsert_budget(month: str, category: str, amount_cents: int) -> dict:
    """Create or update a monthly category budget."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO budgets (month, category, amount_cents)
            VALUES (?, ?, ?)
            ON CONFLICT(month, category) DO UPDATE SET
                amount_cents = excluded.amount_cents,
                updated_at = CURRENT_TIMESTAMP
            """,
            (month, category, amount_cents),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, month, category, amount_cents, updated_at
            FROM budgets
            WHERE month = ? AND category = ?
            """,
            (month, category),
        ).fetchone()

    progress = budget_progress(month)
    return next(item for item in progress if item["id"] == row["id"])


def delete_budget(budget_id: int) -> bool:
    """Delete a monthly category budget by id."""
    with connect() as conn:
        cursor = conn.execute(
            """
            DELETE FROM budgets
            WHERE id = ?
            """,
            (budget_id,),
        )
        conn.commit()
    return cursor.rowcount > 0


def list_uploads(limit: int = 20) -> list[dict]:
    """Return recent statement upload history."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, filename, file_type, parsed_count, imported_count,
                   duplicates_skipped, first_transaction_date, last_transaction_date,
                   created_at
            FROM upload_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_upload_row_to_dict(row) for row in rows]


def list_transactions(
    limit: int = 200,
    month: str | None = None,
    category: str | None = None,
    search: str | None = None,
) -> list[dict]:
    """Return transactions with optional month, category, and description filters."""
    where_sql, params = _transaction_filter(month=month, category=category, search=search)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, transaction_date, description, amount_cents, category, source_file
            FROM transactions
            {where_sql}
            ORDER BY transaction_date DESC, id DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [_transaction_row_to_dict(row) for row in rows]


def monthly_summary(month: str | None = None) -> dict:
    """Return spending, income, and category totals for a month or all data."""
    where_sql, params = _month_filter(month)
    with connect() as conn:
        totals = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN amount_cents < 0 THEN ABS(amount_cents) ELSE 0 END), 0) AS spending_cents,
                COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END), 0) AS income_cents,
                COALESCE(SUM(amount_cents), 0) AS net_cents,
                COUNT(*) AS transaction_count
            FROM transactions
            {where_sql}
            """,
            params,
        ).fetchone()
        category_rows = conn.execute(
            f"""
            SELECT category, COALESCE(SUM(ABS(amount_cents)), 0) AS total_cents
            FROM transactions
            {where_sql}
              {"AND" if where_sql else "WHERE"} amount_cents < 0
            GROUP BY category
            ORDER BY total_cents DESC
            """,
            params,
        ).fetchall()

    return {
        "month": month,
        "total_spending": cents_to_dollars(totals["spending_cents"]),
        "total_income": cents_to_dollars(totals["income_cents"]),
        "net": cents_to_dollars(totals["net_cents"]),
        "transaction_count": totals["transaction_count"],
        "categories": [
            {
                "category": row["category"],
                "total": cents_to_dollars(row["total_cents"]),
            }
            for row in category_rows
        ],
    }


def available_months() -> list[dict]:
    """Return imported months with high-level totals."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                substr(transaction_date, 1, 7) AS month,
                COALESCE(SUM(CASE WHEN amount_cents < 0 THEN ABS(amount_cents) ELSE 0 END), 0) AS spending_cents,
                COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END), 0) AS income_cents,
                COALESCE(SUM(amount_cents), 0) AS net_cents,
                COUNT(*) AS transaction_count
            FROM transactions
            GROUP BY month
            ORDER BY month DESC
            """
        ).fetchall()

    return [
        {
            "month": row["month"],
            "total_spending": cents_to_dollars(row["spending_cents"]),
            "total_income": cents_to_dollars(row["income_cents"]),
            "net": cents_to_dollars(row["net_cents"]),
            "transaction_count": row["transaction_count"],
        }
        for row in rows
    ]


def category_totals(month: str | None = None) -> list[dict]:
    """Return category totals and transaction counts for expenses."""
    where_sql, params = _month_filter(month)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                category,
                COALESCE(SUM(ABS(amount_cents)), 0) AS total_cents,
                COUNT(*) AS transaction_count
            FROM transactions
            {where_sql}
              {"AND" if where_sql else "WHERE"} amount_cents < 0
            GROUP BY category
            ORDER BY total_cents DESC
            """,
            params,
        ).fetchall()

    return [
        {
            "category": row["category"],
            "total": cents_to_dollars(row["total_cents"]),
            "transaction_count": row["transaction_count"],
        }
        for row in rows
    ]


def monthly_trends(limit: int = 12) -> list[dict]:
    """Return month-by-month totals in chronological order."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                substr(transaction_date, 1, 7) AS month,
                COALESCE(SUM(CASE WHEN amount_cents < 0 THEN ABS(amount_cents) ELSE 0 END), 0) AS spending_cents,
                COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END), 0) AS income_cents,
                COALESCE(SUM(amount_cents), 0) AS net_cents,
                COUNT(*) AS transaction_count
            FROM transactions
            GROUP BY month
            ORDER BY month DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        {
            "month": row["month"],
            "total_spending": cents_to_dollars(row["spending_cents"]),
            "total_income": cents_to_dollars(row["income_cents"]),
            "net": cents_to_dollars(row["net_cents"]),
            "transaction_count": row["transaction_count"],
        }
        for row in reversed(rows)
    ]


def top_merchants(month: str | None = None, limit: int = 10) -> list[dict]:
    """Return top expense merchants by total spending."""
    where_sql, params = _month_filter(month)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                description,
                category,
                COALESCE(SUM(ABS(amount_cents)), 0) AS total_cents,
                COUNT(*) AS transaction_count
            FROM transactions
            {where_sql}
              {"AND" if where_sql else "WHERE"} amount_cents < 0
            GROUP BY description, category
            ORDER BY total_cents DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()

    return [
        {
            "merchant": row["description"],
            "category": row["category"],
            "total": cents_to_dollars(row["total_cents"]),
            "transaction_count": row["transaction_count"],
        }
        for row in rows
    ]


def largest_expenses(month: str | None = None, limit: int = 10) -> list[dict]:
    """Return largest individual expenses."""
    where_sql, params = _month_filter(month)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, transaction_date, description, amount_cents, category, source_file
            FROM transactions
            {where_sql}
              {"AND" if where_sql else "WHERE"} amount_cents < 0
            ORDER BY ABS(amount_cents) DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [_transaction_row_to_dict(row) for row in rows]


def recurring_charges(limit: int = 10, min_occurrences: int = 3) -> list[dict]:
    """Detect likely recurring expenses from repeated merchant descriptions."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT transaction_date, description, amount_cents, category
            FROM transactions
            WHERE amount_cents < 0
            ORDER BY lower(trim(description)), transaction_date
            """
        ).fetchall()

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(normalize_text(row["description"]), []).append(row)

    charges = []
    for items in grouped.values():
        if len(items) < min_occurrences:
            continue

        months = {item["transaction_date"][:7] for item in items}
        if len(months) < min_occurrences:
            continue

        parsed_dates = [date.fromisoformat(item["transaction_date"]) for item in items]
        gaps = [
            (parsed_dates[index] - parsed_dates[index - 1]).days
            for index in range(1, len(parsed_dates))
        ]
        cadence = _recurring_cadence(gaps)
        if cadence is None:
            continue

        amounts = [abs(int(item["amount_cents"])) for item in items]
        average_cents = int((sum(amounts) / len(amounts)) + 0.5)
        total_cents = sum(amounts)
        max_difference = max(abs(amount - average_cents) for amount in amounts)
        stability = 1 - min(1, max_difference / max(average_cents, 1))
        confidence = round(min(0.99, 0.6 + (stability * 0.35)), 2)
        expected_gap = int((sum(gaps) / len(gaps)) + 0.5) if gaps else 30
        next_expected_date = (parsed_dates[-1] + timedelta(days=expected_gap)).isoformat()

        charges.append({
            "merchant": items[-1]["description"],
            "category": items[-1]["category"],
            "average_amount": cents_to_dollars(average_cents),
            "total_amount": cents_to_dollars(total_cents),
            "occurrences": len(items),
            "first_seen": parsed_dates[0].isoformat(),
            "last_seen": parsed_dates[-1].isoformat(),
            "next_expected_date": next_expected_date,
            "cadence": cadence,
            "confidence": confidence,
        })

    return sorted(
        charges,
        key=lambda item: (item["confidence"], item["average_amount"], item["occurrences"]),
        reverse=True,
    )[:limit]


def monthly_insights(month: str | None = None) -> dict:
    """Compose a concise monthly report from deterministic finance signals."""
    insight_month = month or _latest_month()
    if insight_month is None:
        return {
            "month": None,
            "summary": monthly_summary(month=None),
            "spending_delta": None,
            "spending_delta_percent": None,
            "top_category": None,
            "top_merchant": None,
            "largest_expense": None,
            "over_budget_count": 0,
            "near_budget_count": 0,
            "recurring_count": 0,
            "anomaly_count": 0,
            "highlights": ["No imported transactions yet."],
            "risks": [],
            "next_actions": ["Upload a CSV or text-based PDF statement to generate a monthly report."],
        }

    summary = monthly_summary(month=insight_month)
    categories = category_totals(month=insight_month)
    merchants = top_merchants(month=insight_month, limit=3)
    expenses = largest_expenses(month=insight_month, limit=3)
    budgets = budget_progress(insight_month)
    recurring = recurring_charges(limit=3)
    anomalies = detect_anomalies(limit=3, month=insight_month)
    previous_summary = monthly_summary(month=_previous_month(insight_month))

    spending_delta = None
    spending_delta_percent = None
    if previous_summary["transaction_count"]:
        spending_delta = round(
            summary["total_spending"] - previous_summary["total_spending"],
            2,
        )
        if previous_summary["total_spending"]:
            spending_delta_percent = round(
                (spending_delta / previous_summary["total_spending"]) * 100,
                1,
            )

    over_budgets = [item for item in budgets if item["status"] == "over"]
    near_budgets = [item for item in budgets if item["status"] == "near"]
    top_category = categories[0] if categories else None
    top_merchant = merchants[0] if merchants else None
    largest_expense = expenses[0] if expenses else None

    highlights = _monthly_highlights(
        summary=summary,
        top_category=top_category,
        top_merchant=top_merchant,
        recurring=recurring,
        spending_delta=spending_delta,
        spending_delta_percent=spending_delta_percent,
    )
    risks = _monthly_risks(
        summary=summary,
        over_budgets=over_budgets,
        near_budgets=near_budgets,
        anomalies=anomalies,
        largest_expense=largest_expense,
    )
    next_actions = _monthly_next_actions(
        budgets=budgets,
        over_budgets=over_budgets,
        near_budgets=near_budgets,
        anomalies=anomalies,
        recurring=recurring,
    )

    return {
        "month": insight_month,
        "summary": summary,
        "spending_delta": spending_delta,
        "spending_delta_percent": spending_delta_percent,
        "top_category": top_category,
        "top_merchant": top_merchant,
        "largest_expense": largest_expense,
        "over_budget_count": len(over_budgets),
        "near_budget_count": len(near_budgets),
        "recurring_count": len(recurring),
        "anomaly_count": len(anomalies),
        "highlights": highlights,
        "risks": risks,
        "next_actions": next_actions,
    }


def monthly_forecast(month: str | None = None) -> dict:
    """Project month-end spending from imported activity and expected recurring charges."""
    forecast_month = month or _latest_month()
    if forecast_month is None:
        return {
            "month": None,
            "status": "no_data",
            "confidence": "low",
            "coverage_start_date": None,
            "coverage_end_date": None,
            "days_elapsed": 0,
            "days_in_month": 0,
            "remaining_days": 0,
            "actual_spending": 0,
            "daily_spending_average": 0,
            "run_rate_projection": 0,
            "projected_spending": 0,
            "projected_income": 0,
            "projected_net": 0,
            "budget_total": 0,
            "budget_remaining": 0,
            "budget_status": "no_budget",
            "upcoming_recurring_total": 0,
            "upcoming_recurring": [],
            "notes": ["Upload transactions to build a cash-flow forecast."],
        }

    summary = monthly_summary(month=forecast_month)
    budgets = budget_progress(forecast_month)
    start_date, end_date = _month_bounds(forecast_month)
    latest_date_text = _latest_transaction_date(forecast_month)
    coverage_date = date.fromisoformat(latest_date_text) if latest_date_text else None
    days_in_month = (end_date - start_date).days
    days_elapsed = 0
    if coverage_date:
        clamped_date = min(max(coverage_date, start_date), end_date - timedelta(days=1))
        days_elapsed = (clamped_date - start_date).days + 1

    remaining_days = max(0, days_in_month - days_elapsed)
    daily_average = round(summary["total_spending"] / days_elapsed, 2) if days_elapsed else 0
    run_rate_projection = round(daily_average * days_in_month, 2) if days_elapsed else 0
    upcoming_recurring = _upcoming_recurring_charges(forecast_month, coverage_date)
    upcoming_recurring_total = round(sum(item["average_amount"] for item in upcoming_recurring), 2)
    projected_spending = round(
        max(summary["total_spending"], run_rate_projection + upcoming_recurring_total),
        2,
    )
    projected_income = summary["total_income"]
    projected_net = round(projected_income - projected_spending, 2)

    over_budgets = [item for item in budgets if item["status"] == "over"]
    near_budgets = [item for item in budgets if item["status"] == "near"]
    budget_total = round(sum(item["amount"] for item in budgets), 2)
    budget_remaining = round(sum(item["remaining"] for item in budgets), 2)
    budget_status = _forecast_budget_status(budgets, over_budgets, near_budgets)
    status = _forecast_status(projected_net, budget_status, projected_spending)
    confidence = _forecast_confidence(days_elapsed, days_in_month, summary["transaction_count"])
    notes = _forecast_notes(
        forecast_month=forecast_month,
        coverage_date=coverage_date,
        projected_spending=projected_spending,
        projected_net=projected_net,
        upcoming_recurring_total=upcoming_recurring_total,
        budget_status=budget_status,
    )

    return {
        "month": forecast_month,
        "status": status,
        "confidence": confidence,
        "coverage_start_date": start_date.isoformat(),
        "coverage_end_date": coverage_date.isoformat() if coverage_date else None,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "remaining_days": remaining_days,
        "actual_spending": summary["total_spending"],
        "daily_spending_average": daily_average,
        "run_rate_projection": run_rate_projection,
        "projected_spending": projected_spending,
        "projected_income": projected_income,
        "projected_net": projected_net,
        "budget_total": budget_total,
        "budget_remaining": budget_remaining,
        "budget_status": budget_status,
        "upcoming_recurring_total": upcoming_recurring_total,
        "upcoming_recurring": upcoming_recurring,
        "notes": notes,
    }


def spending_for_categories(categories: list[str], month: str | None = None) -> int:
    """Return spending cents for the requested categories."""
    if not categories:
        return 0

    month_sql, month_params = _month_filter(month)
    placeholders = ", ".join("?" for _ in categories)
    category_clause = f"category IN ({placeholders})"
    sql = f"""
        SELECT COALESCE(SUM(ABS(amount_cents)), 0) AS spending_cents
        FROM transactions
        {month_sql}
          {"AND" if month_sql else "WHERE"} amount_cents < 0
          AND {category_clause}
    """
    with connect() as conn:
        row = conn.execute(sql, [*month_params, *categories]).fetchone()
    return int(row["spending_cents"])


def detect_anomalies(limit: int = 10, month: str | None = None) -> list[dict]:
    """Return unusually large expenses compared with each category average."""
    where_sql, params = _month_filter(month)
    anomaly_filter = f"{where_sql} AND" if where_sql else "WHERE"
    with connect() as conn:
        rows = conn.execute(
            f"""
            WITH category_stats AS (
                SELECT
                    category,
                    AVG(ABS(amount_cents)) AS avg_cents,
                    COUNT(*) AS transaction_count
                FROM transactions
                WHERE amount_cents < 0
                GROUP BY category
            )
            SELECT
                t.id,
                t.transaction_date,
                t.description,
                t.amount_cents,
                t.category,
                t.source_file,
                s.avg_cents,
                s.transaction_count
            FROM transactions t
            JOIN category_stats s ON t.category = s.category
            {anomaly_filter} t.amount_cents < 0
              AND s.transaction_count >= 2
              AND ABS(t.amount_cents) >= s.avg_cents * 1.8
            ORDER BY ABS(t.amount_cents) DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()

    anomalies = []
    for row in rows:
        transaction = _transaction_row_to_dict(row)
        transaction["average_category_spend"] = cents_to_dollars(row["avg_cents"])
        transaction["reason"] = "Expense is at least 80% higher than this category average."
        anomalies.append(transaction)
    return anomalies


def cents_to_dollars(cents: int | float) -> float:
    """Convert cents to a rounded dollar amount."""
    return round(float(cents) / 100, 2)


def _transaction_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "date": row["transaction_date"],
        "description": row["description"],
        "amount": cents_to_dollars(row["amount_cents"]),
        "category": row["category"],
        "source_file": row["source_file"],
    }


def _merchant_rule_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "merchant": row["merchant_name"],
        "merchant_key": row["merchant_key"],
        "category": row["category"],
        "updated_at": row["updated_at"],
    }


def _budget_row_to_dict(row: sqlite3.Row, spent_cents: int) -> dict:
    amount_cents = int(row["amount_cents"])
    remaining_cents = amount_cents - spent_cents
    percent_used = round((spent_cents / amount_cents) * 100, 1)
    if spent_cents > amount_cents:
        status = "over"
    elif spent_cents >= amount_cents * 0.85:
        status = "near"
    else:
        status = "on_track"

    return {
        "id": row["id"],
        "month": row["month"],
        "category": row["category"],
        "amount": cents_to_dollars(amount_cents),
        "spent": cents_to_dollars(spent_cents),
        "remaining": cents_to_dollars(remaining_cents),
        "percent_used": percent_used,
        "status": status,
        "updated_at": row["updated_at"],
    }


def _upload_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "file_type": row["file_type"],
        "parsed_count": row["parsed_count"],
        "imported_count": row["imported_count"],
        "duplicates_skipped": row["duplicates_skipped"],
        "first_transaction_date": row["first_transaction_date"],
        "last_transaction_date": row["last_transaction_date"],
        "created_at": row["created_at"],
    }


def _recurring_cadence(gaps: list[int]) -> str | None:
    if not gaps:
        return None

    average_gap = sum(gaps) / len(gaps)
    max_gap_variance = max(abs(gap - average_gap) for gap in gaps)
    if max_gap_variance > 8:
        return None
    if 6 <= average_gap <= 8:
        return "weekly"
    if 12 <= average_gap <= 16:
        return "biweekly"
    if 25 <= average_gap <= 35:
        return "monthly"
    if 80 <= average_gap <= 100:
        return "quarterly"
    return None


def _latest_month() -> str | None:
    months = available_months()
    return months[0]["month"] if months else None


def _previous_month(month: str) -> str:
    current = date.fromisoformat(f"{month}-01")
    if current.month == 1:
        return f"{current.year - 1}-12"
    return f"{current.year}-{current.month - 1:02d}"


def _month_bounds(month: str) -> tuple[date, date]:
    start = date.fromisoformat(f"{month}-01")
    if start.month == 12:
        return start, date(start.year + 1, 1, 1)
    return start, date(start.year, start.month + 1, 1)


def _latest_transaction_date(month: str) -> str | None:
    where_sql, params = _month_filter(month)
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT MAX(transaction_date) AS latest_date
            FROM transactions
            {where_sql}
            """,
            params,
        ).fetchone()
    return row["latest_date"] if row else None


def _upcoming_recurring_charges(month: str, coverage_date: date | None) -> list[dict]:
    start_date, end_date = _month_bounds(month)
    cutoff = coverage_date or (start_date - timedelta(days=1))
    charges = []
    for charge in recurring_charges(limit=100):
        expected_date = date.fromisoformat(charge["next_expected_date"])
        if start_date <= expected_date < end_date and expected_date > cutoff:
            charges.append(charge)
    return sorted(charges, key=lambda item: item["next_expected_date"])


def _forecast_budget_status(
    budgets: list[dict],
    over_budgets: list[dict],
    near_budgets: list[dict],
) -> str:
    if not budgets:
        return "no_budget"
    if over_budgets:
        return "over_budget"
    if near_budgets:
        return "near_budget"
    return "on_track"


def _forecast_status(projected_net: float, budget_status: str, projected_spending: float) -> str:
    if projected_spending <= 0:
        return "no_data"
    if projected_net < 0:
        return "negative_cash_flow"
    if budget_status == "over_budget":
        return "over_budget"
    if budget_status == "near_budget":
        return "near_budget"
    return "on_track"


def _forecast_confidence(days_elapsed: int, days_in_month: int, transaction_count: int) -> str:
    if not days_elapsed or not transaction_count:
        return "low"

    coverage_ratio = days_elapsed / max(days_in_month, 1)
    if coverage_ratio >= 0.75 and transaction_count >= 8:
        return "high"
    if coverage_ratio >= 0.35 and transaction_count >= 3:
        return "medium"
    return "low"


def _forecast_notes(
    forecast_month: str,
    coverage_date: date | None,
    projected_spending: float,
    projected_net: float,
    upcoming_recurring_total: float,
    budget_status: str,
) -> list[str]:
    notes = []
    if coverage_date:
        notes.append(f"Projection uses imported activity through {coverage_date.isoformat()}.")
    else:
        notes.append(f"No imported transactions for {forecast_month}; projection uses known recurring charges.")
    notes.append(f"Projected spending is {_format_money(projected_spending)}.")
    notes.append(f"Projected net cash flow is {_format_money(projected_net)}.")
    if upcoming_recurring_total:
        notes.append(f"Upcoming recurring charges add {_format_money(upcoming_recurring_total)}.")
    if budget_status == "no_budget":
        notes.append("No budgets are set for this month.")
    elif budget_status == "over_budget":
        notes.append("At least one budget category is already over its target.")
    elif budget_status == "near_budget":
        notes.append("At least one budget category is near its target.")
    else:
        notes.append("Budget categories are currently on track.")
    return notes[:5]


def _monthly_highlights(
    summary: dict,
    top_category: dict | None,
    top_merchant: dict | None,
    recurring: list[dict],
    spending_delta: float | None,
    spending_delta_percent: float | None,
) -> list[str]:
    if not summary["transaction_count"]:
        return ["No transactions were imported for this month yet."]

    highlights = [
        (
            f"Spending was {_format_money(summary['total_spending'])} across "
            f"{summary['transaction_count']} transactions."
        )
    ]
    if top_category:
        highlights.append(
            f"Top category was {top_category['category']} at {_format_money(top_category['total'])}."
        )
    if top_merchant:
        highlights.append(
            f"Top merchant was {top_merchant['merchant']} at {_format_money(top_merchant['total'])}."
        )
    if spending_delta is not None:
        direction = "up" if spending_delta > 0 else "down"
        if spending_delta == 0:
            highlights.append("Spending matched the previous month.")
        elif spending_delta_percent is None:
            highlights.append(f"Spending was {direction} {_format_money(abs(spending_delta))} from the previous month.")
        else:
            highlights.append(
                f"Spending was {direction} {_format_money(abs(spending_delta))} "
                f"({abs(spending_delta_percent)}%) from the previous month."
            )
    if recurring:
        highlights.append(f"{len(recurring)} recurring charge{'s' if len(recurring) != 1 else ''} detected.")
    return highlights[:5]


def _monthly_risks(
    summary: dict,
    over_budgets: list[dict],
    near_budgets: list[dict],
    anomalies: list[dict],
    largest_expense: dict | None,
) -> list[str]:
    risks = []
    if summary["net"] < 0:
        risks.append(f"Net cash flow was negative at {_format_money(summary['net'])}.")
    if over_budgets:
        largest_gap = max(over_budgets, key=lambda item: abs(item["remaining"]))
        risks.append(
            f"{largest_gap['category']} is {_format_money(abs(largest_gap['remaining']))} over budget."
        )
    if near_budgets:
        risks.append(f"{len(near_budgets)} budget category{' is' if len(near_budgets) == 1 else 'ies are'} near the limit.")
    if anomalies:
        risks.append(f"{len(anomalies)} unusual charge{' was' if len(anomalies) == 1 else 's were'} flagged.")
    if largest_expense and not risks:
        risks.append(
            f"Largest expense was {largest_expense['description']} at {_format_money(abs(largest_expense['amount']))}."
        )
    return risks[:4]


def _monthly_next_actions(
    budgets: list[dict],
    over_budgets: list[dict],
    near_budgets: list[dict],
    anomalies: list[dict],
    recurring: list[dict],
) -> list[str]:
    actions = []
    if not budgets:
        actions.append("Set category budgets for this month.")
    if over_budgets:
        categories = ", ".join(item["category"] for item in over_budgets[:2])
        actions.append(f"Review over-budget categories: {categories}.")
    elif near_budgets:
        categories = ", ".join(item["category"] for item in near_budgets[:2])
        actions.append(f"Watch near-limit budgets: {categories}.")
    if anomalies:
        actions.append("Review unusual charges before the next import.")
    if recurring:
        actions.append("Check upcoming recurring charges against next month's budget.")
    if not actions:
        actions.append("Keep importing statements monthly to maintain the trend line.")
    return actions[:4]


def _format_money(value: float) -> str:
    return f"${value:,.2f}"


def _month_filter(month: str | None) -> tuple[str, list[str]]:
    if not month:
        return "", []

    start, end = _month_bounds(month)

    return (
        "WHERE transaction_date >= ? AND transaction_date < ?",
        [start.isoformat(), end.isoformat()],
    )


def _transaction_filter(
    month: str | None = None,
    category: str | None = None,
    search: str | None = None,
) -> tuple[str, list[str]]:
    clauses = []
    params = []

    if month:
        _where_sql, month_params = _month_filter(month)
        clauses.append("transaction_date >= ? AND transaction_date < ?")
        params.extend(month_params)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if search:
        clauses.append("lower(description) LIKE ?")
        params.append(f"%{search.strip().lower()}%")

    if not clauses:
        return "", []
    return f"WHERE {' AND '.join(clauses)}", params
