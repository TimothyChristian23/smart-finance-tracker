"""SQLite storage and analytics helpers."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

from app.categorization import (
    clean_merchant_description,
    explain_category_source,
    merchant_key,
    normalize_text,
    suggest_category,
)

load_dotenv()

QUESTION_STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "also",
    "any",
    "are",
    "because",
    "before",
    "between",
    "can",
    "could",
    "did",
    "does",
    "for",
    "from",
    "give",
    "had",
    "have",
    "high",
    "how",
    "into",
    "last",
    "look",
    "month",
    "monthly",
    "more",
    "much",
    "over",
    "show",
    "spend",
    "spending",
    "spent",
    "tell",
    "than",
    "that",
    "the",
    "this",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "with",
    "why",
    "you",
}


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
            account_name TEXT,
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
        CREATE TABLE IF NOT EXISTS recurring_ignores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_key TEXT NOT NULL UNIQUE,
            merchant_name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS anomaly_ignores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_key TEXT NOT NULL UNIQUE,
            transaction_date TEXT NOT NULL,
            description TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            category TEXT NOT NULL,
            source_file TEXT,
            account_name TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS csv_import_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            date_column TEXT NOT NULL,
            description_column TEXT NOT NULL,
            amount_column TEXT,
            debit_column TEXT,
            credit_column TEXT,
            type_column TEXT,
            category_column TEXT,
            account_column TEXT,
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
            account_name TEXT,
            parsed_count INTEGER NOT NULL,
            imported_count INTEGER NOT NULL,
            duplicates_skipped INTEGER NOT NULL,
            first_transaction_date TEXT,
            last_transaction_date TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ask_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            amount REAL,
            categories_json TEXT NOT NULL DEFAULT '[]',
            month TEXT,
            intent TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ask_history_id
        ON ask_history (id)
        """
    )
    _ensure_column(conn, "transactions", "account_name", "TEXT")
    _ensure_column(conn, "upload_history", "account_name", "TEXT")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_account
        ON transactions (account_name)
        """
    )
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def reset_db() -> None:
    """Delete imported transaction data."""
    with connect() as conn:
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM upload_history")
        conn.commit()


def reset_all_data() -> None:
    """Delete all locally stored finance records."""
    with connect() as conn:
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM upload_history")
        conn.execute("DELETE FROM merchant_rules")
        conn.execute("DELETE FROM recurring_ignores")
        conn.execute("DELETE FROM anomaly_ignores")
        conn.execute("DELETE FROM csv_import_presets")
        conn.execute("DELETE FROM budgets")
        conn.execute("DELETE FROM ask_history")
        conn.commit()


def insert_transactions(rows: list[dict], apply_merchant_rules: bool = True) -> dict:
    """Insert parsed transactions and return inserted/skipped counts."""
    result = {"inserted": 0, "skipped": 0}
    if not rows:
        return result

    with connect() as conn:
        for row in rows:
            category = row["category"]
            if apply_merchant_rules:
                category = merchant_rule_for_description(conn, row["description"]) or category
            values = (
                row["date"],
                row["description"],
                row["amount_cents"],
                category,
                row.get("source_file"),
                row.get("account_name"),
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
                    source_file,
                    account_name
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            result["inserted"] += 1
        conn.commit()
    return result


def create_transaction(
    *,
    transaction_date: str,
    description: str,
    amount_cents: int,
    category: str,
    account_name: str | None = None,
    source_file: str | None = "manual",
) -> dict:
    """Create one manually-entered transaction."""
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO transactions (
                transaction_date,
                description,
                amount_cents,
                category,
                source_file,
                account_name
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                transaction_date,
                description,
                amount_cents,
                category,
                source_file,
                account_name,
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, transaction_date, description, amount_cents, category, source_file, account_name
            FROM transactions
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return _transaction_row_to_dict(row)


def preview_import(rows: list[dict], sample_limit: int = 25) -> dict:
    """Return normalized import rows, totals, and duplicate estimates without saving."""
    if not rows:
        return {
            "row_count": 0,
            "importable_count": 0,
            "duplicate_count": 0,
            "first_transaction_date": None,
            "last_transaction_date": None,
            "total_spending": 0,
            "total_income": 0,
            "net": 0,
            "categories": [],
            "rows": [],
            "errors": [],
        }

    preview_rows = []
    category_totals_cents: dict[str, dict] = {}
    spending_cents = 0
    income_cents = 0
    net_cents = 0
    duplicate_count = 0

    with connect() as conn:
        for row in rows:
            category = merchant_rule_for_description(conn, row["description"]) or row["category"]
            amount_cents = int(row["amount_cents"])
            suggestion = category_suggestion_for_description(
                conn,
                row["description"],
                amount_cents,
                category,
            )
            source_file = row.get("source_file")
            account_name = row.get("account_name")
            duplicate = transaction_exists(
                conn,
                (
                    row["date"],
                    row["description"],
                    amount_cents,
                    category,
                    source_file,
                    account_name,
                ),
            )
            if duplicate:
                duplicate_count += 1

            if amount_cents < 0:
                spending_cents += abs(amount_cents)
                category_bucket = category_totals_cents.setdefault(
                    category,
                    {"category": category, "total_cents": 0, "transaction_count": 0},
                )
                category_bucket["total_cents"] += abs(amount_cents)
                category_bucket["transaction_count"] += 1
            elif amount_cents > 0:
                income_cents += amount_cents
            net_cents += amount_cents

            preview_rows.append({
                "date": row["date"],
                "description": row["description"],
                "amount": cents_to_dollars(amount_cents),
                "category": category,
                "suggested_category": suggestion["category"],
                "category_confidence": suggestion["confidence"],
                "category_confidence_label": suggestion["confidence_label"],
                "category_source": suggestion["source"],
                "category_source_label": explain_category_source(suggestion["source"]),
                "category_reason": suggestion["reason"],
                "matched_terms": suggestion["matched_terms"],
                "source_file": source_file,
                "account_name": account_name,
                "duplicate": duplicate,
            })

    dates = sorted(row["date"] for row in rows if row.get("date"))
    categories = sorted(
        [
            {
                "category": item["category"],
                "total": cents_to_dollars(item["total_cents"]),
                "transaction_count": item["transaction_count"],
            }
            for item in category_totals_cents.values()
        ],
        key=lambda item: item["total"],
        reverse=True,
    )

    return {
        "row_count": len(rows),
        "importable_count": len(rows) - duplicate_count,
        "duplicate_count": duplicate_count,
        "first_transaction_date": dates[0] if dates else None,
        "last_transaction_date": dates[-1] if dates else None,
        "total_spending": cents_to_dollars(spending_cents),
        "total_income": cents_to_dollars(income_cents),
        "net": cents_to_dollars(net_cents),
        "categories": categories,
        "rows": preview_rows[:sample_limit],
        "errors": [],
    }


def record_upload(filename: str, file_type: str, rows: list[dict], result: dict, account_name: str | None = None) -> dict:
    """Record a successful statement upload."""
    dates = sorted(row["date"] for row in rows if row.get("date"))
    upload_account_name = account_name or account_name_from_rows(rows)
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO upload_history (
                filename,
                file_type,
                account_name,
                parsed_count,
                imported_count,
                duplicates_skipped,
                first_transaction_date,
                last_transaction_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                file_type,
                upload_account_name,
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
            SELECT id, filename, file_type, account_name, parsed_count, imported_count,
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
    transaction_date, description, amount_cents, _category, source_file, account_name = values
    if source_file is None:
        source_sql = "source_file IS NULL"
        params = [transaction_date, description, amount_cents]
    else:
        source_sql = "source_file = ?"
        params = [transaction_date, description, amount_cents, source_file]
    if account_name is None:
        account_sql = "account_name IS NULL"
    else:
        account_sql = "account_name = ?"
        params.append(account_name)

    row = conn.execute(
        f"""
        SELECT 1
        FROM transactions
        WHERE transaction_date = ?
          AND description = ?
          AND amount_cents = ?
          AND {source_sql}
          AND {account_sql}
        LIMIT 1
        """,
        params,
    ).fetchone()
    return row is not None


def account_name_from_rows(rows: list[dict]) -> str | None:
    account_names = {
        row.get("account_name")
        for row in rows
        if row.get("account_name")
    }
    if len(account_names) == 1:
        return account_names.pop()
    return None


def merchant_rule_for_description(conn: sqlite3.Connection, description: str) -> str | None:
    """Return the learned category for an exact merchant description, if any."""
    row = conn.execute(
        """
        SELECT category
        FROM merchant_rules
        WHERE merchant_key = ?
        """,
        (merchant_key(description),),
    ).fetchone()
    return row["category"] if row else None


def category_suggestion_for_description(
    conn: sqlite3.Connection,
    description: str,
    amount_cents: int,
    current_category: str,
) -> dict:
    """Return an explainable category suggestion, including saved-rule evidence."""
    rule_category = merchant_rule_for_description(conn, description)
    merchant_name = clean_merchant_description(description)
    if rule_category:
        return {
            "category": rule_category,
            "confidence": 0.99,
            "confidence_label": "high",
            "source": "saved_rule",
            "matched_terms": [merchant_name],
            "reason": f"Saved merchant rule maps {merchant_name} to {rule_category}.",
        }
    return suggest_category(description, amount_cents, current_category)


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


def save_merchant_rule(merchant_name: str, category: str, apply_existing: bool = False) -> dict:
    """Create or update a normalized merchant rule, optionally updating matching transactions."""
    cleaned_name = clean_merchant_description(merchant_name)
    target_key = merchant_key(cleaned_name)
    with connect() as conn:
        upsert_merchant_rule(conn, cleaned_name, category)
        updated_count = 0
        if apply_existing:
            updated_count = apply_merchant_rule_to_transactions(conn, cleaned_name, category)
        conn.commit()
        row = conn.execute(
            """
            SELECT id, merchant_name, merchant_key, category, updated_at
            FROM merchant_rules
            WHERE merchant_key = ?
            """,
            (target_key,),
        ).fetchone()

    return {
        "rule": _merchant_rule_row_to_dict(row),
        "updated_transactions": updated_count,
    }


def update_transaction_category(transaction_id: int, category: str, remember: bool = False) -> dict | None:
    """Update one transaction category and optionally save a merchant rule."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, transaction_date, description, amount_cents, category, source_file, account_name
            FROM transactions
            WHERE id = ?
            """,
            (transaction_id,),
        ).fetchone()
        if row is None:
            return None

        if remember:
            upsert_merchant_rule(conn, row["description"], category)
            apply_merchant_rule_to_transactions(conn, row["description"], category)
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
            SELECT id, transaction_date, description, amount_cents, category, source_file, account_name
            FROM transactions
            WHERE id = ?
            """,
            (transaction_id,),
        ).fetchone()
    return _transaction_row_to_dict(updated)


