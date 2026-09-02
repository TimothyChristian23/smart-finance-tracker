import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  BookmarkPlus,
  CalendarClock,
  Check,
  ClipboardList,
  CircleDollarSign,
  Download,
  Eye,
  FileUp,
  History,
  Lightbulb,
  MessageSquare,
  Plus,
  ReceiptText,
  Repeat2,
  RefreshCw,
  Search,
  Shield,
  Store,
  Tags,
  Target,
  Trash2,
  TrendingUp,
  X,
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
  const [insights, setInsights] = useState(emptyInsights());
  const [forecast, setForecast] = useState(emptyForecast());
  const [transactions, setTransactions] = useState([]);
  const [anomalies, setAnomalies] = useState([]);
  const [months, setMonths] = useState([]);
  const [categories, setCategories] = useState([]);
  const [trends, setTrends] = useState([]);
  const [merchants, setMerchants] = useState([]);
  const [largestExpenses, setLargestExpenses] = useState([]);
  const [categoryOptions, setCategoryOptions] = useState([]);
  const [categoryReview, setCategoryReview] = useState([]);
  const [merchantRules, setMerchantRules] = useState([]);
  const [budgets, setBudgets] = useState([]);
  const [budgetRecommendations, setBudgetRecommendations] = useState([]);
  const [recurringCharges, setRecurringCharges] = useState([]);
  const [uploads, setUploads] = useState([]);
  const [budgetDraft, setBudgetDraft] = useState({ category: "", amount: "" });
  const [transactionFilters, setTransactionFilters] = useState({ category: "", search: "" });
  const [month, setMonth] = useState("");
  const [uploadPreview, setUploadPreview] = useState(null);
  const [uploadStatus, setUploadStatus] = useState("");
  const [question, setQuestion] = useState(DEFAULT_QUESTION);
  const [answer, setAnswer] = useState(null);
  const [askHistory, setAskHistory] = useState([]);
  const [resetConfirmation, setResetConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [updatingTransactionId, setUpdatingTransactionId] = useState(null);
  const [lastUpdated, setLastUpdated] = useState("");

  const selectedMonthLabel = month || "All imported data";
  const hasLocalData = Boolean(months.length || uploads.length || budgets.length || merchantRules.length || askHistory.length);

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
        insightPayload,
        forecastPayload,
        transactionPayload,
        anomalyPayload,
        categoryPayload,
        trendPayload,
        merchantPayload,
        largestPayload,
        categoryOptionsPayload,
        categoryReviewPayload,
        merchantRulesPayload,
        budgetPayload,
        budgetRecommendationPayload,
        recurringPayload,
        uploadPayload,
        askHistoryPayload,
      ] = await Promise.all([
        request(`/summary${queryString({ month: activeMonth })}`),
        request(`/insights/monthly${queryString({ month: activeMonth })}`),
        request(`/forecast/monthly${queryString({ month: activeMonth })}`),
        request(`/transactions${queryString({
          month: activeMonth,
          category: transactionFilters.category,
          search: transactionFilters.search,
          limit: 50,
        })}`),
        request(`/anomalies${queryString({ month: activeMonth, limit: 6 })}`),
        request(`/categories${queryString({ month: activeMonth })}`),
        request("/trends?limit=12"),
        request(`/merchants${queryString({ month: activeMonth, limit: 6 })}`),
        request(`/expenses/largest${queryString({ month: activeMonth, limit: 6 })}`),
        request("/category-options"),
        request(`/categories/review${queryString({ month: activeMonth, limit: 6 })}`),
        request("/merchant-rules"),
        request(`/budgets${queryString({ month: activeMonth })}`),
        request(`/budgets/recommendations${queryString({ month: activeMonth, limit: 6 })}`),
        request("/recurring?limit=6"),
        request("/uploads?limit=6"),
        request("/ask/history?limit=5"),
      ]);

      setMonths(monthsPayload);
      setSummary(summaryPayload);
      setInsights(insightPayload);
      setForecast(forecastPayload);
      setTransactions(transactionPayload);
      setAnomalies(anomalyPayload);
      setCategories(categoryPayload);
      setTrends(trendPayload);
      setMerchants(merchantPayload);
      setLargestExpenses(largestPayload);
      setCategoryOptions(categoryOptionsPayload);
      setCategoryReview(categoryReviewPayload);
      setMerchantRules(merchantRulesPayload);
      setBudgets(budgetPayload);
      setBudgetRecommendations(budgetRecommendationPayload);
      setRecurringCharges(recurringPayload);
      setUploads(uploadPayload);
      setAskHistory(askHistoryPayload);
      setLastUpdated(new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
    } catch (error) {
      setHealth("Offline");
      setUploadStatus(error.message);
    }
  }, [month, transactionFilters]);

  useEffect(() => {
    refreshDashboard();
  }, [refreshDashboard]);

  async function handleUpload(event) {
    event.preventDefault();
    const file = event.currentTarget.elements.statement.files[0];
    if (!file) {
      setUploadStatus("Choose a statement file first.");
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
      setUploadPreview(null);
      event.currentTarget.reset();
      await refreshDashboard();
    } catch (error) {
      setUploadStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function handlePreviewUpload(event) {
    const file = event.currentTarget.form.elements.statement.files[0];
    if (!file) {
      setUploadStatus("Choose a statement file first.");
      return;
    }

    setBusy(true);
    setUploadStatus("Previewing statement...");
    const formData = new FormData();
    formData.append("file", file);

    try {
      const preview = await request("/transactions/preview", {
        method: "POST",
        body: formData,
      });
      setUploadPreview(preview);
      setUploadStatus(`Previewed ${preview.row_count} rows from ${preview.filename}.`);
    } catch (error) {
      setUploadPreview(null);
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
      setAskHistory(await request("/ask/history?limit=5"));
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
      setUploadPreview(null);
      await refreshDashboard();
    } catch (error) {
      setUploadStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleResetLocalData(event) {
    event.preventDefault();
    if (resetConfirmation !== "RESET") {
      setUploadStatus("Type RESET first.");
      return;
    }
    if (!window.confirm("Reset all local finance data? This deletes transactions, upload history, budgets, merchant rules, and Q&A history.")) return;

    setBusy(true);
    try {
      await request(`/data${queryString({ confirmation: resetConfirmation })}`, { method: "DELETE" });
      setUploadStatus("All local finance data cleared.");
      setAnswer(null);
      setMonth("");
      setUploadPreview(null);
      setAskHistory([]);
      setBudgetDraft({ category: "", amount: "" });
      setTransactionFilters({ category: "", search: "" });
      setResetConfirmation("");
      await refreshDashboard();
    } catch (error) {
      setUploadStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleCategoryChange(transaction, category, remember = false) {
    if (!remember && transaction.category === category) return;

    setUpdatingTransactionId(transaction.id);
    try {
      await request(`/transactions/${transaction.id}/category`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category, remember }),
      });
      setUploadStatus(remember
        ? `Saved ${category} rule for ${transaction.description}.`
        : `Updated ${transaction.description} to ${category}.`);
      await refreshDashboard();
    } catch (error) {
      setUploadStatus(error.message);
    } finally {
      setUpdatingTransactionId(null);
    }
  }

  async function handleDeleteRule(rule) {
    setBusy(true);
    try {
      await request(`/merchant-rules/${rule.id}`, { method: "DELETE" });
      setUploadStatus(`Removed rule for ${rule.merchant}.`);
      await refreshDashboard();
    } catch (error) {
      setUploadStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleBudgetSubmit(event) {
    event.preventDefault();
    const category = budgetDraft.category || categoryOptions[0] || "";
    const amount = Number(budgetDraft.amount);
    if (!month) {
      setUploadStatus("Choose a month before saving a budget.");
      return;
    }
    if (!category || !amount || amount <= 0) {
      setUploadStatus("Enter a budget amount greater than zero.");
      return;
    }

    setBusy(true);
    try {
      const budget = await request("/budgets", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ month, category, amount }),
      });
      setBudgetDraft({ category: budget.category, amount: "" });
      setUploadStatus(`Saved ${month} ${budget.category} budget.`);
      await refreshDashboard();
    } catch (error) {
      setUploadStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteBudget(budget) {
    setBusy(true);
    try {
      await request(`/budgets/${budget.id}`, { method: "DELETE" });
      setUploadStatus(`Removed ${budget.month} ${budget.category} budget.`);
      await refreshDashboard();
    } catch (error) {
      setUploadStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleApplyBudgetRecommendation(recommendation) {
    const recommendationMonth = recommendation.month || month;
    if (!recommendationMonth) {
      setUploadStatus("Choose a month before saving a recommendation.");
      return;
    }

    setBusy(true);
    try {
      const budget = await request("/budgets", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          month: recommendationMonth,
          category: recommendation.category,
          amount: recommendation.recommended_amount,
        }),
      });
      setUploadStatus(`Saved ${budget.month} ${budget.category} budget.`);
      await refreshDashboard();
    } catch (error) {
      setUploadStatus(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleApplyCategorySuggestion(item) {
    await handleCategoryChange(item.transaction, item.suggested_category, true);
  }

  function handleTransactionFilterChange(name, value) {
    setTransactionFilters((current) => ({ ...current, [name]: value }));
  }

  function handleClearTransactionFilters() {
    setTransactionFilters({ category: "", search: "" });
  }

  function handleExportTransactions() {
    const params = queryString({
      month,
      category: transactionFilters.category,
      search: transactionFilters.search,
      limit: 5000,
    });
    window.location.assign(`${API_BASE}/transactions/export${params}`);
  }

  function handleExportBackup() {
    window.location.assign(`${API_BASE}/data/export`);
  }

  const visibleTransactions = useMemo(() => transactions.slice(0, 20), [transactions]);
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

        <section className="panel insights-panel">
          <PanelTitle icon={<ClipboardList size={18} />} title="Monthly Insights" detail={insights.month || selectedMonthLabel} />
          <InsightList insights={insights} />
        </section>

        <section className="panel forecast-panel">
          <PanelTitle icon={<CalendarClock size={18} />} title="Cash Flow Forecast" detail={forecast.month || selectedMonthLabel} />
          <ForecastSummary forecast={forecast} />
        </section>

        <section className="panel budget-panel">
          <PanelTitle icon={<Target size={18} />} title="Budgets" detail={month || "No month"} />
          <BudgetForm
            categories={categoryOptions}
            disabled={busy || !month || !categoryOptions.length}
            draft={budgetDraft}
            onDraftChange={setBudgetDraft}
            onSubmit={handleBudgetSubmit}
          />
          <BudgetList budgets={budgets} busy={busy} onDelete={handleDeleteBudget} />
        </section>

        <section className="panel recommendations-panel">
          <PanelTitle icon={<Lightbulb size={18} />} title="Budget Recommendations" detail={month || "No month"} />
          <BudgetRecommendationList
            busy={busy}
            onApply={handleApplyBudgetRecommendation}
            recommendations={budgetRecommendations}
          />
        </section>

        <section className="panel category-review-panel">
          <PanelTitle icon={<Tags size={18} />} title="Category Review" detail={`${categoryReview.length} queued`} />
          <CategoryReviewList
            busy={busy || updatingTransactionId !== null}
            items={categoryReview}
            onApply={handleApplyCategorySuggestion}
          />
        </section>

        <section className="panel recurring-panel">
          <PanelTitle icon={<Repeat2 size={18} />} title="Recurring Charges" detail={`${recurringCharges.length} detected`} />
          <RecurringList charges={recurringCharges} />
        </section>

        <section className="panel action-panel">
          <PanelTitle icon={<FileUp size={18} />} title="Import Statement" detail="CSV/PDF" />
          <form className="upload-form" onSubmit={handleUpload}>
            <input name="statement" type="file" accept=".csv,.pdf,text/csv,application/pdf" onChange={() => setUploadPreview(null)} />
            <div className="button-row">
              <button className="ghost-button" type="button" disabled={busy} onClick={handlePreviewUpload}>
                <Eye size={16} />
                Preview
              </button>
              <button type="submit" disabled={busy}>Import</button>
              <button className="ghost-button" type="button" disabled={busy} onClick={handleExportBackup}>
                <Download size={16} />
                Backup
              </button>
              <button className="ghost-button danger-button" type="button" disabled={busy || !transactions.length} onClick={handleClear}>
                <Trash2 size={16} />
                Clear Txns
              </button>
            </div>
          </form>
          {uploadStatus && <p className="helper-text">{uploadStatus}</p>}
          {uploadPreview && <ImportPreview preview={uploadPreview} />}
        </section>

        <section className="panel uploads-panel">
          <PanelTitle icon={<History size={18} />} title="Import History" detail={`${uploads.length} recent`} />
          <UploadHistoryList uploads={uploads} />
        </section>

        <section className="panel privacy-panel">
          <PanelTitle icon={<Shield size={18} />} title="Privacy" detail="Local data" />
          <form className="privacy-form" onSubmit={handleResetLocalData}>
            <input
              aria-label="Reset confirmation"
              autoComplete="off"
              onChange={(event) => setResetConfirmation(event.target.value)}
              placeholder="RESET"
              value={resetConfirmation}
            />
            <button className="danger-action" type="submit" disabled={busy || !hasLocalData || resetConfirmation !== "RESET"}>
              <Trash2 size={16} />
              Reset Data
            </button>
          </form>
        </section>

        <section className="panel ask-panel">
          <PanelTitle icon={<MessageSquare size={18} />} title="Ask About Spending" detail={selectedMonthLabel} />
          <form className="ask-form" onSubmit={handleAsk}>
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} />
            <button type="submit" disabled={busy}>Ask</button>
          </form>
          {answer && <AnswerCard answer={answer} />}
          <AskHistoryList history={askHistory} onSelect={(item) => setQuestion(item.question)} />
        </section>

        <section className="panel">
          <PanelTitle icon={<Store size={18} />} title="Top Merchants" detail={selectedMonthLabel} />
          <MoneyList items={merchants} emptyText="No merchant spend yet." getTitle={(item) => item.merchant} getSubtitle={(item) => item.category} />
        </section>

        <section className="panel rules-panel">
          <PanelTitle icon={<BookmarkPlus size={18} />} title="Merchant Rules" detail={`${merchantRules.length} saved`} />
          <RuleList rules={merchantRules} busy={busy} onDelete={handleDeleteRule} />
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
        <PanelTitle
          icon={<CircleDollarSign size={18} />}
          title="Transactions"
          detail={`${visibleTransactions.length}${transactions.length > visibleTransactions.length ? ` of ${transactions.length}` : ""} shown`}
        />
        <TransactionFilters
          categoryOptions={categoryOptions}
          filters={transactionFilters}
          onChange={handleTransactionFilterChange}
          onClear={handleClearTransactionFilters}
          onExport={handleExportTransactions}
          total={transactions.length}
        />
        <div className="transaction-table">
          <div className="table-heading">Date</div>
          <div className="table-heading">Description</div>
          <div className="table-heading">Category</div>
          <div className="table-heading align-right">Amount</div>
          {visibleTransactions.map((transaction) => (
            <TransactionRow
              categoryOptions={categoryOptions}
              key={transaction.id}
              onCategoryChange={handleCategoryChange}
              transaction={transaction}
              updating={updatingTransactionId === transaction.id}
            />
          ))}
        </div>
        {!visibleTransactions.length && <p className="empty">No matching transactions.</p>}
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

function InsightList({ insights }) {
  const rows = [
    ...(insights.highlights || []).map((text) => ({ tone: "highlight", text })),
    ...(insights.risks || []).map((text) => ({ tone: "risk", text })),
    ...(insights.next_actions || []).map((text) => ({ tone: "action", text })),
  ].slice(0, 7);

  if (!rows.length) return <p className="empty">No monthly insights yet.</p>;

  return (
    <div className="insight-list">
      {rows.map((row, index) => (
        <div className={`insight-row insight-${row.tone}`} key={`${row.tone}-${index}`}>
          <span aria-hidden="true" />
          <p>{row.text}</p>
        </div>
      ))}
    </div>
  );
}

function ForecastSummary({ forecast }) {
  if (!forecast.month) return <p className="empty">No forecast yet.</p>;

  return (
    <div className="forecast-summary">
      <div className="forecast-main">
        <span>Projected Spending</span>
        <strong>{money(forecast.projected_spending)}</strong>
      </div>
      <div className="forecast-stats">
        <span>{forecast.confidence} confidence</span>
        <span>{forecast.remaining_days} days remaining</span>
        <span>{forecast.status.replaceAll("_", " ")}</span>
      </div>
      <div className="forecast-notes">
        {(forecast.notes || []).slice(0, 4).map((note) => (
          <p key={note}>{note}</p>
        ))}
      </div>
    </div>
  );
}

function BudgetForm({ categories, disabled, draft, onDraftChange, onSubmit }) {
  const selectedCategory = draft.category || categories[0] || "";

  return (
    <form className="budget-form" onSubmit={onSubmit}>
      <select
        aria-label="Budget category"
        disabled={disabled}
        onChange={(event) => onDraftChange({ ...draft, category: event.target.value })}
        value={selectedCategory}
      >
        {!selectedCategory && <option value="">Category</option>}
        {categories.map((category) => (
          <option key={category} value={category}>{category}</option>
        ))}
      </select>
      <input
        aria-label="Budget amount"
        disabled={disabled}
        min="0.01"
        onChange={(event) => onDraftChange({ ...draft, amount: event.target.value })}
        placeholder="Amount"
        step="0.01"
        type="number"
        value={draft.amount}
      />
      <button type="submit" disabled={disabled || !selectedCategory || !draft.amount}>
        <Plus size={16} />
        Save
      </button>
    </form>
  );
}

function BudgetList({ budgets, busy, onDelete }) {
  if (!budgets.length) return <p className="empty">No budgets for this month.</p>;

  return (
    <div className="budget-list">
      {budgets.map((budget) => (
        <BudgetRow budget={budget} busy={busy} key={budget.id} onDelete={onDelete} />
      ))}
    </div>
  );
}

function BudgetRecommendationList({ busy, onApply, recommendations }) {
  if (!recommendations.length) return <p className="empty">No budget recommendations yet.</p>;

  return (
    <div className="recommendation-list">
      {recommendations.map((recommendation) => (
        <div className={`recommendation-row recommendation-${recommendation.action}`} key={recommendation.category}>
          <div>
            <strong>{recommendation.category}</strong>
            <span>{recommendation.reason}</span>
            <small>
              {recommendation.confidence} confidence | {recommendation.history_months} month
              {recommendation.history_months === 1 ? "" : "s"} history
            </small>
          </div>
          <div className="recommendation-action">
            <b>{money(recommendation.recommended_amount)}</b>
            <button disabled={busy} onClick={() => onApply(recommendation)} type="button">
              <Plus size={15} />
              Use
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function CategoryReviewList({ busy, items, onApply }) {
  if (!items.length) return <p className="empty">No category review items.</p>;

  return (
    <div className="category-review-list">
      {items.map((item) => (
        <div className={`category-review-row category-review-${item.action}`} key={item.transaction.id}>
          <div>
            <strong>{item.transaction.description}</strong>
            <span>{item.current_category} to {item.suggested_category} | {Math.round(item.confidence * 100)}% confidence</span>
            <small>{item.reason}</small>
          </div>
          <div className="category-review-action">
            <b>{money(Math.abs(item.transaction.amount))}</b>
            {item.action === "update" ? (
              <button disabled={busy} onClick={() => onApply(item)} type="button">
                <Check size={15} />
                Apply
              </button>
            ) : (
              <span>Review</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function BudgetRow({ budget, busy, onDelete }) {
  const used = Math.max(0, Math.min(Number(budget.percent_used) || 0, 100));
  const remainingLabel = budget.remaining >= 0
    ? `${money(budget.remaining)} left`
    : `${money(Math.abs(budget.remaining))} over`;

  return (
    <div className={`budget-row budget-${budget.status}`}>
      <div className="budget-main">
        <div className="budget-topline">
          <strong>{budget.category}</strong>
          <span>{money(budget.spent)} / {money(budget.amount)}</span>
        </div>
        <div className="budget-meter" aria-label={`${budget.percent_used}% used`}>
          <span style={{ width: `${used}%` }} />
        </div>
        <small>{remainingLabel} | {budget.percent_used}% used</small>
      </div>
      <button
        aria-label={`Delete ${budget.category} budget`}
        className="row-icon-button"
        disabled={busy}
        onClick={() => onDelete(budget)}
        title="Delete budget"
        type="button"
      >
        <Trash2 size={15} />
      </button>
    </div>
  );
}

function RecurringList({ charges }) {
  if (!charges.length) return <p className="empty">No recurring charges detected yet.</p>;

  return (
    <div className="list">
      {charges.map((charge) => (
        <div className="list-row" key={`${charge.merchant}-${charge.first_seen}`}>
          <div>
            <strong>{charge.merchant}</strong>
            <span>
              {charge.cadence} | {charge.occurrences} charges | next {charge.next_expected_date}
            </span>
          </div>
          <b>{money(charge.average_amount)}</b>
        </div>
      ))}
    </div>
  );
}

function UploadHistoryList({ uploads }) {
  if (!uploads.length) return <p className="empty">No statement uploads yet.</p>;

  return (
    <div className="list">
      {uploads.map((upload) => (
        <div className="list-row" key={upload.id}>
          <div>
            <strong>{upload.filename}</strong>
            <span>
              {upload.file_type.toUpperCase()} | {dateRange(upload)} | {upload.duplicates_skipped} skipped
            </span>
          </div>
          <b>{upload.imported_count}</b>
        </div>
      ))}
    </div>
  );
}

function ImportPreview({ preview }) {
  const errors = preview.errors || [];

  return (
    <div className="import-preview">
      <div className="preview-metrics">
        <span>{preview.importable_count} importable</span>
        <span>{preview.duplicate_count} duplicates</span>
        <span>{money(preview.total_spending)} spending</span>
      </div>
      {!!errors.length && (
        <div className="preview-errors">
          {errors.slice(0, 4).map((error) => (
            <span key={error}>
              <AlertTriangle size={14} />
              {error}
            </span>
          ))}
        </div>
      )}
      <div className="preview-rows">
        {preview.rows.slice(0, 5).map((row, index) => (
          <div className={`preview-row ${row.duplicate ? "preview-duplicate" : ""}`} key={`${row.date}-${row.description}-${index}`}>
            <div>
              <strong>{row.description}</strong>
              <span>{row.date} | {row.category}</span>
            </div>
            <b>{money(row.amount)}</b>
          </div>
        ))}
      </div>
    </div>
  );
}

function RuleList({ rules, busy, onDelete }) {
  if (!rules.length) return <p className="empty">No saved merchant rules.</p>;

  return (
    <div className="list">
      {rules.map((rule) => (
        <div className="list-row rule-row" key={rule.id}>
          <div>
            <strong>{rule.merchant}</strong>
            <span>{rule.category}</span>
          </div>
          <button
            aria-label={`Delete rule for ${rule.merchant}`}
            className="row-icon-button"
            disabled={busy}
            onClick={() => onDelete(rule)}
            title="Delete rule"
            type="button"
          >
            <Trash2 size={15} />
          </button>
        </div>
      ))}
    </div>
  );
}

function AnswerCard({ answer }) {
  const citations = answer.citations || [];

  return (
    <div className="answer">
      <strong>{answer.answer}</strong>
      {!!answer.categories?.length && <span>{answer.categories.join(" + ")}</span>}
      {!!citations.length && (
        <div className="citation-list">
          {citations.map((citation) => (
            <div className="citation-row" key={citation.id}>
              <div>
                <b>{citation.title}</b>
                <span>{citation.detail}</span>
              </div>
              {citation.amount !== null && citation.amount !== undefined && <strong>{money(citation.amount)}</strong>}
            </div>
          ))}
        </div>
      )}
      {!!answer.data?.length && <small>{answer.data.length} supporting result{answer.data.length === 1 ? "" : "s"}</small>}
    </div>
  );
}

function AskHistoryList({ history, onSelect }) {
  if (!history.length) {
    return <p className="empty">No recent questions.</p>;
  }

  return (
    <div className="ask-history" aria-label="Recent questions">
      {history.map((item) => (
        <button className="history-question" key={item.id} onClick={() => onSelect(item)} type="button">
          <span>{item.question}</span>
          <small>{formatHistoryMeta(item)}</small>
          <em>{item.answer}</em>
        </button>
      ))}
    </div>
  );
}

function formatHistoryMeta(item) {
  const intent = item.intent.replaceAll("_", " ");
  return item.month ? `${intent} - ${item.month}` : intent;
}

function TransactionFilters({ categoryOptions, filters, onChange, onClear, onExport, total }) {
  return (
    <div className="transaction-toolbar">
      <label className="transaction-filter">
        <Search size={16} />
        <input
          aria-label="Search transactions"
          onChange={(event) => onChange("search", event.target.value)}
          placeholder="Search merchant"
          value={filters.search}
        />
      </label>
      <select
        aria-label="Filter transactions by category"
        onChange={(event) => onChange("category", event.target.value)}
        value={filters.category}
      >
        <option value="">All categories</option>
        {categoryOptions.map((category) => (
          <option key={category} value={category}>{category}</option>
        ))}
      </select>
      <button
        aria-label="Clear transaction filters"
        className="ghost-button filter-reset"
        onClick={onClear}
        title="Clear filters"
        type="button"
      >
        <X size={16} />
      </button>
      <button className="ghost-button export-button" disabled={!total} onClick={onExport} type="button">
        <Download size={16} />
        Export CSV
      </button>
    </div>
  );
}

function TransactionRow({ categoryOptions, onCategoryChange, transaction, updating }) {
  return (
    <>
      <div>{transaction.date}</div>
      <div>{transaction.description}</div>
      <div>
        <CategoryEditor
          options={categoryOptions}
          onCategoryChange={onCategoryChange}
          transaction={transaction}
          updating={updating}
        />
      </div>
      <div className={`align-right ${transaction.amount < 0 ? "negative" : "positive"}`}>
        {money(transaction.amount)}
      </div>
    </>
  );
}

function CategoryEditor({ options, onCategoryChange, transaction, updating }) {
  if (!options.length) {
    return <span className="category-pill">{transaction.category}</span>;
  }

  return (
    <div className="category-editor">
      <select
        aria-label={`Category for ${transaction.description}`}
        className="category-select"
        disabled={updating}
        onChange={(event) => onCategoryChange(transaction, event.target.value)}
        value={transaction.category}
      >
        {options.map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
      <button
        aria-label={`Save merchant rule for ${transaction.description}`}
        className="row-icon-button"
        disabled={updating}
        onClick={() => onCategoryChange(transaction, transaction.category, true)}
        title="Save merchant rule"
        type="button"
      >
        <BookmarkPlus size={15} />
      </button>
    </div>
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

function emptyInsights() {
  return {
    month: null,
    summary: emptySummary(),
    spending_delta: null,
    spending_delta_percent: null,
    top_category: null,
    top_merchant: null,
    largest_expense: null,
    over_budget_count: 0,
    near_budget_count: 0,
    recurring_count: 0,
    anomaly_count: 0,
    highlights: [],
    risks: [],
    next_actions: [],
  };
}

function emptyForecast() {
  return {
    month: null,
    status: "no_data",
    confidence: "low",
    coverage_start_date: null,
    coverage_end_date: null,
    days_elapsed: 0,
    days_in_month: 0,
    remaining_days: 0,
    actual_spending: 0,
    daily_spending_average: 0,
    run_rate_projection: 0,
    projected_spending: 0,
    projected_income: 0,
    projected_net: 0,
    budget_total: 0,
    budget_remaining: 0,
    budget_status: "no_budget",
    upcoming_recurring_total: 0,
    upcoming_recurring: [],
    notes: [],
  };
}

function money(value) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(value) || 0);
}

function dateRange(upload) {
  if (!upload.first_transaction_date || !upload.last_transaction_date) {
    return "No dates";
  }
  if (upload.first_transaction_date === upload.last_transaction_date) {
    return upload.first_transaction_date;
  }
  return `${upload.first_transaction_date} to ${upload.last_transaction_date}`;
}
