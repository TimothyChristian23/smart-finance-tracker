# Architecture

The Smart Personal Finance Tracker combines structured finance analytics with an
AI-ready product surface.

## MVP Flow

1. A user uploads a CSV bank statement or text-based PDF statement.
2. The backend parses common CSV columns or extracts transaction-like rows from PDF text.
3. Transactions are normalized into SQLite with cent-based amounts, and each successful upload is logged with imported and duplicate counts.
4. Starter categorization rules assign categories from merchant descriptions.
5. Users can correct transaction categories, save exact merchant rules for future imports, and set monthly category budgets.
6. Dashboard endpoints return searchable transactions, upload history, monthly category summaries, monthly insight reports, budgets, recurring charges, merchants, trends, largest expenses, anomalies, and saved rules.
7. The question endpoint routes common finance questions to exact SQL-backed totals, rankings, and monthly reports.
8. The React frontend uploads statements and displays import history, summaries, monthly insights, budget progress, recurring charges, anomalies, editable categories, saved rules, filtered transactions, CSV export, and a finance Q&A panel.

## Trust Boundary

Exact totals should come from database queries, not from an LLM. Future AI features
can classify ambiguous merchants, explain spending patterns, and route questions,
but arithmetic should stay deterministic.

## Future AI Layer

- Smarter category suggestions with user review
- Budget recommendations from recurring spending patterns
- RAG over uploaded statements and user notes
- Natural-language query routing to structured SQL calculations
- Monthly narrative summaries with cited transaction evidence
