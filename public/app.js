const reportDefinitions = [
  {
    id: "all",
    label: "All Products",
    title: "All calculated products",
    description: "The main Data sheet translated into a searchable product table.",
    getRows: (items) => [...items],
    columns: ["product", "specs", "sku", "segment", "sales7", "sales30", "sales90", "stockNow", "weeksUntilOut", "grossMargin", "netMargin"],
  },
  {
    id: "best-week",
    label: "Best Sellers 7d",
    title: "Best sellers over the last 7 days",
    description: "Top 50 selling SKUs by units sold in the last 7 days.",
    getRows: (items) => topRows(items.filter((row) => row.sales7 > 0), "sales7", 50),
    columns: ["product", "specs", "sku", "sales7", "grossMargin", "netMargin", "stockNow", "weeksUntilOut"],
  },
  {
    id: "best-month",
    label: "Best Sellers 30d",
    title: "Best sellers over the last 30 days",
    description: "Top 50 selling SKUs by units sold in the last 30 days.",
    getRows: (items) => topRows(items.filter((row) => row.sales30 > 0), "sales30", 50),
    columns: ["product", "specs", "sku", "sales30", "grossMargin", "netMargin", "stockNow", "weeksUntilOut"],
  },
  {
    id: "best-quarter",
    label: "Best Sellers 90d",
    title: "Best sellers over the last 90 days",
    description: "Top 75 selling SKUs by units sold in the last 90 days.",
    getRows: (items) => topRows(items.filter((row) => row.sales90 > 0), "sales90", 75),
    columns: ["product", "specs", "sku", "sales90", "grossMargin", "netMargin", "stockNow", "weeksUntilOut"],
  },
  {
    id: "high-margin",
    label: "High Margin",
    title: "High margin sellers",
    description: "Top 75 SKUs by net margin, with stock and recent sales context.",
    getRows: (items) => topRows(items.filter((row) => isNumber(row.netMargin)), "netMargin", 75),
    columns: ["product", "specs", "costPrice", "sku", "averageSalesPrice", "sales7", "sales30", "sales90", "grossMargin", "netMargin", "stockNow", "weeksUntilOut"],
  },
  {
    id: "no-sales",
    label: "In Stock No Sales",
    title: "In stock with no recent sales",
    description: "Items with stock on hand and no units sold over the last 90 days.",
    getRows: (items) => items.filter((row) => row.stockNow > 0 && row.sales90 === 0).sort((a, b) => b.stockNow - a.stockNow),
    columns: ["product", "specs", "sku", "sales90", "stockNow"],
  },
  {
    id: "almost-out",
    label: "Almost Out",
    title: "Almost out of stock",
    description: "Selling items with less than 12 estimated weeks of stock remaining.",
    getRows: (items) => items.filter((row) => row.weeksUntilOut < 12 && row.weeksUntilOut > 0.49).sort((a, b) => a.weeksUntilOut - b.weeksUntilOut),
    columns: ["product", "specs", "sku", "sales7", "sales30", "sales90", "stockNow", "weeksUntilOut"],
  },
  {
    id: "sold-out",
    label: "Sold Now Out",
    title: "Sold and now out of stock",
    description: "Items with sales in the last 90 days where current stock is now zero.",
    getRows: (items) => items.filter((row) => row.sales90 > 0 && row.stockNow <= 0).sort((a, b) => b.sales90 - a.sales90),
    columns: ["product", "specs", "sku", "sales7", "sales30", "sales90", "stockNow"],
  },
];

const columnLabels = {
  product: "Product",
  specs: "Product Specs",
  segment: "Womens / Kids",
  costPrice: "Cost Price",
  sku: "SKU",
  stock30DaysAgo: "Stock 30 Days Ago",
  averageSalesPrice: "Average Sales Price",
  sales7: "Unit Sales 7d",
  sales30: "Unit Sales 30d",
  sales90: "Unit Sales 90d",
  grossMargin: "Gross Margin",
  netMargin: "Net Margin",
  stockNow: "Stock Now",
  weeksUntilOut: "Weeks Until Out",
};

