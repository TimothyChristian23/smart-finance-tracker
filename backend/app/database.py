"""SQLite storage and analytics helpers."""
from __future__ import annotations

import os
import sqlite3
from datetime import date
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
    conn.commit()


def reset_db() -> None:
    """Delete all stored transactions."""
    with connect() as conn:
        conn.execute("DELETE FROM transactions")
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


def list_transactions(limit: int = 200) -> list[dict]:
    """Return recent transactions."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, transaction_date, description, amount_cents, category, source_file
            FROM transactions
            ORDER BY transaction_date DESC, id DESC
            LIMIT ?
            """,
            (limit,),
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


def _month_filter(month: str | None) -> tuple[str, list[str]]:
    if not month:
        return "", []

    start = date.fromisoformat(f"{month}-01")
    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)

    return (
        "WHERE transaction_date >= ? AND transaction_date < ?",
        [start.isoformat(), end.isoformat()],
    )
