# Architecture

The Smart Personal Finance Tracker combines structured finance analytics with an
AI-ready product surface.

## MVP Flow

1. A user uploads a CSV bank statement or text-based PDF statement.
2. The backend parses common CSV columns or extracts transaction-like rows from PDF text.
3. Transactions are normalized into SQLite with cent-based amounts, and each successful upload is logged with imported and duplicate counts.
4. Starter categorization rules assign categories from merchant descriptions.
5. Users can correct transaction categories, save exact merchant rules for future imports, set monthly category budgets, and apply suggested budgets.
6. Dashboard endpoints return searchable transactions, upload history, monthly category summaries, monthly insight reports, cash-flow forecasts, budget recommendations, budgets, recurring charges, merchants, trends, largest expenses, anomalies, and saved rules.
7. The question endpoint routes common finance questions to exact SQL-backed totals, rankings, monthly reports, forecast projections, and suggested budgets. Broader questions fall through to a retrieval layer that returns cited transaction and aggregate evidence.
8. The React frontend uploads statements and displays import history, summaries, monthly insights, cash-flow forecasts, budget recommendations, budget progress, recurring charges, anomalies, editable categories, saved rules, filtered transactions, CSV export, and a finance Q&A panel with citations.

## Trust Boundary

Exact totals should come from database queries, not from an LLM. The retrieval layer
can provide cited evidence for broad answers, while future AI features can classify
ambiguous merchants, explain spending patterns, and route questions. Arithmetic
should stay deterministic.

## Future AI Layer

- Smarter category suggestions with user review
- Budget recommendation explanations from recurring spending and forecast patterns
- LLM-generated answers over retrieved statement, transaction, and user-note evidence
- Natural-language query routing to structured SQL calculations
- Monthly narrative summaries with cited transaction evidence
