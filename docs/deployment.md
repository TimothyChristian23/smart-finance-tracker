# Deployment Notes

The app is easiest to host as two services:

- Backend: a FastAPI service with a persistent SQLite volume.
- Frontend: a static Vite build pointed at the backend URL.

Keep real financial data out of the repo. For a public demo, use the bundled
synthetic sample data or a disposable database.

## Backend

Install and start FastAPI:

```bash
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
```

Required environment:

```env
FINANCE_DB_PATH=/data/finance.sqlite3
FRONTEND_ORIGIN=https://your-frontend.example.com
```

`FRONTEND_ORIGIN` can contain multiple comma-separated origins:

```env
FRONTEND_ORIGIN=https://your-frontend.example.com,https://preview.example.com
```

Optional AI Assist environment:

```env
OPENAI_API_KEY=sk-...
OPENAI_CATEGORY_MODEL=gpt-5-nano
```

Use a persistent disk or volume for `FINANCE_DB_PATH`. Ephemeral filesystems can
erase uploaded statements, transactions, budgets, saved presets, and Q&A
history when the service restarts.

Use `/health` as the service health check.

## Frontend

Build the static site with the deployed backend URL:

```bash
cd frontend
npm ci
VITE_API_BASE_URL=https://your-backend.example.com npm run build
```

Deploy the generated `frontend/dist` directory with any static host.

For local development, copy `frontend/.env.example` to `frontend/.env.local` and
adjust `VITE_API_BASE_URL` if your backend is not running on
`http://localhost:8000`.

## Smoke Checks

After deploying:

1. Open the frontend URL and verify the dashboard loads.
2. Check the backend URL at `/health`.
3. Import the sample data or a small synthetic CSV.
4. Confirm dashboard totals, transaction search, Q&A, backup export, and data
   reset still work.
5. If AI Assist is enabled, verify the warning appears before any request is
   sent to OpenAI.
