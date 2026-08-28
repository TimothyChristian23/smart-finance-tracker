import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  CircleDollarSign,
  FileUp,
  MessageSquare,
  ReceiptText,
  RefreshCw,
  Store,
  Trash2,
  TrendingUp,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const DEFAULT_QUESTION = "How much did I spend on food in 2026-07?";

export default function App() {
  const [health, setHealth] = useState("Checking");
  const [summary, setSummary] = useState(emptySummary());
  const [transactions, setTransactions] = useState([]);
  const [anomalies, setAnomalies] = useState([]);
  const [months, setMonths] = useState([]);
  const [categories, setCategories] = useState([]);
  const [trends, setTrends] = useState([]);
  const [merchants, setMerchants] = useState([]);
  const [largestExpenses, setLargestExpenses] = useState([]);
  const [month, setMonth] = useState("");
  const [uploadStatus, setUploadStatus] = useState("");
  const [question, setQuestion] = useState(DEFAULT_QUESTION);
  const [answer, setAnswer] = useState(null);
  const [busy, setBusy] = useState(false);
  const [lastUpdated, setLastUpdated] = useState("");

  const selectedMonthLabel = month || "All imported data";

  const refreshDashboard = useCallback(async () => {
    try {
      const healthPayload = await request("/health");
      setHealth(healthPayload.status === "ok" ? "Online" : "Offline");

      const monthsPayload = await request("/months");
      const activeMonth = month || monthsPayload[0]?.month || "";
      if (!month && activeMonth) {
        setMonth(activeMonth);
      }

      const [
        summaryPayload,
        transactionPayload,
        anomalyPayload,
        categoryPayload,
        trendPayload,
        merchantPayload,
        largestPayload,
      ] = await Promise.all([
        request(`/summary${queryString({ month: activeMonth })}`),
        request("/transactions?limit=12"),
        request(`/anomalies${queryString({ month: activeMonth, limit: 6 })}`),
        request(`/categories${queryString({ month: activeMonth })}`),
        request("/trends?limit=12"),
        request(`/merchants${queryString({ month: activeMonth, limit: 6 })}`),
        request(`/expenses/largest${queryString({ month: activeMonth, limit: 6 })}`),
      ]);

      setMonths(monthsPayload);
      setSummary(summaryPayload);
      setTransactions(transactionPayload);
      setAnomalies(anomalyPayload);
      setCategories(categoryPayload);
      setTrends(trendPayload);
      setMerchants(merchantPayload);
      setLargestExpenses(largestPayload);
      setLastUpdated(new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
    } catch (error) {
      setHealth("Offline");
      setUploadStatus(error.message);
    }
  }, [month]);

  useEffect(() => {
    refreshDashboard();
  }, [refreshDashboard]);

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
      const skipped = payload.duplicates_skipped || 0;
      setUploadStatus(skipped
        ? `Imported ${payload.imported} transactions and skipped ${skipped} duplicates from ${payload.filename}.`
        : `Imported ${payload.imported} transactions from ${payload.filename}.`);
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
    setAnswer(null);
    try {
      const payload = await request("/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      setAnswer(payload);
    } catch (error) {
      setAnswer({ answer: error.message, data: [] });
    } finally {
      setBusy(false);
    }
  }

  async function handleClear() {
    if (!window.confirm("Clear all imported transactions?")) return;

    setBusy(true);
    try {
      await request("/transactions", { method: "DELETE" });
      setUploadStatus("Transactions cleared.");
      setAnswer(null);
      setMonth("");
      await refreshDashboard();
    } catch (error) {
      setUploadStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  const recentTransactions = useMemo(() => transactions.slice(0, 10), [transactions]);
  const trendDomain = useMemo(() => [0, "auto"], []);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Finance assistant</p>
          <h1>Smart Personal Finance Tracker</h1>
          {lastUpdated && <p className="updated">Updated {lastUpdated}</p>}
        </div>
        <div className="topbar-actions">
          <label htmlFor="month">Month</label>
          <select id="month" value={month} onChange={(event) => setMonth(event.target.value)}>
            <option value="">All data</option>
            {months.map((item) => (
              <option value={item.month} key={item.month}>{item.month}</option>
            ))}
          </select>
          <span className={`status ${health === "Online" ? "status-online" : "status-offline"}`}>{health}</span>
          <button className="icon-button" type="button" onClick={refreshDashboard} aria-label="Refresh dashboard" title="Refresh dashboard">
            <RefreshCw size={18} />
          </button>
        </div>
      </header>

      <section className="metrics-grid" aria-label="Finance metrics">
        <Metric label="Spending" value={money(summary.total_spending)} tone="spend" />
        <Metric label="Income" value={money(summary.total_income)} tone="income" />
        <Metric label="Net" value={money(summary.net)} tone={summary.net >= 0 ? "income" : "spend"} />
        <Metric label="Transactions" value={summary.transaction_count} />
      </section>

      <section className="dashboard-grid">
        <section className="panel chart-panel">
          <PanelTitle icon={<BarChart3 size={18} />} title="Category Spend" detail={selectedMonthLabel} />
          <ChartFrame empty={!categories.length} emptyText="No category totals yet.">
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={categories} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="category" tick={{ fontSize: 12 }} interval={0} angle={-12} textAnchor="end" height={66} />
                <YAxis tickFormatter={(value) => `$${value}`} tick={{ fontSize: 12 }} width={56} />
                <Tooltip formatter={(value) => money(value)} />
                <Bar dataKey="total" fill="#24786a" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </ChartFrame>
        </section>

        <section className="panel trend-panel">
          <PanelTitle icon={<TrendingUp size={18} />} title="Monthly Trend" detail={`${trends.length} month${trends.length === 1 ? "" : "s"}`} />
          <ChartFrame empty={!trends.length} emptyText="No trend data yet.">
            <ResponsiveContainer width="100%" height={210}>
              <LineChart data={trends} margin={{ top: 8, right: 12, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                <YAxis domain={trendDomain} tickFormatter={(value) => `$${value}`} tick={{ fontSize: 12 }} width={56} />
                <Tooltip formatter={(value) => money(value)} />
                <Line type="monotone" dataKey="total_spending" stroke="#a45f20" strokeWidth={3} dot={{ r: 4 }} name="Spending" />
                <Line type="monotone" dataKey="total_income" stroke="#315f9f" strokeWidth={3} dot={{ r: 4 }} name="Income" />
              </LineChart>
            </ResponsiveContainer>
          </ChartFrame>
        </section>

        <section className="panel action-panel">
          <PanelTitle icon={<FileUp size={18} />} title="Import Statement" detail="CSV" />
          <form className="upload-form" onSubmit={handleUpload}>
            <input name="statement" type="file" accept=".csv,text/csv" />
            <div className="button-row">
              <button type="submit" disabled={busy}>Upload CSV</button>
              <button className="ghost-button" type="button" disabled={busy || !transactions.length} onClick={handleClear}>
                <Trash2 size={16} />
                Clear
              </button>
            </div>
          </form>
          {uploadStatus && <p className="helper-text">{uploadStatus}</p>}
        </section>

        <section className="panel ask-panel">
          <PanelTitle icon={<MessageSquare size={18} />} title="Ask About Spending" detail={selectedMonthLabel} />
          <form className="ask-form" onSubmit={handleAsk}>
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} />
            <button type="submit" disabled={busy}>Ask</button>
          </form>
          {answer && <AnswerCard answer={answer} />}
        </section>

        <section className="panel">
          <PanelTitle icon={<Store size={18} />} title="Top Merchants" detail={selectedMonthLabel} />
          <MoneyList items={merchants} emptyText="No merchant spend yet." getTitle={(item) => item.merchant} getSubtitle={(item) => item.category} />
        </section>

        <section className="panel">
          <PanelTitle icon={<ReceiptText size={18} />} title="Largest Expenses" detail={selectedMonthLabel} />
          <MoneyList items={largestExpenses} emptyText="No expenses yet." getTitle={(item) => item.description} getSubtitle={(item) => `${item.category} on ${item.date}`} />
        </section>

        <section className="panel anomalies-panel">
          <PanelTitle icon={<AlertTriangle size={18} />} title="Anomalies" detail={selectedMonthLabel} />
          <MoneyList items={anomalies} emptyText="No anomalies yet." getTitle={(item) => item.description} getSubtitle={(item) => item.reason || `${item.category} on ${item.date}`} tone="warn" />
        </section>
      </section>

      <section className="panel transactions-panel">
        <PanelTitle icon={<CircleDollarSign size={18} />} title="Recent Transactions" detail={`${recentTransactions.length} shown`} />
        <div className="transaction-table">
          <div className="table-heading">Date</div>
          <div className="table-heading">Description</div>
          <div className="table-heading">Category</div>
          <div className="table-heading align-right">Amount</div>
          {recentTransactions.map((transaction) => (
            <TransactionRow key={transaction.id} transaction={transaction} />
          ))}
        </div>
        {!recentTransactions.length && <p className="empty">No transactions imported.</p>}
      </section>
    </main>
  );
}

function Metric({ label, value, tone = "neutral" }) {
  return (
    <article className={`metric metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function PanelTitle({ icon, title, detail }) {
  return (
    <div className="panel-title">
      <div className="panel-title-main">
        {icon}
        <h2>{title}</h2>
      </div>
      {detail && <span>{detail}</span>}
    </div>
  );
}

function ChartFrame({ children, empty, emptyText }) {
  return <div className="chart-wrap">{empty ? <p className="empty chart-empty">{emptyText}</p> : children}</div>;
}

function MoneyList({ items, emptyText, getTitle, getSubtitle, tone = "default" }) {
  if (!items.length) return <p className="empty">{emptyText}</p>;

  return (
    <div className="list">
      {items.map((item) => (
        <div className="list-row" key={`${getTitle(item)}-${item.id ?? item.category}`}>
          <div>
            <strong>{getTitle(item)}</strong>
            <span>{getSubtitle(item)}</span>
          </div>
          <b className={tone === "warn" ? "warn" : ""}>{money(Math.abs(item.amount ?? item.total ?? 0))}</b>
        </div>
      ))}
    </div>
  );
}

function AnswerCard({ answer }) {
  return (
    <div className="answer">
      <strong>{answer.answer}</strong>
      {!!answer.categories?.length && <span>{answer.categories.join(" + ")}</span>}
      {!!answer.data?.length && <small>{answer.data.length} supporting result{answer.data.length === 1 ? "" : "s"}</small>}
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

function queryString(params) {
  const entries = Object.entries(params).filter(([, value]) => value !== "" && value !== null && value !== undefined);
  if (!entries.length) return "";
  return `?${new URLSearchParams(entries).toString()}`;
}

function emptySummary() {
  return {
    month: null,
    total_spending: 0,
    total_income: 0,
    net: 0,
    transaction_count: 0,
    categories: [],
  };
}

function money(value) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(value) || 0);
}