def upsert_merchant_rule(conn: sqlite3.Connection, merchant_name: str, category: str) -> str:
    """Insert or update a normalized merchant category rule and return its key."""
    cleaned_name = clean_merchant_description(merchant_name)
    target_key = merchant_key(cleaned_name)
    conn.execute(
        """
        INSERT INTO merchant_rules (merchant_key, merchant_name, category)
        VALUES (?, ?, ?)
        ON CONFLICT(merchant_key) DO UPDATE SET
            merchant_name = excluded.merchant_name,
            category = excluded.category,
            updated_at = CURRENT_TIMESTAMP
        """,
        (target_key, cleaned_name, category),
    )
    return target_key


def apply_merchant_rule_to_transactions(conn: sqlite3.Connection, merchant_name: str, category: str) -> int:
    """Apply a merchant rule category to existing matching transactions."""
    target_key = merchant_key(merchant_name)
    matching_ids = [
        item["id"]
        for item in conn.execute("SELECT id, description FROM transactions").fetchall()
        if merchant_key(item["description"]) == target_key
    ]
    if not matching_ids:
        return 0

    placeholders = ", ".join("?" for _ in matching_ids)
    cursor = conn.execute(
        f"""
        UPDATE transactions
        SET category = ?
        WHERE id IN ({placeholders})
        """,
        [category, *matching_ids],
    )
    return cursor.rowcount


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


def list_recurring_ignores() -> list[dict]:
    """Return recurring merchants that the user has hidden."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, merchant_name, merchant_key, created_at
            FROM recurring_ignores
            ORDER BY merchant_name
            """
        ).fetchall()
    return [_recurring_ignore_row_to_dict(row) for row in rows]


def ignore_recurring_merchant(merchant_name: str) -> dict:
    """Hide a normalized merchant from recurring charge detection."""
    cleaned_name = clean_merchant_description(merchant_name)
    target_key = merchant_key(cleaned_name)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO recurring_ignores (merchant_key, merchant_name)
            VALUES (?, ?)
            ON CONFLICT(merchant_key) DO UPDATE SET
                merchant_name = excluded.merchant_name
            """,
            (target_key, cleaned_name),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, merchant_name, merchant_key, created_at
            FROM recurring_ignores
            WHERE merchant_key = ?
            """,
            (target_key,),
        ).fetchone()
    return _recurring_ignore_row_to_dict(row)


def delete_recurring_ignore(ignore_id: int) -> bool:
    """Restore a hidden recurring merchant by ignore id."""
    with connect() as conn:
        cursor = conn.execute(
            """
            DELETE FROM recurring_ignores
            WHERE id = ?
            """,
            (ignore_id,),
        )
        conn.commit()
    return cursor.rowcount > 0


def list_anomaly_ignores() -> list[dict]:
    """Return anomaly transactions the user has dismissed."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                transaction_key,
                transaction_date,
                description,
                amount_cents,
                category,
                source_file,
                account_name,
                created_at
            FROM anomaly_ignores
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
    return [_anomaly_ignore_row_to_dict(row) for row in rows]


def ignore_anomaly_transaction(transaction_id: int) -> dict | None:
    """Dismiss one transaction from anomaly detection."""
    with connect() as conn:
        transaction = conn.execute(
            """
            SELECT id, transaction_date, description, amount_cents, category, source_file, account_name
            FROM transactions
            WHERE id = ?
            """,
            (transaction_id,),
        ).fetchone()
        if transaction is None:
            return None

        transaction_key = anomaly_transaction_key_from_row(transaction)
        conn.execute(
            """
            INSERT INTO anomaly_ignores (
                transaction_key,
                transaction_date,
                description,
                amount_cents,
                category,
                source_file,
                account_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(transaction_key) DO UPDATE SET
                transaction_date = excluded.transaction_date,
                description = excluded.description,
                amount_cents = excluded.amount_cents,
                category = excluded.category,
                source_file = excluded.source_file,
                account_name = excluded.account_name
            """,
            (
                transaction_key,
                transaction["transaction_date"],
                transaction["description"],
                int(transaction["amount_cents"]),
                transaction["category"],
                transaction["source_file"],
                transaction["account_name"],
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT
                id,
                transaction_key,
                transaction_date,
                description,
                amount_cents,
                category,
                source_file,
                account_name,
                created_at
            FROM anomaly_ignores
            WHERE transaction_key = ?
            """,
            (transaction_key,),
        ).fetchone()
    return _anomaly_ignore_row_to_dict(row)


def delete_anomaly_ignore(ignore_id: int) -> bool:
    """Restore a dismissed anomaly by ignore id."""
    with connect() as conn:
        cursor = conn.execute(
            """
            DELETE FROM anomaly_ignores
            WHERE id = ?
            """,
            (ignore_id,),
        )
        conn.commit()
    return cursor.rowcount > 0


