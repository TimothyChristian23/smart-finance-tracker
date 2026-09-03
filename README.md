# Smart Personal Finance Tracker

A full-stack personal finance assistant that imports bank statement data, categorizes
transactions, detects unusual spending, and answers natural-language questions using
structured transaction data.

The first implementation slice focuses on trustworthy analytics:

- CSV transaction upload
- Text-based PDF statement upload
- Import preview with duplicate estimates before saving
- Reviewed import flow with editable preview categories before saving
- Manual transaction entry for cash purchases or one-off corrections
- Flexible CSV parsing for common bank column names and debit/credit formats
- Merchant cleanup for noisy bank descriptors and payment reference codes
- Optional account labels for statement imports and transaction filtering
- Account-level spending, income, and net summaries
- Upload history with imported and duplicate counts
- SQLite transaction storage
- Explainable starter categorization with confidence, source, and matched merchant signals
- Editable transaction categories
- Category review queue with confidence-scored suggestions
- Saved normalized merchant rules that apply to future imports
- Direct merchant rule creation with optional retroactive category updates
- Editable transaction details for date, description, signed amount, category, and account
- Individual transaction deletion with confirmation
- Monthly category budgets with live progress
- Budget recommendations from recent history and recurring charges
- Recurring charge and subscription detection with hide/restore controls
- Transaction search, category filtering, and CSV export
- Full local JSON backup export for transactions, uploads, budgets, merchant rules, recurring ignore preferences, and anomaly dismissals
- Guarded JSON backup restore for moving or recovering local development data
- Guarded local data reset with typed confirmation
- Monthly spending summaries
- Monthly insight reports with highlights, risks, and next actions
- Cash-flow forecasts from imported activity and upcoming recurring charges
- Month, category, merchant, and trend analytics
- Basic anomaly detection with dismiss/restore controls
- Deterministic question answering for spending, income, account, category, category explanation, merchant, budget, budget recommendation, forecast, recurring charge, monthly report, largest expense, and anomaly questions
- RAG-style broad Q&A with cited transaction and summary evidence
- Local Q&A history for recent finance questions and answers
- React dashboard scaffold
- Locked frontend dependency install and production build
- Browser UI smoke test for import, manual transactions, backup/restore, and Q&A
- GitHub Actions backend test, frontend build, and UI smoke workflows

Future LLM layers can improve ambiguous categorization, explain trends, and add
RAG over statement notes and transaction context.

## Project Structure

```text
smart-finance-tracker/
|-- backend/
|   |-- app/
|   |   |-- categorization.py
|   |   |-- database.py
|   |   `-- main.py
|   |-- tests/
|   |   `-- test_api.py
|   |-- pytest.ini
|   `-- requirements.txt
|-- frontend/
|   |-- src/
|   |   |-- App.jsx
|   |   |-- main.jsx
|   |   `-- styles.css
|   |-- index.html
|   |-- package-lock.json
|   `-- package.json
|-- data/
|   |-- sample_recurring_transactions.csv
|   `-- sample_transactions.csv
|-- docs/
|   `-- architecture.md
|-- .env.example
|-- .gitignore
`-- README.md
```

## Backend Quick Start

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open the API docs:

```text
http://localhost:8000/docs
```

## Frontend Quick Start

```bash
cd frontend
npm install
npm run dev
```

If npm fails on Windows with `UNABLE_TO_VERIFY_LEAF_SIGNATURE`, run the install with the Windows certificate store enabled:

```powershell
$env:NODE_OPTIONS="--use-system-ca"
npm.cmd install
```

Open the React app:

```text
http://localhost:5173
```

## Try The Sample Data

Upload `data/sample_transactions.csv` through the frontend or API. Then ask:

```text
How much did I spend on food last month?
Tell me about Amazon in July 2026
How much did I spend on Chase Checking in July 2026?
```

Useful API endpoints:

```text
GET  /summary?month=2026-07
GET  /insights/monthly?month=2026-07
GET  /forecast/monthly?month=2026-08
GET  /months
GET  /uploads
POST /transactions
POST /transactions/preview
POST /transactions/import-reviewed
GET  /transactions?month=2026-07&category=Dining&search=coffee
GET  /transactions/export?month=2026-07
GET  /data/export
POST /data/import
GET  /accounts
GET  /accounts/summary?month=2026-07
GET  /ask/history
GET  /categories?month=2026-07
GET  /categories/review?month=2026-07
GET  /category-options
GET  /budgets?month=2026-07
GET  /budgets/recommendations?month=2026-08
GET  /trends
GET  /merchants?month=2026-07
GET  /merchant-rules
GET  /recurring
GET  /recurring/ignored
GET  /expenses/largest?month=2026-07
GET  /anomalies?month=2026-07
GET  /anomalies/ignored
PUT  /budgets
PUT  /merchant-rules
POST /recurring/ignored
POST /anomalies/{id}/ignore
PATCH /transactions/{id}/category
PATCH /transactions/{id}
DELETE /transactions/{id}
DELETE /budgets/{id}
DELETE /merchant-rules/{id}
DELETE /recurring/ignored/{id}
DELETE /anomalies/ignored/{id}
DELETE /data?confirmation=RESET
POST /ask
```

