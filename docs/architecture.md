# Architecture

The Smart Personal Finance Tracker combines structured finance analytics with an
AI-ready product surface.

## MVP Flow

1. A user uploads a CSV bank statement.
2. The backend parses common date, description, amount, debit, and credit columns.
3. Transactions are normalized into SQLite with cent-based amounts.
4. Starter categorization rules assign categories from merchant descriptions.
5. Dashboard endpoints return recent transactions, monthly category summaries, merchants, trends, largest expenses, and anomalies.
6. The question endpoint routes common finance questions to exact SQL-backed totals and rankings.
7. The React frontend uploads CSVs and displays summaries, anomalies, and a finance Q&A panel.

## Trust Boundary

Exact totals should come from database queries, not from an LLM. Future AI features
can classify ambiguous merchants, explain spending patterns, and route questions,
but arithmetic should stay deterministic.

## Future AI Layer

- AI-assisted category suggestions with user review
- RAG over uploaded statements and user notes
- Natural-language query routing to structured SQL calculations
- Monthly narrative summaries with cited transaction evidence