def list_csv_import_presets() -> list[dict]:
    """Return saved CSV column mapping presets."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                name,
                date_column,
                description_column,
                amount_column,
                debit_column,
                credit_column,
                type_column,
                category_column,
                account_column,
                updated_at
            FROM csv_import_presets
            ORDER BY name
            """
        ).fetchall()
    return [_csv_import_preset_row_to_dict(row) for row in rows]


def get_csv_import_preset(preset_id: int) -> dict | None:
    """Return one saved CSV mapping preset."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                name,
                date_column,
                description_column,
                amount_column,
                debit_column,
                credit_column,
                type_column,
                category_column,
                account_column,
                updated_at
            FROM csv_import_presets
            WHERE id = ?
            """,
            (preset_id,),
        ).fetchone()
    return _csv_import_preset_row_to_dict(row) if row else None


def save_csv_import_preset(
    name: str,
    date_column: str,
    description_column: str,
    amount_column: str | None = None,
    debit_column: str | None = None,
    credit_column: str | None = None,
    type_column: str | None = None,
    category_column: str | None = None,
    account_column: str | None = None,
) -> dict:
    """Create or update a CSV column mapping preset by name."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO csv_import_presets (
                name,
                date_column,
                description_column,
                amount_column,
                debit_column,
                credit_column,
                type_column,
                category_column,
                account_column
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                date_column = excluded.date_column,
                description_column = excluded.description_column,
                amount_column = excluded.amount_column,
                debit_column = excluded.debit_column,
                credit_column = excluded.credit_column,
                type_column = excluded.type_column,
                category_column = excluded.category_column,
                account_column = excluded.account_column,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                name,
                date_column,
                description_column,
                amount_column,
                debit_column,
                credit_column,
                type_column,
                category_column,
                account_column,
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT
                id,
                name,
                date_column,
                description_column,
                amount_column,
                debit_column,
                credit_column,
                type_column,
                category_column,
                account_column,
                updated_at
            FROM csv_import_presets
            WHERE name = ?
            """,
            (name,),
        ).fetchone()
    return _csv_import_preset_row_to_dict(row)


def delete_csv_import_preset(preset_id: int) -> bool:
    """Delete a saved CSV column mapping preset."""
    with connect() as conn:
        cursor = conn.execute(
            """
            DELETE FROM csv_import_presets
            WHERE id = ?
            """,
            (preset_id,),
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
            SELECT id, filename, file_type, account_name, parsed_count, imported_count,
                   duplicates_skipped, first_transaction_date, last_transaction_date,
                   created_at
            FROM upload_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_upload_row_to_dict(row) for row in rows]


def list_accounts() -> list[str]:
    """Return account labels found on imported transactions."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT account_name
            FROM transactions
            WHERE account_name IS NOT NULL
              AND trim(account_name) != ''
            ORDER BY account_name
            """
        ).fetchall()
    return [row["account_name"] for row in rows]


def account_summary(month: str | None = None) -> list[dict]:
    """Return income, spending, and net totals grouped by account label."""
    where_sql, params = _month_filter(month)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                account_name,
                COALESCE(SUM(CASE WHEN amount_cents < 0 THEN ABS(amount_cents) ELSE 0 END), 0) AS spending_cents,
                COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END), 0) AS income_cents,
                COALESCE(SUM(amount_cents), 0) AS net_cents,
                COUNT(*) AS transaction_count
            FROM transactions
            {where_sql}
            GROUP BY account_name
            ORDER BY spending_cents DESC, income_cents DESC, transaction_count DESC
            """,
            params,
        ).fetchall()

    return [
        {
            "account_name": row["account_name"],
            "total_spending": cents_to_dollars(row["spending_cents"]),
            "total_income": cents_to_dollars(row["income_cents"]),
            "net": cents_to_dollars(row["net_cents"]),
            "transaction_count": row["transaction_count"],
        }
        for row in rows
    ]


def list_transactions(
    limit: int = 200,
    month: str | None = None,
    category: str | None = None,
    search: str | None = None,
    account_name: str | None = None,
) -> list[dict]:
    """Return transactions with optional month, category, and description filters."""
    where_sql, params = _transaction_filter(
        month=month,
        category=category,
        search=search,
        account_name=account_name,
    )
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, transaction_date, description, amount_cents, category, source_file, account_name
            FROM transactions
            {where_sql}
            ORDER BY transaction_date DESC, id DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [_transaction_row_to_dict(row) for row in rows]


def get_transaction(transaction_id: int) -> dict | None:
    """Return one transaction by id."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, transaction_date, description, amount_cents, category, source_file, account_name
            FROM transactions
            WHERE id = ?
            """,
            (transaction_id,),
        ).fetchone()
    return _transaction_row_to_dict(row) if row else None


def update_transaction_details(
    transaction_id: int,
    *,
    transaction_date: str,
    description: str,
    amount_cents: int,
    category: str,
    account_name: str | None,
) -> dict | None:
    """Update editable transaction details."""
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE transactions
            SET transaction_date = ?,
                description = ?,
                amount_cents = ?,
                category = ?,
                account_name = ?
            WHERE id = ?
            """,
            (transaction_date, description, amount_cents, category, account_name, transaction_id),
        )
        if cursor.rowcount == 0:
            return None

        row = conn.execute(
            """
            SELECT id, transaction_date, description, amount_cents, category, source_file, account_name
            FROM transactions
            WHERE id = ?
            """,
            (transaction_id,),
        ).fetchone()
        conn.commit()
    return _transaction_row_to_dict(row)


def delete_transaction(transaction_id: int) -> bool:
    """Delete one transaction by id."""
    with connect() as conn:
        cursor = conn.execute(
            """
            DELETE FROM transactions
            WHERE id = ?
            """,
            (transaction_id,),
        )
        conn.commit()
    return cursor.rowcount > 0


def export_backup() -> dict:
    """Return a complete JSON-serializable snapshot of local finance data."""
    transactions = list_transactions(limit=100000)
    uploads = list_uploads(limit=100000)
    merchant_rules = list_merchant_rules()
    recurring_ignores = list_recurring_ignores()
    anomaly_ignores = list_anomaly_ignores()
    csv_import_presets = list_csv_import_presets()
    budgets = list_all_budgets()
    ask_history = list_ask_history(limit=100000)
    months = available_months()
    summary = monthly_summary(month=None)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "counts": {
            "transactions": len(transactions),
            "uploads": len(uploads),
            "merchant_rules": len(merchant_rules),
            "recurring_ignores": len(recurring_ignores),
            "anomaly_ignores": len(anomaly_ignores),
            "csv_import_presets": len(csv_import_presets),
            "budgets": len(budgets),
            "ask_history": len(ask_history),
            "months": len(months),
        },
        "summary": summary,
        "months": months,
        "transactions": transactions,
        "budgets": budgets,
        "merchant_rules": merchant_rules,
        "recurring_ignores": recurring_ignores,
        "anomaly_ignores": anomaly_ignores,
        "csv_import_presets": csv_import_presets,
        "ask_history": ask_history,
        "uploads": uploads,
    }