const state = {
  data: null,
  reportId: "all",
  sortKey: "sales30",
  sortDirection: "desc",
};

const els = {
  nav: document.querySelector("#reportNav"),
  title: document.querySelector("#pageTitle"),
  kpis: document.querySelector("#kpis"),
  sectionTitle: document.querySelector("#sectionTitle"),
  sectionMeta: document.querySelector("#sectionMeta"),
  tableHead: document.querySelector("#tableHead"),
  tableBody: document.querySelector("#tableBody"),
  search: document.querySelector("#searchInput"),
  segment: document.querySelector("#segmentFilter"),
  stock: document.querySelector("#stockFilter"),
  clearStock: document.querySelector("#clearStockFilter"),
  export: document.querySelector("#exportBtn"),
  sync: document.querySelector("#syncBtn"),
  syncStatus: document.querySelector("#syncStatus"),
};

fetch("./data.json")
  .then((response) => response.json())
  .then((data) => {
    state.data = data;
    renderNav();
    render();
  });

els.search.addEventListener("input", render);
els.segment.addEventListener("change", render);
els.stock.addEventListener("change", render);
els.clearStock.addEventListener("click", () => {
  els.stock.value = "";
  render();
});
els.export.addEventListener("click", exportCsv);
els.sync.addEventListener("click", runSync);
loadSyncStatus();
setInterval(loadSyncStatus, 60000);

function renderNav() {
  els.nav.innerHTML = reportDefinitions.map((report) => `
    <button type="button" data-report="${report.id}" class="${report.id === state.reportId ? "active" : ""}">
      ${report.label}
    </button>
  `).join("");

  els.nav.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      navigateToReport(button.dataset.report);
    });
  });
}

function render() {
  const report = currentReport();
  const baseRows = getFilteredRows(report, { includeStockFilter: false });
  const rows = applyStockFilter(baseRows);
  els.title.textContent = report.title;
  els.sectionTitle.textContent = report.label;
  els.sectionMeta.textContent = `${rows.length.toLocaleString()} rows shown from ${state.data.counts.dataItems.toLocaleString()} calculated SKUs`;
  renderActiveFilter();
  renderKpis(report, rows, baseRows);
  renderTable(report, rows);
}

function renderActiveFilter() {
  const labels = { in: "In stock", low: "Low stock", out: "Out of stock" };
  if (!els.stock.value) {
    els.clearStock.hidden = true;
    els.clearStock.textContent = "";
    return;
  }

  els.clearStock.hidden = false;
  els.clearStock.textContent = `${labels[els.stock.value]} filter x`;
}

function renderKpis(report, rows, baseRows) {
  const cards = getKpiCards(report, rows, baseRows);
  els.kpis.innerHTML = cards.map((card) => {
    if (card.kind === "risk") {
      return `
        <article class="kpi risk-card">
          <span>${card.label}</span>
          <div class="risk-actions" aria-label="Stock risk filters">
            <button type="button" class="${els.stock.value === "low" ? "active" : ""}" data-stock-filter="low">
              <strong>${card.low}</strong>
              <small>low</small>
            </button>
            <button type="button" class="${els.stock.value === "out" ? "active" : ""}" data-stock-filter="out">
              <strong>${card.out}</strong>
              <small>out</small>
            </button>
          </div>
          <small>${card.note}</small>
        </article>
      `;
    }

    const tag = card.target ? "button" : "article";
    const target = card.target ? ` data-target="${card.target}" type="button"` : "";
    return `
      <${tag} class="kpi ${card.target ? "clickable" : ""}"${target}>
        <span>${card.label}</span>
        <strong>${card.value}</strong>
        <small>${card.note}</small>
      </${tag}>
    `;
  }).join("");

  els.kpis.querySelectorAll("[data-target]").forEach((card) => {
    card.addEventListener("click", () => navigateToReport(card.dataset.target));
  });

  els.kpis.querySelectorAll("[data-stock-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      els.stock.value = els.stock.value === button.dataset.stockFilter ? "" : button.dataset.stockFilter;
      render();
    });
  });
}