PDF uploads are supported when the statement exposes selectable text with rows like:

```text
2026-07-02 Trader Joes -86.42
```

Statements that are scanned images will need OCR support before they can be imported.

Use import preview from the dashboard or `POST /transactions/preview` to inspect
normalized rows, category assignments, totals, and duplicate estimates before
saving statement data. In the dashboard, preview categories can be edited before
saving through the reviewed import flow. CSV preview keeps valid rows visible and
reports row-level errors for lines that need cleanup; direct file importing remains
strict and rejects files with invalid rows.
CSV imports accept common bank-style headers such as `Date`, `Posting Date`,
`Transaction Date`, `Description`, `Transaction Description`, `Payee`, `Memo`,
`Amount`, `Transaction Amount`, `Debit Amount`, and `Credit Amount`. Debit values
are stored as expenses even when the export already includes a minus sign or
parentheses, and unsigned amount columns can use a `Type` column for debit/credit
direction.
Imported statement descriptors are cleaned into stable merchant names before
categorization, duplicate checks, recurring-charge detection, merchant summaries,
and saved merchant rules run.
Use the dashboard account label field or CSV headers like `Account` and
`Account Name` to track which account a statement came from. Transaction list and
CSV export requests can filter by account with `account=Chase%20Checking`.
The Account Summary panel and `GET /accounts/summary` endpoint break spending,
income, net cash flow, and transaction counts down by account.
Manual transactions can be added from the Transactions panel and are stored with
`manual` as their source.

Use `GET /data/export` or the dashboard Backup button to download a JSON snapshot
of local finance data before clearing or moving development databases.
Use the Privacy panel Restore control or `POST /data/import` with a backup JSON
file and `confirmation=RESTORE` to replace local data from a backup.
The dashboard Privacy panel can reset all local app records after typing `RESET`;
the API requires the same confirmation with `DELETE /data?confirmation=RESET`.

Recurring charge detection needs the same normalized merchant to appear across
multiple months, so it becomes useful after importing a few statements. Upload
`data/sample_recurring_transactions.csv` if you want demo recurring charges right away.
Hide a recurring merchant from the dashboard or `POST /recurring/ignored` when a
detected repeat charge should not affect forecasts or budget recommendations;
restore it later with `DELETE /recurring/ignored/{id}`.

Dismiss an anomaly from the dashboard or `POST /anomalies/{id}/ignore` when a
large expense is expected. Dismissed transactions are hidden from anomaly alerts,
monthly insight anomaly counts, and anomaly Q&A until restored with
`DELETE /anomalies/ignored/{id}`.

Monthly insight reports combine summary totals, budget progress, recurring charges,
top merchants, largest expenses, and anomalies into a deterministic snapshot. Ask
`Give me my monthly report for July 2026` to route the Q&A panel to that same report.

Cash-flow forecasts estimate month-end spending from imported activity and upcoming
recurring charges. Ask `What am I projected to spend in August 2026?` to get the
same forecast through the Q&A panel.

Budget recommendations use recent category spending and upcoming recurring charges
to suggest starting targets. Ask `What budgets do you recommend for August 2026?`
or apply a recommendation directly from the dashboard.

Broad Q&A questions retrieve relevant transaction evidence and return citations in
the answer card. Exact totals still come from deterministic database calculations.
Recent Q&A exchanges are saved locally and included in full JSON backups.
Clear follow-up questions such as `What about housing?` reuse the previous Q&A
month when no new month is provided.

The category review queue surfaces uncertain imported categories and suggests a
more likely category when the local classifier has enough signal. Preview rows
and review items include confidence, source, matched merchant signals, and a short
reason. Ask `Why was Trader Joes categorized as Food & Grocery in July 2026?` to
get the same explanation through Q&A. Applying a suggestion can also save a
merchant rule for future imports.
Merchant rules can also be created from the dashboard or `PUT /merchant-rules`;
turn on `apply_existing` to recategorize matching transactions already in SQLite.

## Notes

Use synthetic or exported demo data while developing. Do not commit real bank
statements, account numbers, or private financial records.

## Tests

Run backend tests locally:

```bash
cd backend
python -m pytest
```

Run the frontend production build locally:

```bash
cd frontend
npm run build
```

Run the browser smoke test locally:

```bash
cd frontend
npx playwright install chromium
npm run test:e2e
```

On Windows, set `NODE_OPTIONS=--use-system-ca` first if Playwright install hits
the local certificate error noted above.

GitHub Actions runs backend tests, the frontend production build, and the UI
smoke test on pushes and pull requests.