def restore_backup(backup: dict) -> dict:
    """Replace local records with durable data from an exported backup."""
    if not isinstance(backup, dict):
        raise ValueError("Backup must be a JSON object.")
    if backup.get("schema_version") != 1:
        raise ValueError("Unsupported backup schema version.")

    transactions = _restore_transactions(backup)
    uploads = _restore_uploads(backup)
    merchant_rules = _restore_merchant_rules(backup)
    recurring_ignores = _restore_recurring_ignores(backup)
    anomaly_ignores = _restore_anomaly_ignores(backup)
    csv_import_presets = _restore_csv_import_presets(backup)
    budgets = _restore_budgets(backup)
    ask_history = _restore_ask_history(backup)
    if len({row[0] for row in merchant_rules}) != len(merchant_rules):
        raise ValueError("Backup contains duplicate merchant rules.")
    if len({row[0] for row in recurring_ignores}) != len(recurring_ignores):
        raise ValueError("Backup contains duplicate recurring ignores.")
    if len({row[0] for row in anomaly_ignores}) != len(anomaly_ignores):
        raise ValueError("Backup contains duplicate anomaly ignores.")
    if len({row[0].casefold() for row in csv_import_presets}) != len(csv_import_presets):
        raise ValueError("Backup contains duplicate CSV import presets.")
    if len({(row[0], row[1]) for row in budgets}) != len(budgets):
        raise ValueError("Backup contains duplicate budgets.")

    with connect() as conn:
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM upload_history")
        conn.execute("DELETE FROM merchant_rules")
        conn.execute("DELETE FROM recurring_ignores")
        conn.execute("DELETE FROM anomaly_ignores")
        conn.execute("DELETE FROM csv_import_presets")
        conn.execute("DELETE FROM budgets")
        conn.execute("DELETE FROM ask_history")
        conn.executemany(
            """
            INSERT INTO transactions (
                transaction_date,
                description,
                amount_cents,
                category,
                source_file,
                account_name
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            transactions,
        )
        conn.executemany(
            """
            INSERT INTO upload_history (
                filename,
                file_type,
                account_name,
                parsed_count,
                imported_count,
                duplicates_skipped,
                first_transaction_date,
                last_transaction_date,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            uploads,
        )
        conn.executemany(
            """
            INSERT INTO merchant_rules (
                merchant_key,
                merchant_name,
                category,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), COALESCE(?, CURRENT_TIMESTAMP))
            """,
            merchant_rules,
        )
        conn.executemany(
            """
            INSERT INTO recurring_ignores (
                merchant_key,
                merchant_name,
                created_at
            )
            VALUES (?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            recurring_ignores,
        )
        conn.executemany(
            """
            INSERT INTO anomaly_ignores (
                transaction_key,
                transaction_date,
                description,
                amount_cents,
                category,
                source_file,
                account_name,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            anomaly_ignores,
        )
        conn.executemany(
            """
            INSERT INTO csv_import_presets (
                name,
                date_column,
                description_column,
                amount_column,
                debit_column,
                credit_column,
                type_column,
                category_column,
                account_column,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            csv_import_presets,
        )
        conn.executemany(
            """
            INSERT INTO budgets (
                month,
                category,
                amount_cents,
                updated_at
            )
            VALUES (?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            budgets,
        )
        conn.executemany(
            """
            INSERT INTO ask_history (
                question,
                answer,
                amount,
                categories_json,
                month,
                intent,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            ask_history,
        )
        conn.commit()

    return {
        "transactions": len(transactions),
        "uploads": len(uploads),
        "merchant_rules": len(merchant_rules),
        "recurring_ignores": len(recurring_ignores),
        "anomaly_ignores": len(anomaly_ignores),
        "csv_import_presets": len(csv_import_presets),
        "budgets": len(budgets),
        "ask_history": len(ask_history),
    }


def list_all_budgets() -> list[dict]:
    """Return every stored budget with live spending progress."""
    with connect() as conn:
        months = conn.execute(
            """
            SELECT DISTINCT month
            FROM budgets
            ORDER BY month DESC
            """
        ).fetchall()

    budgets = []
    for row in months:
        budgets.extend(budget_progress(row["month"]))
    return budgets


def record_ask_history(
    *,
    question: str,
    answer: str,
    amount: float | None,
    categories: list[str],
    month: str | None,
    intent: str,
) -> None:
    """Persist a compact record of a finance Q&A exchange."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ask_history (question, answer, amount, categories_json, month, intent)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                question,
                answer,
                float(amount) if amount is not None else None,
                json.dumps(categories),
                month,
                intent,
            ),
        )
        conn.commit()


def list_ask_history(limit: int = 20) -> list[dict]:
    """Return recent finance Q&A history."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, question, answer, amount, categories_json, month, intent, created_at
            FROM ask_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_ask_history_row_to_dict(row) for row in rows]


def category_review_queue(month: str | None = None, limit: int = 20) -> list[dict]:
    """Return transactions whose category could use review."""
    where_sql, params = _month_filter(month)
    review_items = []
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, transaction_date, description, amount_cents, category, source_file, account_name
            FROM transactions
            {where_sql}
            ORDER BY transaction_date DESC, ABS(amount_cents) DESC
            LIMIT 500
            """,
            params,
        ).fetchall()

        for row in rows:
            if int(row["amount_cents"]) > 0:
                continue

            suggestion = category_suggestion_for_description(
                conn,
                row["description"],
                int(row["amount_cents"]),
                row["category"],
            )
            needs_review = (
                row["category"] == "Other"
                or suggestion["category"] != row["category"]
            )
            if not needs_review:
                continue

            transaction = _transaction_row_to_dict(row)
            review_items.append({
                "transaction": transaction,
                "current_category": row["category"],
                "suggested_category": suggestion["category"],
                "confidence": suggestion["confidence"],
                "confidence_label": suggestion["confidence_label"],
                "category_source": suggestion["source"],
                "category_source_label": explain_category_source(suggestion["source"]),
                "matched_terms": suggestion["matched_terms"],
                "reason": suggestion["reason"],
                "action": "update" if suggestion["category"] != row["category"] else "review",
            })

    action_priority = {"update": 1, "review": 0}
    return sorted(
        review_items,
        key=lambda item: (
            action_priority[item["action"]],
            item["confidence"],
            abs(item["transaction"]["amount"]),
        ),
        reverse=True,
    )[:limit]


def category_explanations_for_question(question: str, month: str | None = None, limit: int = 5) -> list[dict]:
    """Return ranked transaction category explanations for a natural-language question."""
    explanation_month = month or _latest_month()
    where_sql, params = _month_filter(explanation_month)
    tokens = _category_explanation_tokens(question)
    explanations = []

    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, transaction_date, description, amount_cents, category, source_file, account_name
            FROM transactions
            {where_sql}
            ORDER BY transaction_date DESC, ABS(amount_cents) DESC
            LIMIT 500
            """,
            params,
        ).fetchall()

        for row in rows:
            score = _transaction_question_score(row, tokens, question)
            if tokens and score <= 0:
                continue

            suggestion = category_suggestion_for_description(
                conn,
                row["description"],
                int(row["amount_cents"]),
                row["category"],
            )
            transaction = _transaction_row_to_dict(row)
            explanations.append({
                "transaction": transaction,
                "current_category": row["category"],
                "suggested_category": suggestion["category"],
                "confidence": suggestion["confidence"],
                "confidence_label": suggestion["confidence_label"],
                "category_source": suggestion["source"],
                "category_source_label": explain_category_source(suggestion["source"]),
                "matched_terms": suggestion["matched_terms"],
                "reason": suggestion["reason"],
                "score": score,
            })

    return sorted(
        explanations,
        key=lambda item: (
            item["score"],
            item["confidence"],
            abs(item["transaction"]["amount"]),
        ),
        reverse=True,
    )[:limit]


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
            SELECT description, category, amount_cents
            FROM transactions
            {where_sql}
              {"AND" if where_sql else "WHERE"} amount_cents < 0
            ORDER BY transaction_date DESC, id DESC
            """,
            params,
        ).fetchall()

    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        cleaned_name = clean_merchant_description(row["description"])
        key = (merchant_key(cleaned_name), row["category"])
        bucket = grouped.setdefault(
            key,
            {
                "merchant": cleaned_name,
                "category": row["category"],
                "total_cents": 0,
                "transaction_count": 0,
            },
        )
        bucket["total_cents"] += abs(int(row["amount_cents"]))
        bucket["transaction_count"] += 1

    ranked = sorted(
        grouped.values(),
        key=lambda item: (-item["total_cents"], -item["transaction_count"], item["merchant"]),
    )
    return [
        {
            "merchant": item["merchant"],
            "category": item["category"],
            "total": cents_to_dollars(item["total_cents"]),
            "transaction_count": item["transaction_count"],
        }
        for item in ranked[:limit]
    ]