function getKpiCards(report, rows, baseRows = rows) {
  const totalStock = sum(rows, "stockNow");
  const sold7 = sum(rows, "sales7");
  const sold30 = sum(rows, "sales30");
  const sold90 = sum(rows, "sales90");
  const outOfStock = baseRows.filter((row) => row.stockNow <= 0).length;
  const lowStock = baseRows.filter((row) => row.weeksUntilOut > 0 && row.weeksUntilOut < 12).length;
  const avgNet = average(rows.map((row) => row.netMargin).filter(isNumber));

  const metrics = {
    rows: { label: "Rows", value: rows.length.toLocaleString(), note: "Current filtered result", target: "all" },
    stock: { label: "Stock Units", value: formatNumber(totalStock), note: "On hand now", target: "all" },
    sales7: { label: "Sales 7d", value: formatNumber(sold7), note: "Go to 7-day best sellers", target: "best-week" },
    sales30: { label: "Sales 30d", value: formatNumber(sold30), note: "Go to 30-day best sellers", target: "best-month" },
    sales90: { label: "Sales 90d", value: formatNumber(sold90), note: "Go to 90-day best sellers", target: "best-quarter" },
    risk: { kind: "risk", label: "Stock Risk", low: lowStock.toLocaleString(), out: outOfStock.toLocaleString(), note: "Click a count to filter this table" },
    margin: { label: "Avg Net Margin", value: formatPercent(avgNet), note: "Go to high margin sellers", target: "high-margin" },
  };

  if (report.id === "all") {
    return [metrics.rows, metrics.stock, metrics.sales7, metrics.sales30, metrics.sales90, metrics.margin];
  }

  if (report.id.startsWith("best-") || report.id === "high-margin") {
    return [withoutTarget(metrics.risk), withoutTarget(metrics.margin)];
  }

  if (report.id === "no-sales") {
    return [withoutTarget(metrics.rows), withoutTarget(metrics.stock)];
  }

  if (report.id === "almost-out") {
    return [
      withoutTarget(metrics.rows),
      { label: "Sold 90d", value: formatNumber(sold90), note: "Units sold in the last 90 days" },
    ];
  }

  if (report.id === "sold-out") {
    return [withoutTarget(metrics.rows)];
  }

  return [withoutTarget(metrics.rows)];
}

function withoutTarget(card) {
  return { ...card, target: "" };
}

function navigateToReport(reportId) {
  state.reportId = reportId;
  state.sortKey = defaultSortKey(currentReport());
  state.sortDirection = "desc";
  els.stock.value = "";
  renderNav();
  render();
}

function renderTable(report, rows) {
  const columns = report.columns;
  els.tableHead.innerHTML = `<tr>${columns.map((key) => `
    <th data-key="${key}">${columnLabels[key] || key}${state.sortKey === key ? (state.sortDirection === "asc" ? " ↑" : " ↓") : ""}</th>
  `).join("")}</tr>`;

  els.tableHead.querySelectorAll("th").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (state.sortKey === key) state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
      else {
        state.sortKey = key;
        state.sortDirection = "desc";
      }
      render();
    });
  });

  const sorted = sortRows(rows, state.sortKey, state.sortDirection).slice(0, 500);
  els.tableBody.innerHTML = sorted.map((row) => `
    <tr>${columns.map((key) => `<td class="${cellClass(key)}">${formatCell(row, key)}</td>`).join("")}</tr>
  `).join("");
}

function getFilteredRows(report, options = {}) {
  const { includeStockFilter = true } = options;
  const query = els.search.value.trim().toLowerCase();
  const segment = els.segment.value;
  const rows = report.getRows(state.data.items).filter((row) => {
    const matchesQuery = !query || [row.product, row.specs, row.sku, row.segment].join(" ").toLowerCase().includes(query);
    const matchesSegment = !segment || row.segment === segment;
    return matchesQuery && matchesSegment;
  });
  return includeStockFilter ? applyStockFilter(rows) : rows;
}

