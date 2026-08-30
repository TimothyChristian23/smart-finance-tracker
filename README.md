# Smart Personal Finance Tracker

A full-stack personal finance assistant that imports bank statement data, categorizes
transactions, detects unusual spending, and answers natural-language questions using
structured transaction data.

The first implementation slice focuses on trustworthy analytics:

- CSV transaction upload
- Text-based PDF statement upload
- SQLite transaction storage
- Rule-based starter categorization
- Editable transaction categories
- Saved merchant rules that apply to future imports
- Monthly category budgets with live progress
- Recurring charge and subscription detection
- Monthly spending summaries
- Month, category, merchant, and trend analytics
- Basic anomaly detection
- Deterministic question answering for spending, income, category, merchant, budget, recurring charge, largest expense, and anomaly questions
- React dashboard scaffold
- Locked frontend dependency install and production build
- GitHub Actions backend test and frontend build workflows

Future AI layers can improve categorization, explain trends, and add RAG over statement
notes and transaction context.

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
```

Useful API endpoints:

```text
GET  /summary?month=2026-07
GET  /months
GET  /categories?month=2026-07
GET  /category-options
GET  /budgets?month=2026-07
GET  /trends
GET  /merchants?month=2026-07
GET  /merchant-rules
GET  /recurring
GET  /expenses/largest?month=2026-07
GET  /anomalies?month=2026-07
PUT  /budgets
PATCH /transactions/{id}/category
DELETE /budgets/{id}
DELETE /merchant-rules/{id}
POST /ask
```

PDF uploads are supported when the statement exposes selectable text with rows like:

```text
2026-07-02 Trader Joes -86.42
```

Statements that are scanned images will need OCR support before they can be imported.

Recurring charge detection needs the same merchant to appear across multiple months,
so it becomes useful after importing a few statements. Upload
`data/sample_recurring_transactions.csv` if you want demo recurring charges right away.

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

GitHub Actions runs backend tests and the frontend production build on pushes and pull requests.