def largest_expenses(month: str | None = None, limit: int = 10) -> list[dict]:
    """Return largest individual expenses."""
    where_sql, params = _month_filter(month)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, transaction_date, description, amount_cents, category, source_file, account_name
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
        ignored_keys = {
            row["merchant_key"]
            for row in conn.execute("SELECT merchant_key FROM recurring_ignores").fetchall()
        }

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        key = merchant_key(row["description"])
        if key in ignored_keys:
            continue
        grouped.setdefault(key, []).append(row)

    charges = []
    for recurring_key, items in grouped.items():
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
            "merchant": clean_merchant_description(items[-1]["description"]),
            "merchant_key": recurring_key,
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


def recurring_bill_calendar(month: str | None = None, limit: int = 20) -> dict:
    """Return expected recurring charges for a calendar month."""
    calendar_month = month or _next_month(_latest_month())
    if calendar_month is None:
        return {
            "month": None,
            "total_expected": 0,
            "item_count": 0,
            "items": [],
        }

    projected = _projected_recurring_charges(calendar_month, coverage_date=None)
    items = [
        {
            "date": charge["next_expected_date"],
            "merchant": charge["merchant"],
            "merchant_key": charge["merchant_key"],
            "category": charge["category"],
            "amount": charge["average_amount"],
            "cadence": charge["cadence"],
            "confidence": charge["confidence"],
        }
        for charge in projected
    ][:limit]

    return {
        "month": calendar_month,
        "total_expected": round(sum(item["amount"] for item in items), 2),
        "item_count": len(items),
        "items": items,
    }


def import_quality_report(month: str | None = None) -> dict:
    """Summarize import trust signals for a month or the full local dataset."""
    summary = monthly_summary(month=month)
    uploads = _uploads_for_report_month(list_uploads(limit=100000), month)
    review_items = category_review_queue(month=month, limit=500)
    anomalies = detect_anomalies(limit=500, month=month)
    recurring_items = recurring_charges(limit=500)
    other_total = next(
        (item["total"] for item in summary["categories"] if item["category"] == "Other"),
        0,
    )

    report = {
        "month": month,
        "status": _import_quality_status(
            transaction_count=summary["transaction_count"],
            review_count=len(review_items),
            anomaly_count=len(anomalies),
        ),
        "transaction_count": summary["transaction_count"],
        "upload_count": len(uploads),
        "duplicates_skipped": sum(item["duplicates_skipped"] for item in uploads),
        "review_count": len(review_items),
        "anomaly_count": len(anomalies),
        "recurring_count": len(recurring_items),
        "other_total": other_total,
        "latest_upload": uploads[0] if uploads else None,
        "review_items": review_items[:3],
        "anomalies": anomalies[:3],
        "recurring_charges": recurring_items[:3],
    }
    report["notes"] = _import_quality_notes(report)
    return report


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


def budget_recommendations(month: str | None = None, limit: int = 8) -> list[dict]:
    """Suggest category budgets from recent spending and recurring charges."""
    target_month = month or _latest_month()
    if target_month is None:
        return []

    history = _category_spending_history(target_month, lookback=3)
    recurring_by_category = _recurring_spending_by_category(target_month)
    existing_by_category = {
        item["category"]: item
        for item in budget_progress(target_month)
    }
    recommendation_categories = sorted(set(history) | set(recurring_by_category))

    recommendations = []
    for category in recommendation_categories:
        history_rows = history.get(category, [])
        history_cents = [row["total_cents"] for row in history_rows]
        average_cents = int((sum(history_cents) / len(history_cents)) + 0.5) if history_cents else 0
        recurring_cents = recurring_by_category.get(category, 0)
        baseline_cents = max(average_cents, recurring_cents)
        if baseline_cents <= 0:
            continue

        buffer = 1.05 if recurring_cents >= average_cents and recurring_cents else 1.1
        recommended_cents = _round_up_to_nearest_cents(int((baseline_cents * buffer) + 0.5))
        existing_budget = existing_by_category.get(category)
        existing_cents = _dollars_to_cents(existing_budget["amount"]) if existing_budget else None

        recommendations.append({
            "month": target_month,
            "category": category,
            "recommended_amount": cents_to_dollars(recommended_cents),
            "baseline_average": cents_to_dollars(average_cents),
            "recurring_amount": cents_to_dollars(recurring_cents),
            "history_months": len(history_rows),
            "existing_budget": cents_to_dollars(existing_cents) if existing_cents is not None else None,
            "difference_from_existing": (
                cents_to_dollars(recommended_cents - existing_cents)
                if existing_cents is not None
                else cents_to_dollars(recommended_cents)
            ),
            "confidence": _budget_recommendation_confidence(len(history_rows), recurring_cents),
            "action": _budget_recommendation_action(recommended_cents, existing_cents),
            "reason": _budget_recommendation_reason(
                history_months=len(history_rows),
                average_cents=average_cents,
                recurring_cents=recurring_cents,
                recommended_cents=recommended_cents,
                existing_cents=existing_cents,
            ),
        })

    action_priority = {"raise": 3, "create": 2, "review": 1, "keep": 0}
    return sorted(
        recommendations,
        key=lambda item: (action_priority[item["action"]], item["recommended_amount"]),
        reverse=True,
    )[:limit]


def question_evidence(question: str, month: str | None = None, limit: int = 6) -> dict:
    """Retrieve cited transaction and aggregate context for broad Q&A."""
    evidence_month = month or _latest_month()
    if evidence_month is None:
        return {
            "month": None,
            "summary": monthly_summary(month=None),
            "matches": [],
            "citations": [],
            "top_categories": [],
            "top_merchants": [],
            "anomalies": [],
        }

    summary = monthly_summary(month=evidence_month)
    top_categories = category_totals(month=evidence_month)[:5]
    top_merchants_list = top_merchants(month=evidence_month, limit=5)
    anomalies = detect_anomalies(limit=3, month=evidence_month)
    matches = _rank_question_transactions(question, evidence_month, limit)
    citations = _question_citations(summary, top_categories, top_merchants_list, anomalies, matches)

    return {
        "month": evidence_month,
        "summary": summary,
        "matches": matches,
        "citations": citations,
        "top_categories": top_categories,
        "top_merchants": top_merchants_list,
        "anomalies": anomalies,
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
                t.account_name,
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
            [*params, limit * 5],
        ).fetchall()
        ignored_keys = _ignored_anomaly_keys(conn)

    anomalies = []
    for row in rows:
        if anomaly_transaction_key_from_row(row) in ignored_keys:
            continue
        transaction = _transaction_row_to_dict(row)
        transaction["average_category_spend"] = cents_to_dollars(row["avg_cents"])
        transaction["reason"] = "Expense is at least 80% higher than this category average."
        anomalies.append(transaction)
    return anomalies[:limit]


def cents_to_dollars(cents: int | float) -> float:
    """Convert cents to a rounded dollar amount."""
    return round(float(cents) / 100, 2)


def anomaly_transaction_key(
    transaction_date: str,
    description: str,
    amount_cents: int,
    category: str,
    source_file: str | None = None,
    account_name: str | None = None,
) -> str:
    """Build a stable key for a transaction-level anomaly dismissal."""
    return "|".join([
        transaction_date,
        merchant_key(description),
        str(int(amount_cents)),
        normalize_text(category),
        normalize_text(source_file or ""),
        normalize_text(account_name or ""),
    ])


def anomaly_transaction_key_from_row(row: sqlite3.Row) -> str:
    return anomaly_transaction_key(
        row["transaction_date"],
        row["description"],
        int(row["amount_cents"]),
        row["category"],
        row["source_file"],
        row["account_name"],
    )