function applyStockFilter(rows) {
  const stock = els.stock.value;
  if (!stock) return rows;
  return rows.filter((row) =>
    (stock === "in" && row.stockNow > 0) ||
    (stock === "out" && row.stockNow <= 0) ||
    (stock === "low" && row.weeksUntilOut > 0 && row.weeksUntilOut < 12)
  );
}

function currentReport() {
  return reportDefinitions.find((report) => report.id === state.reportId) || reportDefinitions[0];
}

function defaultSortKey(report) {
  if (report.columns.includes("sales30")) return "sales30";
  if (report.columns.includes("netMargin")) return "netMargin";
  if (report.columns.includes("weeksUntilOut")) return "weeksUntilOut";
  return report.columns[0];
}

function topRows(rows, key, limit) {
  const sorted = [...rows].sort((a, b) => safeNumber(b[key]) - safeNumber(a[key]));
  return sorted.slice(0, limit);
}

function sortRows(rows, key, direction) {
  const multiplier = direction === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (isNumber(av) || isNumber(bv)) return (safeNumber(av) - safeNumber(bv)) * multiplier;
    return String(av || "").localeCompare(String(bv || "")) * multiplier;
  });
}

function formatCell(row, key) {
  const value = row[key];
  if (key === "sku") return `<span class="sku">${escapeHtml(value)}</span>`;
  if (key === "segment") return value ? `<span class="pill">${escapeHtml(value)}</span>` : "";
  if (key === "grossMargin" || key === "netMargin") return formatPercent(value);
  if (key === "costPrice" || key === "averageSalesPrice") return isNumber(value) ? `£${Number(value).toFixed(2)}` : "";
  if (key === "weeksUntilOut") {
    if (!isNumber(value)) return "";
    const klass = value < 1 ? "risk" : value < 12 ? "warn" : "";
    return `<span class="pill ${klass}">${Number(value).toFixed(1)}</span>`;
  }
  if (isNumber(value)) return formatNumber(value);
  return escapeHtml(value || "");
}

function cellClass(key) {
  return ["costPrice", "averageSalesPrice", "sales7", "sales30", "sales90", "grossMargin", "netMargin", "stockNow", "weeksUntilOut"].includes(key) ? "numeric" : "";
}

function exportCsv() {
  const report = currentReport();
  const rows = getFilteredRows(report);
  const columns = report.columns;
  const csv = [
    columns.map((key) => columnLabels[key] || key),
    ...sortRows(rows, state.sortKey, state.sortDirection).map((row) => columns.map((key) => row[key] ?? "")),
  ].map((line) => line.map(csvEscape).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${report.id}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function csvEscape(value) {
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function sum(rows, key) {
  return rows.reduce((total, row) => total + safeNumber(row[key]), 0);
}

function average(values) {
  return values.length ? values.reduce((total, value) => total + value, 0) / values.length : null;
}

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function safeNumber(value) {
  return isNumber(value) ? value : 0;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function formatPercent(value) {
  return isNumber(value) ? `${(value * 100).toFixed(1)}%` : "";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function formatDateTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function loadSyncStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) return;
    const status = await response.json();
    renderSyncStatus(status);
  } catch {
    els.syncStatus.textContent = "Sync status unavailable";
  }
}

function renderSyncStatus(status) {
  const last = status.lastFinishedAt ? `Last: ${formatDateTime(status.lastFinishedAt)}` : "Not synced by server";
  const next = status.nextRunAt ? `Next: ${formatDateTime(status.nextRunAt)}` : "";
  els.syncStatus.textContent = status.running ? "Sync running..." : [last, next].filter(Boolean).join(" | ");
  els.sync.disabled = Boolean(status.running);
}

async function runSync() {
  els.sync.disabled = true;
  els.syncStatus.textContent = "Sync running...";
  try {
    const response = await fetch("/api/sync", { method: "POST" });
    const status = await response.json();
    renderSyncStatus(status);
    if (status.lastSuccess) {
      const dataResponse = await fetch("./data.json", { cache: "reload" });
      state.data = await dataResponse.json();
      render();
    }
  } catch {
    els.syncStatus.textContent = "Sync failed to start";
  } finally {
    els.sync.disabled = false;
  }
}
