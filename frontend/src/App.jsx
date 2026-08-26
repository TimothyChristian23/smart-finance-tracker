import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BarChart3, FileUp, MessageSquare, RefreshCw } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export default function App() {
  const [health, setHealth] = useState("Checking");
  const [summary, setSummary] = useState(null);
  const [transactions, setTransactions] = useState([]);
  const [anomalies, setAnomalies] = useState([]);
  const [month, setMonth] = useState("2026-07");
  const [uploadStatus, setUploadStatus] = useState("");
  const [question, setQuestion] = useState("How much did I spend on food in 2026-07?");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    refreshDashboard();
  }, [month]);

  async function refreshDashboard() {
    try {
      const healthPayload = await request("/health");
      setHealth(healthPayload.status === "ok" ? "Online" : "Offline");
      const [summaryPayload, transactionPayload, anomalyPayload] = await Promise.all([
        request(`/summary?month=${encodeURIComponent(month)}`),
        request("/transactions"),
        request("/anomalies"),
      ]);
      setSummary(summaryPayload);
      setTransactions(transactionPayload);
      setAnomalies(anomalyPayload);
    } catch (error) {
      setHealth("Offline");
      setUploadStatus(error.message);
    }
  }

  async function handleUpload(event) {
    event.preventDefault();
    const file = event.currentTarget.elements.statement.files[0];
    if (!file) {
      setUploadStatus("Choose a CSV statement first.");
      return;
    }

    setBusy(true);
    setUploadStatus("Importing transactions...");
    const formData = new FormData();
    formData.append("file", file);

    try {
      const payload = await request("/transactions/upload", {
        method: "POST",
        body: formData,
      });
      setUploadStatus(`Imported ${payload.imported} transactions from ${payload.filename}.`);
      event.currentTarget.reset();
      await refreshDashboard();
    } catch (error) {
      setUploadStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleAsk(event) {
    event.preventDefault();
    if (!question.trim()) return;

    setBusy(true);
    try {
      const payload = await request("/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      setAnswer(payload.answer);
    } catch (error) {
      setAnswer(error.message);
    } finally {
      setBusy(false);
    }
  }

  const topTransactions = useMemo(() => transactions.slice(0, 8), [transactions]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Finance assistant</p>
          <h1>Smart Personal Finance Tracker</h1>
        </div>
        <div className="topbar-actions">
          <label htmlFor="month">Month</label>
          <input id="month" type="month" value={month} onChange={(event) => setMonth(event.target.value)} />
          <span className={`status ${health === "Online" ? "status-online" : "status-offline"}`}>{health}</span>
          <button className="icon-button" type="button" onClick={refreshDashboard} aria-label="Refresh dashboard">
            <RefreshCw size={18} />
          </button>
        </div>
      </header>

      <section className="metrics-grid" aria-label="Monthly finance metrics">
        <Metric label="Spending" value={summary ? money(summary.total_spending) : "$0.00"} />
        <Metric label="Income" value={summary ? money(summary.total_income) : "$0.00"} />
        <Metric label="Net" value={summary ? money(summary.net) : "$0.00"} />
        <Metric label="Transactions" value={summary?.transaction_count ?? 0} />
      </section>

      <section className="dashboard-grid">
        <section className="panel chart-panel">
          <PanelTitle icon={<BarChart3 size={18} />} title="Category Spend" />
          <div className="chart-wrap">
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={summary?.categories ?? []}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="category" tick={{ fontSize: 12 }} />
                <YAxis tickFormatter={(value) => `$${value}`} tick={{ fontSize: 12 }} />
                <Tooltip formatter={(value) => money(value)} />
                <Bar dataKey="total" fill="#267a6d" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>

        <section className="panel">
          <PanelTitle icon={<FileUp size={18} />} title="Import Statement" />
          <form className="upload-form" onSubmit={handleUpload}>
            <input name="statement" type="file" accept=".csv" />
            <button type="submit" disabled={busy}>Upload CSV</button>
          </form>
          <p className="helper-text">{uploadStatus || "Try data/sample_transactions.csv to seed the dashboard."}</p>
        </section>

        <section className="panel">
          <PanelTitle icon={<MessageSquare size={18} />} title="Ask About Spending" />
          <form className="ask-form" onSubmit={handleAsk}>
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} />
            <button type="submit" disabled={busy}>Ask</button>
          </form>
          {answer && <p className="answer">{answer}</p>}
        </section>

        <section className="panel">
          <PanelTitle icon={<AlertTriangle size={18} />} title="Anomalies" />
          <div className="list">
            {anomalies.length ? anomalies.map((item) => (
              <div className="list-row" key={item.id}>
                <div>
                  <strong>{item.description}</strong>
                  <span>{item.category} on {item.date}</span>
                </div>
                <b>{money(Math.abs(item.amount))}</b>
              </div>
            )) : <p className="empty">No anomalies yet.</p>}
          </div>
        </section>
      </section>

      <section className="panel transactions-panel">
        <h2>Recent Transactions</h2>
        <div className="transaction-table">
          <div className="table-heading">Date</div>
          <div className="table-heading">Description</div>
          <div className="table-heading">Category</div>
          <div className="table-heading align-right">Amount</div>
          {topTransactions.map((transaction) => (
            <TransactionRow key={transaction.id} transaction={transaction} />
          ))}
        </div>
      </section>
    </main>
  );
}

function Metric({ label, value }) {
  return (
    <article className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function PanelTitle({ icon, title }) {
  return (
    <div className="panel-title">
      {icon}
      <h2>{title}</h2>
    </div>
  );
}

function TransactionRow({ transaction }) {
  return (
    <>
      <div>{transaction.date}</div>
      <div>{transaction.description}</div>
      <div><span className="category-pill">{transaction.category}</span></div>
      <div className={`align-right ${transaction.amount < 0 ? "negative" : "positive"}`}>
        {money(transaction.amount)}
      </div>
    </>
  );
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "Request failed.");
  }
  return payload;
}

function money(value) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(value);
}