def _transaction_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "date": row["transaction_date"],
        "description": row["description"],
        "amount": cents_to_dollars(row["amount_cents"]),
        "category": row["category"],
        "source_file": row["source_file"],
        "account_name": row["account_name"],
    }


def _merchant_rule_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "merchant": row["merchant_name"],
        "merchant_key": row["merchant_key"],
        "category": row["category"],
        "updated_at": row["updated_at"],
    }


def _recurring_ignore_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "merchant": row["merchant_name"],
        "merchant_key": row["merchant_key"],
        "created_at": row["created_at"],
    }


def _anomaly_ignore_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "transaction_key": row["transaction_key"],
        "transaction": {
            "date": row["transaction_date"],
            "description": row["description"],
            "amount": cents_to_dollars(row["amount_cents"]),
            "category": row["category"],
            "source_file": row["source_file"],
            "account_name": row["account_name"],
        },
        "created_at": row["created_at"],
    }


def _csv_import_preset_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "date_column": row["date_column"],
        "description_column": row["description_column"],
        "amount_column": row["amount_column"],
        "debit_column": row["debit_column"],
        "credit_column": row["credit_column"],
        "type_column": row["type_column"],
        "category_column": row["category_column"],
        "account_column": row["account_column"],
        "updated_at": row["updated_at"],
    }


def _ignored_anomaly_keys(conn: sqlite3.Connection) -> set[str]:
    return {
        row["transaction_key"]
        for row in conn.execute("SELECT transaction_key FROM anomaly_ignores").fetchall()
    }


def _ask_history_row_to_dict(row: sqlite3.Row) -> dict:
    try:
        categories = json.loads(row["categories_json"] or "[]")
    except json.JSONDecodeError:
        categories = []

    return {
        "id": row["id"],
        "question": row["question"],
        "answer": row["answer"],
        "amount": row["amount"],
        "categories": categories,
        "month": row["month"],
        "intent": row["intent"],
        "created_at": row["created_at"],
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
        "account_name": row["account_name"],
        "parsed_count": row["parsed_count"],
        "imported_count": row["imported_count"],
        "duplicates_skipped": row["duplicates_skipped"],
        "first_transaction_date": row["first_transaction_date"],
        "last_transaction_date": row["last_transaction_date"],
        "created_at": row["created_at"],
    }


def _restore_transactions(backup: dict) -> list[tuple]:
    rows = []
    for index, item in enumerate(_backup_list(backup, "transactions"), start=1):
        record = _backup_object(item, f"transactions[{index}]")
        rows.append((
            _backup_date(record, "date", f"transactions[{index}]"),
            _backup_required_text(record, "description", f"transactions[{index}]", max_length=200),
            _backup_amount_cents(record, "amount", f"transactions[{index}]"),
            _backup_required_text(record, "category", f"transactions[{index}]", max_length=80),
            _backup_optional_text(record, "source_file", max_length=200),
            _backup_optional_text(record, "account_name", max_length=80),
        ))
    return rows


def _restore_uploads(backup: dict) -> list[tuple]:
    rows = []
    for index, item in enumerate(_backup_list(backup, "uploads"), start=1):
        record = _backup_object(item, f"uploads[{index}]")
        rows.append((
            _backup_required_text(record, "filename", f"uploads[{index}]", max_length=260),
            _backup_required_text(record, "file_type", f"uploads[{index}]", max_length=20),
            _backup_optional_text(record, "account_name", max_length=80),
            _backup_non_negative_int(record, "parsed_count", f"uploads[{index}]"),
            _backup_non_negative_int(record, "imported_count", f"uploads[{index}]"),
            _backup_non_negative_int(record, "duplicates_skipped", f"uploads[{index}]"),
            _backup_optional_date(record, "first_transaction_date", f"uploads[{index}]"),
            _backup_optional_date(record, "last_transaction_date", f"uploads[{index}]"),
            _backup_optional_text(record, "created_at", max_length=80),
        ))
    return rows


def _restore_merchant_rules(backup: dict) -> list[tuple]:
    rows = []
    for index, item in enumerate(_backup_list(backup, "merchant_rules"), start=1):
        record = _backup_object(item, f"merchant_rules[{index}]")
        merchant_name = clean_merchant_description(
            _backup_required_text(record, "merchant", f"merchant_rules[{index}]", max_length=200)
        )
        merchant_key_value = merchant_key(
            _backup_optional_text(record, "merchant_key", max_length=200) or merchant_name
        )
        updated_at = _backup_optional_text(record, "updated_at", max_length=80)
        rows.append((
            merchant_key_value,
            merchant_name,
            _backup_required_text(record, "category", f"merchant_rules[{index}]", max_length=80),
            updated_at,
            updated_at,
        ))
    return rows


def _restore_recurring_ignores(backup: dict) -> list[tuple]:
    rows = []
    for index, item in enumerate(_backup_list(backup, "recurring_ignores"), start=1):
        record = _backup_object(item, f"recurring_ignores[{index}]")
        merchant_name = clean_merchant_description(
            _backup_required_text(record, "merchant", f"recurring_ignores[{index}]", max_length=200)
        )
        merchant_key_value = merchant_key(
            _backup_optional_text(record, "merchant_key", max_length=200) or merchant_name
        )
        rows.append((
            merchant_key_value,
            merchant_name,
            _backup_optional_text(record, "created_at", max_length=80),
        ))
    return rows


def _restore_anomaly_ignores(backup: dict) -> list[tuple]:
    rows = []
    for index, item in enumerate(_backup_list(backup, "anomaly_ignores"), start=1):
        record = _backup_object(item, f"anomaly_ignores[{index}]")
        transaction = _backup_object(
            record.get("transaction"),
            f"anomaly_ignores[{index}].transaction",
        )
        transaction_date = _backup_date(
            transaction,
            "date",
            f"anomaly_ignores[{index}].transaction",
        )
        description = _backup_required_text(
            transaction,
            "description",
            f"anomaly_ignores[{index}].transaction",
            max_length=200,
        )
        amount_cents = _backup_amount_cents(
            transaction,
            "amount",
            f"anomaly_ignores[{index}].transaction",
        )
        category = _backup_required_text(
            transaction,
            "category",
            f"anomaly_ignores[{index}].transaction",
            max_length=80,
        )
        source_file = _backup_optional_text(transaction, "source_file", max_length=200)
        account_name = _backup_optional_text(transaction, "account_name", max_length=80)
        transaction_key = _backup_optional_text(record, "transaction_key", max_length=600)
        rows.append((
            transaction_key or anomaly_transaction_key(
                transaction_date,
                description,
                amount_cents,
                category,
                source_file,
                account_name,
            ),
            transaction_date,
            description,
            amount_cents,
            category,
            source_file,
            account_name,
            _backup_optional_text(record, "created_at", max_length=80),
        ))
    return rows


def _restore_csv_import_presets(backup: dict) -> list[tuple]:
    rows = []
    for index, item in enumerate(_backup_list(backup, "csv_import_presets"), start=1):
        record = _backup_object(item, f"csv_import_presets[{index}]")
        rows.append((
            _backup_required_text(record, "name", f"csv_import_presets[{index}]", max_length=80),
            _backup_required_text(record, "date_column", f"csv_import_presets[{index}]", max_length=120),
            _backup_required_text(record, "description_column", f"csv_import_presets[{index}]", max_length=120),
            _backup_optional_text(record, "amount_column", max_length=120),
            _backup_optional_text(record, "debit_column", max_length=120),
            _backup_optional_text(record, "credit_column", max_length=120),
            _backup_optional_text(record, "type_column", max_length=120),
            _backup_optional_text(record, "category_column", max_length=120),
            _backup_optional_text(record, "account_column", max_length=120),
            _backup_optional_text(record, "updated_at", max_length=80),
        ))
    return rows


