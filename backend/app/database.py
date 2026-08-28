"""SQLite storage and analytics helpers."""
from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

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
            values = (
                row["date"],
                row["description"],
                row["amount_cents"],
                row["category"],
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
