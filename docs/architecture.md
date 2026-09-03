# Architecture

The Smart Personal Finance Tracker combines structured finance analytics with an
AI-ready product surface.

## MVP Flow

1. A user uploads or previews a CSV bank statement or text-based PDF statement, or manually adds a cash or one-off transaction.
2. The backend parses common CSV columns, common bank-specific CSV aliases, debit/credit split columns, and transaction-like rows from PDF text, then cleans noisy bank descriptors into stable merchant names.
3. Preview requests return normalized rows, category assignments, confidence, source, explanation, totals, date ranges, duplicate estimates, and row-level CSV errors without saving data.
4. Direct import requests normalize transactions into SQLite with cent-based amounts and optional account labels; reviewed import requests save the user's edited preview categories. Each successful upload is logged with imported and duplicate counts.
5. An explainable local classifier assigns starter categories from high-confidence merchant descriptions, scores lower-confidence signals for review, and lets saved normalized merchant rules override future imports.
6. Users can add manual transactions, correct transaction categories, edit preview categories before import, edit or delete transaction details, review confidence-scored category suggestions, create normalized merchant rules for future imports, optionally apply rules to matching historical transactions, set monthly category budgets, and apply suggested budgets.
7. Dashboard endpoints return searchable transactions, upload history, account summaries, monthly category review suggestions, monthly category summaries, monthly insight reports, cash-flow forecasts, budget recommendations, budgets, recurring charges, ignored recurring preferences, merchants, trends, largest expenses, anomalies, dismissed anomaly preferences, and saved rules.
8. The question endpoint routes common finance questions to exact SQL-backed totals, account summaries, rankings, monthly reports, category explanations, forecast projections, and suggested budgets. Broader questions fall through to a retrieval layer that returns cited transaction and aggregate evidence. Each answered question is saved to local Q&A history, and clear follow-up wording can reuse the previous answer's month.
9. The React frontend previews and uploads statements, supports reviewed import category edits, manual transaction entry, transaction deletion, direct merchant rule creation, recurring charge hide/restore controls, and anomaly dismiss/restore controls, then displays import history, account labels, account summaries, monthly insights, cash-flow forecasts, budget recommendations, category review suggestions with explanations, budget progress, recurring charges, anomalies, editable transaction details, saved rules, filtered transactions, CSV export, full JSON backup export, guarded JSON backup restore, and a finance Q&A panel with citations and recent question history.

## Trust Boundary

Exact totals should come from database queries, not from an LLM. The local
classifier can provide explainable category signals without sending data away,
merchant normalization should keep noisy statement descriptors from fragmenting
rules and analytics, and the retrieval layer can provide cited evidence for
broad answers. Future LLM features can classify ambiguous merchants, explain
spending patterns, and route questions. Arithmetic should stay deterministic.

Users can export a full local JSON backup that includes transactions, upload
history, budgets, merchant rules, recurring ignore preferences, anomaly
dismissals, Q&A history, monthly summaries, and counts. JSON restore validates
the backup first, requires typed `RESTORE` confirmation, then replaces local
durable records and lets summaries recompute from SQLite.
Full local reset requires a typed `RESET` confirmation in both the dashboard and
API, then clears transactions, uploads, budgets, merchant rules, recurring
ignores, anomaly dismissals, and Q&A history.

The UI smoke workflow starts FastAPI and Vite against an isolated SQLite database,
then drives the browser through import preview/import, manual transaction
add/edit/delete, backup download, reset/restore, and account-aware Q&A.

## Future AI Layer

- Optional LLM-assisted category suggestions with user review
- Budget recommendation explanations from recurring spending and forecast patterns
- LLM-generated answers over retrieved statement, transaction, and user-note evidence
- Natural-language query routing to structured SQL calculations
- Monthly narrative summaries with cited transaction evidence