def _restore_budgets(backup: dict) -> list[tuple]:
    rows = []
    for index, item in enumerate(_backup_list(backup, "budgets"), start=1):
        record = _backup_object(item, f"budgets[{index}]")
        rows.append((
            _backup_month(record, "month", f"budgets[{index}]"),
            _backup_required_text(record, "category", f"budgets[{index}]", max_length=80),
            _backup_positive_amount_cents(record, "amount", f"budgets[{index}]"),
            _backup_optional_text(record, "updated_at", max_length=80),
        ))
    return rows


def _restore_ask_history(backup: dict) -> list[tuple]:
    rows = []
    for index, item in enumerate(_backup_list(backup, "ask_history"), start=1):
        record = _backup_object(item, f"ask_history[{index}]")
        categories = record.get("categories", [])
        if not isinstance(categories, list) or not all(isinstance(category, str) for category in categories):
            raise ValueError(f"ask_history[{index}].categories must be a list of strings.")
        rows.append((
            _backup_required_text(record, "question", f"ask_history[{index}]", max_length=500),
            _backup_required_text(record, "answer", f"ask_history[{index}]", max_length=2000),
            _backup_optional_amount(record, "amount", f"ask_history[{index}]"),
            json.dumps(categories),
            _backup_optional_month(record, "month", f"ask_history[{index}]"),
            _backup_required_text(record, "intent", f"ask_history[{index}]", max_length=80),
            _backup_optional_text(record, "created_at", max_length=80),
        ))
    return rows


def _backup_list(backup: dict, key: str) -> list:
    value = backup.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"Backup field '{key}' must be a list.")
    return value


def _backup_object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _backup_required_text(record: dict, key: str, label: str, max_length: int) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{label}.{key} is required.")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{label}.{key} is required.")
    if len(normalized) > max_length:
        raise ValueError(f"{label}.{key} must be {max_length} characters or fewer.")
    return normalized


def _backup_optional_text(record: dict, key: str, max_length: int) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text.")
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise ValueError(f"{key} must be {max_length} characters or fewer.")
    return normalized


def _backup_date(record: dict, key: str, label: str) -> str:
    value = _backup_required_text(record, key, label, max_length=10)
    _validate_iso_date(value, f"{label}.{key}")
    return value


def _backup_optional_date(record: dict, key: str, label: str) -> str | None:
    value = _backup_optional_text(record, key, max_length=10)
    if value is None:
        return None
    _validate_iso_date(value, f"{label}.{key}")
    return value


def _backup_month(record: dict, key: str, label: str) -> str:
    value = _backup_required_text(record, key, label, max_length=7)
    _validate_month_text(value, f"{label}.{key}")
    return value


def _backup_optional_month(record: dict, key: str, label: str) -> str | None:
    value = _backup_optional_text(record, key, max_length=7)
    if value is None:
        return None
    _validate_month_text(value, f"{label}.{key}")
    return value


def _backup_amount_cents(record: dict, key: str, label: str) -> int:
    return _dollars_to_cents(_backup_amount(record, key, label))


def _backup_positive_amount_cents(record: dict, key: str, label: str) -> int:
    amount_cents = _backup_amount_cents(record, key, label)
    if amount_cents <= 0:
        raise ValueError(f"{label}.{key} must be greater than zero.")
    return amount_cents


def _backup_optional_amount(record: dict, key: str, label: str) -> float | None:
    value = record.get(key)
    if value is None:
        return None
    return _backup_amount(record, key, label)


def _backup_amount(record: dict, key: str, label: str) -> float:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}.{key} must be a number.")
    return float(value)


def _backup_non_negative_int(record: dict, key: str, label: str) -> int:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label}.{key} must be a non-negative integer.")
    return value


def _validate_iso_date(value: str, label: str) -> None:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD format.") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use YYYY-MM-DD format.")


def _validate_month_text(value: str, label: str) -> None:
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError(f"{label} must use YYYY-MM format.")


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


def _next_month(month: str | None) -> str | None:
    if month is None:
        return None

    current = date.fromisoformat(f"{month}-01")
    if current.month == 12:
        return f"{current.year + 1}-01"
    return f"{current.year}-{current.month + 1:02d}"


def _uploads_for_report_month(uploads: list[dict], month: str | None) -> list[dict]:
    if month is None:
        return uploads

    month_start, month_end = _month_bounds(month)
    matching_uploads = []
    for upload in uploads:
        first_date = upload["first_transaction_date"]
        last_date = upload["last_transaction_date"]
        if first_date is None or last_date is None:
            continue

        upload_start = date.fromisoformat(first_date)
        upload_end = date.fromisoformat(last_date)
        if upload_start < month_end and upload_end >= month_start:
            matching_uploads.append(upload)
    return matching_uploads


def _import_quality_status(transaction_count: int, review_count: int, anomaly_count: int) -> str:
    if transaction_count == 0:
        return "empty"
    if review_count or anomaly_count:
        return "needs_review"
    return "ready"


def _import_quality_notes(report: dict) -> list[str]:
    if report["transaction_count"] == 0:
        return ["No imported transactions found for this view."]

    notes = [
        f"{report['transaction_count']} transaction{'' if report['transaction_count'] == 1 else 's'} available for this view."
    ]
    if report["latest_upload"]:
        upload = report["latest_upload"]
        notes.append(
            f"Latest import: {upload['filename']} imported {upload['imported_count']} and skipped "
            f"{upload['duplicates_skipped']} duplicate{'' if upload['duplicates_skipped'] == 1 else 's'}."
        )
    if report["review_count"]:
        notes.append(
            f"{report['review_count']} transaction{'' if report['review_count'] == 1 else 's'} need category review."
        )
    if report["anomaly_count"]:
        notes.append(
            f"{report['anomaly_count']} active anomal{'y' if report['anomaly_count'] == 1 else 'ies'} need a look."
        )
    if report["recurring_count"]:
        notes.append(
            f"{report['recurring_count']} recurring charge pattern{'' if report['recurring_count'] == 1 else 's'} detected."
        )
    if report["duplicates_skipped"] and not any("duplicate" in note for note in notes[1:]):
        notes.append(
            f"{report['duplicates_skipped']} duplicate{'' if report['duplicates_skipped'] == 1 else 's'} skipped across matching uploads."
        )
    return notes[:5]


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
    return _projected_recurring_charges(month, coverage_date=coverage_date)


def _projected_recurring_charges(month: str, coverage_date: date | None = None) -> list[dict]:
    projected = []
    for charge in recurring_charges(limit=100):
        for expected_date in _project_recurring_dates_for_month(charge, month, after=coverage_date):
            projected.append({**charge, "next_expected_date": expected_date.isoformat()})
    return sorted(projected, key=lambda item: (item["next_expected_date"], item["merchant"]))


def _project_recurring_dates_for_month(charge: dict, month: str, after: date | None = None) -> list[date]:
    start_date, end_date = _month_bounds(month)
    expected_date = date.fromisoformat(charge["next_expected_date"])
    expected_dates = []

    while expected_date < start_date:
        expected_date = _advance_recurring_date(expected_date, charge["cadence"])

    while expected_date < end_date:
        if after is None or expected_date > after:
            expected_dates.append(expected_date)
        expected_date = _advance_recurring_date(expected_date, charge["cadence"])

    return expected_dates


def _advance_recurring_date(value: date, cadence: str) -> date:
    if cadence == "weekly":
        return value + timedelta(days=7)
    if cadence == "biweekly":
        return value + timedelta(days=14)
    if cadence == "quarterly":
        return _add_months(value, 3)
    return _add_months(value, 1)


def _add_months(value: date, month_delta: int) -> date:
    total_months = (value.year * 12) + (value.month - 1) + month_delta
    year = total_months // 12
    month = (total_months % 12) + 1
    month_start, next_month_start = _month_bounds(f"{year}-{month:02d}")
    last_day = (next_month_start - timedelta(days=1)).day
    return date(month_start.year, month_start.month, min(value.day, last_day))


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


