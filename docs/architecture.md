# Architecture

The Smart Personal Finance Tracker combines structured finance analytics with an
AI-ready product surface.

## MVP Flow

1. A user uploads or previews a CSV bank statement or text-based PDF statement, or manually adds a cash or one-off transaction.
2. The backend parses common CSV columns, common bank-specific CSV aliases, debit/credit split columns, saved CSV mapping presets, and flexible transaction-like rows from PDF text, then cleans noisy bank descriptors into stable merchant names.
3. Preview requests return normalized rows, category assignments, confidence, source, explanation, totals, date ranges, duplicate estimates, parser diagnostics, skipped-line examples, and row-level CSV errors without saving data.
4. Direct import requests normalize transactions into SQLite with cent-based amounts and optional account labels; reviewed import requests save the user's edited preview categories. Each successful upload is logged with imported and duplicate counts.
5. An explainable local classifier assigns starter categories from high-confidence merchant descriptions, scores lower-confidence signals for review, and lets saved normalized merchant rules override future imports.
6. Optional OpenAI-backed AI Assist can re-score the current import preview or category review queue when `OPENAI_API_KEY` is configured. It sends only candidates after an explicit dashboard confirmation, updates only unsaved preview rows, and never imports data or changes existing transactions automatically.
7. Users can add manual transactions, correct transaction categories, edit preview categories before import, save CSV mapping presets for bank-specific headers, edit or delete transaction details, split expense transactions across categories, review confidence-scored category suggestions, dismiss or restore rejected category suggestions, create normalized merchant rules for future imports, optionally apply rules to matching historical transactions, set monthly category budgets, and apply suggested budgets.
8. Dashboard endpoints return searchable transactions, split-aware category totals, upload history, import quality reports, account summaries, monthly category review suggestions, dismissed category review preferences, monthly category summaries, monthly insight reports, cash-flow forecasts, recurring bill calendars, budget recommendations, budgets, recurring charges, ignored recurring preferences, merchants, trends, largest expenses, anomalies, dismissed anomaly preferences, and saved rules.
9. The question endpoint routes common finance questions to exact SQL-backed totals, split-aware category spending, account summaries, rankings, monthly reports, category explanations, forecast projections, recurring bill calendars, and suggested budgets. Broader questions fall through to a retrieval layer that returns cited transaction and aggregate evidence. Each answered question is saved to local Q&A history, and clear follow-up wording can reuse the previous answer's month.
10. The React frontend previews and uploads statements, supports reviewed import category edits, AI-assisted import preview category suggestions, CSV mapping preset management, manual transaction entry, transaction deletion, transaction split editing, direct merchant rule creation, AI-assisted category review, category suggestion dismiss/restore controls, recurring charge hide/restore controls, and anomaly dismiss/restore controls, then displays import history, import quality, account labels, account summaries, monthly insights, cash-flow forecasts, recurring bill calendars, budget recommendations, category review suggestions with explanations, budget progress, recurring charges, anomalies, editable transaction details, saved rules, filtered transactions, CSV export, full JSON backup export, guarded JSON backup restore, and a finance Q&A panel with citations and recent question history.

## Trust Boundary

Exact totals should come from database queries, not from an LLM. The local
classifier can provide explainable category signals without sending data away,
merchant normalization should keep noisy statement descriptors from fragmenting
rules and analytics, and the retrieval layer can provide cited evidence for
broad answers. Future LLM features can classify ambiguous merchants, explain
spending patterns, and route questions. Arithmetic should stay deterministic.
AI Assist is explicitly user-triggered; the dashboard warns that review
candidates sent to OpenAI include transaction descriptions, cleaned merchant
names, dates, amounts, current categories, local suggestions, local reasons, and
account labels. The response is shown as suggestions and never mutates stored
transactions until the user applies one.

Users can export a full local JSON backup that includes transactions, transaction
split allocations, upload history, budgets, merchant rules, category review
dismissals, CSV mapping presets, recurring ignore preferences, anomaly
dismissals, Q&A history, monthly summaries, and counts. JSON
restore validates the backup first, requires typed `RESTORE` confirmation, then
replaces local durable records and lets summaries recompute from SQLite.
Full local reset requires a typed `RESET` confirmation in both the dashboard and
API, then clears transactions, transaction split allocations, uploads, budgets, merchant rules, category review
dismissals, recurring ignores, anomaly dismissals, CSV mapping presets, and Q&A history.

The UI smoke workflow starts FastAPI and Vite against an isolated SQLite database,
then drives the browser through CSV mapping preset creation, import
preview diagnostics/import, transaction splitting, manual transaction
add/edit/delete, backup download, reset/restore, and account-aware Q&A.

## Future AI Layer

- User-tunable AI prompt preferences based on accepted and rejected suggestions
- Budget recommendation explanations from recurring spending and forecast patterns
- LLM-generated answers over retrieved statement, transaction, and user-note evidence
- Natural-language query routing to structured SQL calculations
- Monthly narrative summaries with cited transaction evidence