def _category_spending_history(month: str, lookback: int = 3) -> dict[str, list[dict]]:
    with connect() as conn:
        rows = conn.execute(
            """
            WITH recent_months AS (
                SELECT DISTINCT substr(transaction_date, 1, 7) AS month
                FROM transactions
                WHERE amount_cents < 0
                  AND substr(transaction_date, 1, 7) <= ?
                ORDER BY month DESC
                LIMIT ?
            )
            SELECT
                substr(t.transaction_date, 1, 7) AS month,
                t.category,
                COALESCE(SUM(ABS(t.amount_cents)), 0) AS total_cents,
                COUNT(*) AS transaction_count
            FROM transactions t
            JOIN recent_months rm ON rm.month = substr(t.transaction_date, 1, 7)
            WHERE t.amount_cents < 0
            GROUP BY month, t.category
            ORDER BY month DESC, total_cents DESC
            """,
            (month, lookback),
        ).fetchall()

    history: dict[str, list[dict]] = {}
    for row in rows:
        history.setdefault(row["category"], []).append({
            "month": row["month"],
            "total_cents": int(row["total_cents"]),
            "transaction_count": row["transaction_count"],
        })
    return history


def _recurring_spending_by_category(month: str) -> dict[str, int]:
    spending: dict[str, int] = {}
    for charge in _upcoming_recurring_charges(month, coverage_date=None):
        spending[charge["category"]] = spending.get(charge["category"], 0) + _dollars_to_cents(charge["average_amount"])
    return spending


def _round_up_to_nearest_cents(value_cents: int, nearest_cents: int = 1000) -> int:
    if value_cents <= 0:
        return 0
    return ((value_cents + nearest_cents - 1) // nearest_cents) * nearest_cents


def _dollars_to_cents(value: float) -> int:
    return int(round(float(value) * 100))


def _budget_recommendation_confidence(history_months: int, recurring_cents: int) -> str:
    if history_months >= 3 and recurring_cents:
        return "high"
    if history_months >= 3:
        return "medium"
    if history_months >= 2 or recurring_cents:
        return "medium"
    return "low"


def _budget_recommendation_action(recommended_cents: int, existing_cents: int | None) -> str:
    if existing_cents is None:
        return "create"
    if existing_cents < recommended_cents * 0.95:
        return "raise"
    if existing_cents > recommended_cents * 1.25:
        return "review"
    return "keep"


def _budget_recommendation_reason(
    history_months: int,
    average_cents: int,
    recurring_cents: int,
    recommended_cents: int,
    existing_cents: int | None,
) -> str:
    if recurring_cents >= average_cents and recurring_cents:
        reason = f"Expected recurring charges total {_format_money(cents_to_dollars(recurring_cents))}."
    elif history_months > 1:
        reason = (
            f"Recent average spending is {_format_money(cents_to_dollars(average_cents))} "
            f"across {history_months} months."
        )
    else:
        reason = f"Latest spending baseline is {_format_money(cents_to_dollars(average_cents))}."

    if existing_cents is None:
        return f"{reason} Suggested budget is {_format_money(cents_to_dollars(recommended_cents))}."
    if existing_cents < recommended_cents:
        gap = cents_to_dollars(recommended_cents - existing_cents)
        return f"{reason} Existing budget is {_format_money(gap)} below the suggestion."
    return reason


def _question_tokens(question: str) -> list[str]:
    normalized = normalize_text(question)
    tokens = []
    for token in re.findall(r"[a-z0-9][a-z0-9&'-]*", normalized):
        if len(token) < 3 or token in QUESTION_STOPWORDS or re.fullmatch(r"20\d{2}", token):
            continue
        tokens.append(token)
    return list(dict.fromkeys(tokens))


def _category_explanation_tokens(question: str) -> list[str]:
    ignored_terms = {
        "assigned",
        "categorised",
        "categorize",
        "categorized",
        "category",
        "classified",
        "classify",
        "confidence",
        "explain",
        "reason",
        "suggest",
        "suggested",
    }
    return [
        token
        for token in _question_tokens(question)
        if token not in ignored_terms
    ]


def _rank_question_transactions(question: str, month: str, limit: int) -> list[dict]:
    tokens = _question_tokens(question)
    where_sql, params = _month_filter(month)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, transaction_date, description, amount_cents, category, source_file, account_name
            FROM transactions
            {where_sql}
            ORDER BY ABS(amount_cents) DESC, transaction_date DESC
            LIMIT 500
            """,
            params,
        ).fetchall()

    scored = []
    for row in rows:
        score = _transaction_question_score(row, tokens, question)
        if score <= 0:
            continue
        item = _transaction_row_to_dict(row)
        item["score"] = score
        scored.append(item)

    return sorted(scored, key=lambda item: (item["score"], abs(item["amount"])), reverse=True)[:limit]


def _transaction_question_score(row: sqlite3.Row, tokens: list[str], question: str) -> int:
    if not tokens:
        return 0

    normalized_question = normalize_text(question)
    description = normalize_text(row["description"])
    merchant = normalize_text(clean_merchant_description(row["description"]))
    category = normalize_text(row["category"])
    source_file = normalize_text(row["source_file"] or "")
    score = 0
    for token in tokens:
        if token in description or token in merchant:
            score += 4
        if token in category:
            score += 3
        if token in source_file:
            score += 1

    if description and description in normalized_question:
        score += 6
    if merchant and merchant != description and merchant in normalized_question:
        score += 6
    if int(row["amount_cents"]) < 0 and any(term in normalized_question for term in ["expense", "charge", "purchase"]):
        score += 1
    if int(row["amount_cents"]) > 0 and any(term in normalized_question for term in ["income", "deposit", "payroll"]):
        score += 3
    return score


def _question_citations(
    summary: dict,
    top_categories: list[dict],
    top_merchants_list: list[dict],
    anomalies: list[dict],
    matches: list[dict],
) -> list[dict]:
    citations = [
        {
            "id": f"summary-{summary['month'] or 'all'}",
            "type": "summary",
            "title": f"{summary['month'] or 'All data'} summary",
            "detail": (
                f"{_format_money(summary['total_spending'])} spending, "
                f"{_format_money(summary['total_income'])} income, "
                f"{_format_money(summary['net'])} net"
            ),
            "amount": summary["total_spending"],
            "date": None,
            "category": None,
        }
    ]
    if top_categories:
        top_category = top_categories[0]
        citations.append({
            "id": f"category-{summary['month']}-{top_category['category']}",
            "type": "category",
            "title": top_category["category"],
            "detail": f"{top_category['transaction_count']} transactions",
            "amount": top_category["total"],
            "date": None,
            "category": top_category["category"],
        })
    if top_merchants_list:
        merchant = top_merchants_list[0]
        citations.append({
            "id": f"merchant-{summary['month']}-{merchant['merchant']}",
            "type": "merchant",
            "title": merchant["merchant"],
            "detail": f"{merchant['category']} merchant total",
            "amount": merchant["total"],
            "date": None,
            "category": merchant["category"],
        })
    if anomalies:
        anomaly = anomalies[0]
        citations.append({
            "id": f"anomaly-{anomaly['id']}",
            "type": "anomaly",
            "title": anomaly["description"],
            "detail": anomaly["reason"],
            "amount": abs(anomaly["amount"]),
            "date": anomaly["date"],
            "category": anomaly["category"],
        })
    for transaction in matches:
        citations.append({
            "id": f"transaction-{transaction['id']}",
            "type": "transaction",
            "title": transaction["description"],
            "detail": f"{transaction['date']} | {transaction['category']}",
            "amount": transaction["amount"],
            "date": transaction["date"],
            "category": transaction["category"],
        })
    return citations[:8]


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
    account_name: str | None = None,
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
    if account_name:
        clauses.append("account_name = ?")
        params.append(account_name)

    if not clauses:
        return "", []
    return f"WHERE {' AND '.join(clauses)}", params
