"use strict";

// --- State ---------------------------------------------------------------------

let charts = {};
let priceMap = null;
let priceMapMarkers = [];
let liveRentalPrices = {}; // canonical GPU name -> cheapest live $/hr, set by loadLivePrices
let tokenPriceData = null; // OpenRouter payload, fetched once at init
let latestTokenRanking = null; // cheapest-GPU token cost row from the latest /compute
let defaultRequestSnapshot = null; // pristine scenario signature from defaults, for "modified" detection
let defaultControlValues = {}; // id -> pristine value for every shared control
let defaultGpus = []; // pristine defaults.gpus array clone, for full-editor reset
let workloadProfiles = new Map(); // curated workload id -> server-owned assumptions
let benchmarkData = null; // versioned hardware/model benchmark registry

const CHART_PALETTE = ["#58a6ff", "#3fd68f", "#e8a33d", "#ff6b61", "#bc8cff", "#79c0ff"];
const CHART_TEXT = "#8a94a6";
const CHART_FAINT = "#5c6675";
const CHART_GRID = "#232936";

// Shared controls a preset writes (GPU specs are not part of presets).
const SCENARIO_PRESETS = {
  neocloud:    { power_cost: 0.08, pue: 1.3,  opex_frac: 5, utilization: 70, od_price: 2.50, res_price: 1.60, res_term: 12, fleet_size: 1000, rent_horizon: 36, monthly_demand: 20, capacity_headroom: 15 },
  hyperscaler: { power_cost: 0.05, pue: 1.15, opex_frac: 4, utilization: 85, od_price: 2.50, res_price: 1.40, res_term: 36, fleet_size: 50000, rent_horizon: 48, monthly_demand: 500, capacity_headroom: 20 },
  onprem:      { power_cost: 0.12, pue: 1.6,  opex_frac: 8, utilization: 45, od_price: 2.50, res_price: 1.60, res_term: 12, fleet_size: 64,   rent_horizon: 36, monthly_demand: 5, capacity_headroom: 15 },
};

// --- Formatting helpers --------------------------------------------------------

const usd = (n, decimals = 2) =>
  "$" + (n ?? 0).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

const usdCompact = (n) => {
  if (Math.abs(n) >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  if (Math.abs(n) >= 1e3) return "$" + (n / 1e3).toFixed(1) + "K";
  return usd(n, 0);
};

const pct = (n) => (n * 100).toFixed(1) + "%";

const fmt = (n, decimals = 2) =>
  (n ?? 0).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

const num = (n) => `<td class="num">${n}</td>`;

function chartTooltip(params, valueFormatter = usdCompact) {
  const rows = Array.isArray(params) ? params : [params];
  const title = rows[0]?.axisValueLabel || rows[0]?.name || "";
  const body = rows.map((p) => {
    const raw = Array.isArray(p.value) ? p.value[p.value.length - 1] : p.value;
    return `${p.marker || ""}${esc(p.seriesName || "Value")}: <strong>${valueFormatter(raw)}</strong>`;
  }).join("<br>");
  return `${title ? `<div class="chart-tip-title">${esc(title)}</div>` : ""}${body}`;
}

function chartScaffold({
  xType = "category",
  xData = [],
  yType = "value",
  yFormatter = usdCompact,
  xFormatter = null,
  legend = true,
  grid = {},
} = {}) {
  return {
    backgroundColor: "transparent",
    color: CHART_PALETTE,
    animationDuration: 250,
    animationEasing: "cubicOut",
    textStyle: { color: CHART_TEXT, fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" },
    grid: { left: 58, right: 20, top: legend ? 46 : 18, bottom: 42, ...grid },
    legend: legend ? {
      top: 0,
      left: 0,
      itemWidth: 16,
      itemHeight: 3,
      textStyle: { color: CHART_TEXT, fontSize: 11 },
    } : undefined,
    tooltip: {
      trigger: "axis",
      backgroundColor: "#1a1f29",
      borderColor: "#2f3747",
      textStyle: { color: "#e8edf4", fontSize: 12 },
      extraCssText: "box-shadow:0 10px 28px rgba(0,0,0,.35);border-radius:8px;",
      formatter: (params) => chartTooltip(params, yFormatter),
    },
    xAxis: {
      type: xType,
      data: xType === "category" ? xData : undefined,
      boundaryGap: xType === "category",
      axisLine: { lineStyle: { color: CHART_GRID } },
      axisTick: { show: false },
      axisLabel: {
        color: CHART_FAINT,
        fontSize: 10,
        hideOverlap: true,
        formatter: xFormatter || undefined,
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: yType,
      scale: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: CHART_FAINT, fontSize: 10, formatter: yFormatter },
      splitLine: { lineStyle: { color: CHART_GRID, opacity: 0.65 } },
    },
  };
}

function renderEChart(key, elementId, option) {
  const element = document.getElementById(elementId);
  if (!element || typeof echarts === "undefined") return null;
  let chart = charts[key];
  if (!chart || chart.isDisposed()) {
    chart = echarts.init(element, null, { renderer: "canvas" });
    charts[key] = chart;
  }
  chart.setOption(option, { notMerge: true, lazyUpdate: false });
  requestAnimationFrame(() => chart.resize());
  return chart;
}

function resizeCharts() {
  Object.values(charts).forEach((chart) => {
    if (chart && typeof chart.resize === "function" && !chart.isDisposed()) chart.resize();
  });
}

// GPU names round-trip through the shareable URL, so treat them as untrusted.
const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

function parseNumber(value, fallback) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseWholeNumber(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parsePercent(value, fallbackPercent) {
  return parseNumber(value, fallbackPercent) / 100;
}

// --- Build request from controls ----------------------------------------------

function buildRequest() {
  const gpuRows = document.querySelectorAll(".gpu-input-row");
  const gpus = Array.from(gpuRows).map((row) => {
    const inputs = row.querySelectorAll("input");
    return {
      name: inputs[0].value || "GPU",
      capex_usd: parseNumber(inputs[1].value, 0),
      power_kw: parseNumber(inputs[2].value, 0),
      tokens_per_sec: parseNumber(inputs[3].value, 0),
      useful_life_years: parseNumber(inputs[4].value, 4.0),
      residual_value_frac: parsePercent(inputs[5].value, 10),
    };
  });

  return {
    gpus,
    datacenter: {
      power_cost_per_kwh: parseNumber(document.getElementById("power_cost").value, 0),
      pue: parseNumber(document.getElementById("pue").value, 1),
      opex_frac_of_capex_per_year: parsePercent(document.getElementById("opex_frac").value, 0),
    },
    workload: {
      utilization: parsePercent(document.getElementById("utilization").value, 70),
      on_demand_price_per_gpu_hour: parseNumber(document.getElementById("od_price").value, 0),
      reserved_price_per_gpu_hour: parseNumber(document.getElementById("res_price").value, 0),
      reserved_term_months: parseWholeNumber(document.getElementById("res_term").value, 12),
    },
    fleet_size: parseWholeNumber(document.getElementById("fleet_size").value, 1000),
    rental_prices: liveRentalPrices,
    rent_horizon_months: parseNumber(document.getElementById("rent_horizon").value, 36),
    monthly_token_demand: parseNumber(document.getElementById("monthly_demand").value, 20) * 1e9,
    capacity_headroom: parsePercent(document.getElementById("capacity_headroom").value, 15),
  };
}

// --- Shareable URL state ---------------------------------------------------------

// Short query keys for the shared controls.
const URL_FIELDS = {
  pc: "power_cost", pue: "pue", opex: "opex_frac", util: "utilization",
  od: "od_price", res: "res_price", term: "res_term", fleet: "fleet_size",
  hor: "rent_horizon", demand: "monthly_demand", headroom: "capacity_headroom",
};

function syncUrl() {
  const params = new URLSearchParams();
  for (const [key, id] of Object.entries(URL_FIELDS)) {
    const el = document.getElementById(id);
    if (el && el.value !== "") params.set(key, el.value);
  }
  document.querySelectorAll(".gpu-input-row").forEach((row) => {
    const vals = Array.from(row.querySelectorAll("input")).map((i) => i.value);
    params.append("gpu", vals.join("~"));
  });
  history.replaceState(null, "", "?" + params.toString());
}

function restoreFromUrl(defaults) {
  const params = new URLSearchParams(location.search);
  if ([...params.keys()].length === 0) return;
  for (const [key, id] of Object.entries(URL_FIELDS)) {
    const v = params.get(key);
    const el = document.getElementById(id);
    if (v !== null && el && Number.isFinite(Number(v))) el.value = v;
  }
  const gpuParams = params.getAll("gpu");
  if (gpuParams.length) {
    defaults.gpus = gpuParams.map((s) => {
      const [name, capex, kw, tok, life, resid] = s.split("~");
      return {
        name: (name || "GPU").slice(0, 24),
        capex_usd: parseNumber(capex, 30000),
        power_kw: parseNumber(kw, 0.7),
        tokens_per_sec: parseNumber(tok, 2500),
        useful_life_years: parseNumber(life, 4),
        residual_value_frac: parseNumber(resid, 10) / 100,
      };
    });
  }
}

// --- Render GPU editor ---------------------------------------------------------

function renderGpuEditor(defaults) {
  const editor = document.getElementById("gpu-editor");
  editor.innerHTML = "";
  const fields = [
    { label: "Name", key: "name", type: "text", width: "60px" },
    { label: "Capex", key: "capex_usd", type: "number" },
    { label: "kW", key: "power_kw", type: "number" },
    { label: "Tok/s", key: "tokens_per_sec", type: "number" },
    { label: "Life (yr)", key: "useful_life_years", type: "number" },
    { label: "Resid %", key: "residual_value_frac", type: "number" },
  ];

  for (const gpu of defaults.gpus) {
    const row = document.createElement("div");
    row.className = "gpu-row gpu-input-row";
    for (const f of fields) {
      const input = document.createElement("input");
      input.type = f.type;
      input.value = f.key === "residual_value_frac" ? gpu[f.key] * 100 : gpu[f.key];
      if (f.key === "residual_value_frac") input.step = "0.5";
      if (f.key === "name") input.classList.add("gpu-name");
      input.addEventListener("input", debounce(recompute, 300));
      row.appendChild(input);
    }
    editor.appendChild(row);
  }

  // Add header labels above first row
  const header = document.createElement("div");
  header.className = "gpu-row";
  header.style.fontSize = "0.7rem";
  header.style.color = "var(--text-muted)";
  header.style.marginBottom = "-0.25rem";
  for (const f of fields) {
    const span = document.createElement("span");
    span.textContent = f.label;
    header.appendChild(span);
  }
  editor.insertBefore(header, editor.firstChild);
}

// --- Render results ------------------------------------------------------------

function renderResults(data) {
  renderDecision(data.decision_summary, data.results);
  renderHourly(data.results);
  renderTokens(data.token_ranking, data.results);
  renderMargin(data.results);
  renderDepreciation(data.results);
  renderBreakEven(data.results);
  renderRentVsBuy(data.results);
  latestTokenRanking = (data.token_ranking && data.token_ranking[0]) || null;
  renderInferenceMargin();
}

function renderDecision(decision, results) {
  const host = document.getElementById("decision-summary");
  const action = decision.option === "own" ? "Own" : "Rent";
  const fleetPhrase = decision.option === "own"
    ? `a ${fmt(decision.fleet_size, 0)}-GPU ${esc(decision.gpu)} fleet`
    : `${esc(decision.gpu)} capacity`;
  const nextBest = `${decision.next_best_option} ${esc(decision.next_best_gpu)}`;
  const demand = decision.monthly_token_demand / 1e9;

  host.innerHTML = `
    <div class="decision-verdict">
      <span class="decision-label">Lowest modeled cost</span>
      <strong>${action} ${fleetPhrase}</strong>
      <p>For ${fmt(demand, 1)}B tokens per month over ${fmt(decision.horizon_months, 0)} months, this plan is ${usdCompact(decision.savings_vs_next_best)} cheaper than ${nextBest}.</p>
    </div>
    <div class="decision-metrics">
      <div><span>Monthly cost</span><strong>${usdCompact(decision.monthly_cost)}</strong></div>
      <div><span>Horizon cost</span><strong>${usdCompact(decision.total_cost)}</strong></div>
      <div><span>Upfront capital</span><strong>${decision.upfront_capex ? usdCompact(decision.upfront_capex) : "$0"}</strong></div>
    </div>`;

  const table = document.getElementById("table-fleet");
  table.querySelector("thead").innerHTML =
    `<tr><th>GPU</th><th>Fleet needed</th><th>Usable capacity</th><th>Own / month</th><th>Rent / month</th><th>Lower-cost path</th></tr>`;
  table.querySelector("tbody").innerHTML = results.map((r) => {
    const p = r.fleet_plan;
    const verdict = p.cheaper === "own"
      ? `<span class="good">own</span> · saves ${usdCompact(p.savings)}`
      : `<span class="good">rent</span> · saves ${usdCompact(p.savings)}`;
    return `<tr><td>${esc(r.name)}</td>${num(fmt(p.fleet_size, 0))}${num(`${fmt(p.monthly_token_capacity / 1e9, 1)}B tok`)}${num(usdCompact(p.monthly_ownership_cost))}${num(usdCompact(p.monthly_rental_cost))}<td>${verdict}</td></tr>`;
  }).join("");
}

function renderHourly(results) {
  const tbl = document.getElementById("table-hourly");
  tbl.querySelector("thead").innerHTML =
    `<tr><th>GPU</th><th>Depr/hr</th><th>Power/hr</th><th>Opex/hr</th><th>$/prov-hr</th><th>$/billable-hr</th></tr>`;
  tbl.querySelector("tbody").innerHTML = results
    .map(
      (r) =>
        `<tr><td>${esc(r.name)}</td>${num(usd(r.cost_per_hour.depreciation))}${num(usd(r.cost_per_hour.power))}${num(usd(r.cost_per_hour.opex))}<td class="num"><strong>${usd(r.cost_per_hour.provisioned)}</strong></td><td class="num"><strong>${usd(r.cost_per_hour.billable)}</strong></td></tr>`
    )
    .join("");
}

function renderTokens(ranked, results) {
  const map = Object.fromEntries(results.map((r) => [r.name, r]));
  const tbl = document.getElementById("table-tokens");
  tbl.querySelector("thead").innerHTML =
    `<tr><th>GPU</th><th>Tok/hr (eff)</th><th>$/prov-hr</th><th>$/1M tokens</th></tr>`;
  tbl.querySelector("tbody").innerHTML = ranked
    .map((t, i) => {
      const r = map[t.name];
      const badge = i === 0 ? " <span style='color:var(--green)'>cheapest</span>" : "";
      return `<tr><td>${esc(t.name)}${badge}</td>${num(fmt(r.effective_tokens_per_hour, 0))}${num(usd(r.cost_per_hour.provisioned))}<td class="num"><strong>${usd(t.cost_per_million_tokens)}</strong></td></tr>`;
    })
    .join("");
}

function renderMargin(results) {
  const tbl = document.getElementById("table-margin");
  tbl.querySelector("thead").innerHTML =
    `<tr><th>GPU</th><th>Price/hr</th><th>Cost/hr</th><th>Profit/hr</th><th>Margin</th><th>Annual GP/GPU</th></tr>`;
  tbl.querySelector("tbody").innerHTML = results
    .map((r) => {
      const m = r.margin;
      const color = m.margin_pct >= 0.3 ? "var(--green)" : m.margin_pct >= 0.1 ? "var(--orange)" : "var(--red)";
      return `<tr><td>${esc(r.name)}</td>${num(usd(m.price_per_billable_hour))}${num(usd(m.cost_per_billable_hour))}${num(usd(m.gross_profit_per_hour))}<td class="num" style="color:${color};font-weight:600">${pct(m.margin_pct)}</td>${num(usdCompact(m.annual_gp_per_gpu))}</tr>`;
    })
    .join("");

  const option = chartScaffold({ xData: results.map((r) => r.name) });
  option.yAxis.scale = false;
  option.yAxis.min = 0;
  option.series = [
    {
      name: "Price/hr",
      type: "bar",
      data: results.map((r) => r.margin.price_per_billable_hour),
      barMaxWidth: 38,
      itemStyle: { color: "#58a6ff", borderRadius: [4, 4, 0, 0] },
    },
    {
      name: "Cost/hr",
      type: "bar",
      data: results.map((r) => r.margin.cost_per_billable_hour),
      barMaxWidth: 38,
      itemStyle: { color: "#ff6b61", borderRadius: [4, 4, 0, 0] },
    },
  ];
  renderEChart("margin", "chart-margin", option);

  renderMarginHeatmap(results);
}

function renderMarginHeatmap(results) {
  const host = document.getElementById("margin-heatmap");
  if (!host) return;
  if (results.length === 0) return;
  const cost = results[0].cost_per_hour.provisioned; // utilization-independent
  const utils = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0];
  const prices = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0];
  const data = [];
  utils.forEach((u, y) => prices.forEach((p, x) => {
    data.push([x, y, (p - cost / u) / p]);
  }));
  renderEChart("marginHeatmap", "margin-heatmap", {
    backgroundColor: "transparent",
    animationDuration: 250,
    grid: { left: 54, right: 18, top: 18, bottom: 62 },
    tooltip: {
      position: "top",
      backgroundColor: "#1a1f29",
      borderColor: "#2f3747",
      textStyle: { color: "#e8edf4" },
      formatter: (p) => `${pct(utils[p.value[1]])} utilization at ${usd(prices[p.value[0]])}/hr<br><strong>${pct(p.value[2])} margin</strong>`,
    },
    xAxis: {
      type: "category",
      data: prices.map((p) => usd(p)),
      name: "Price / GPU-hour",
      nameLocation: "middle",
      nameGap: 34,
      nameTextStyle: { color: CHART_FAINT, fontSize: 10 },
      axisLine: { lineStyle: { color: CHART_GRID } },
      axisTick: { show: false },
      axisLabel: { color: CHART_FAINT, fontSize: 10 },
      splitArea: { show: true, areaStyle: { color: ["#12151c", "#141821"] } },
    },
    yAxis: {
      type: "category",
      data: utils.map((u) => pct(u)),
      name: "Utilization",
      nameTextStyle: { color: CHART_FAINT, fontSize: 10 },
      axisLine: { lineStyle: { color: CHART_GRID } },
      axisTick: { show: false },
      axisLabel: { color: CHART_FAINT, fontSize: 10 },
      splitArea: { show: true, areaStyle: { color: ["#12151c", "#141821"] } },
    },
    visualMap: {
      min: -0.6,
      max: 0.6,
      calculable: false,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      text: ["higher margin", "loss"],
      textStyle: { color: CHART_FAINT, fontSize: 10 },
      inRange: { color: ["#5f2427", "#252b35", "#174936"] },
    },
    series: [{
      name: "Gross margin",
      type: "heatmap",
      data,
      label: { show: true, color: "#e8edf4", fontSize: 10, formatter: (p) => pct(p.value[2]) },
      itemStyle: { borderColor: "#12151c", borderWidth: 2, borderRadius: 3 },
      emphasis: { itemStyle: { borderColor: "#4d9fff", borderWidth: 1 } },
    }],
  });
}

// --- Implied inference margin --------------------------------------------------

async function loadTokenPrices() {
  try {
    const resp = await fetch("/api/token-prices");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    tokenPriceData = await resp.json();
    renderInferenceMargin();
  } catch (err) {
    console.error("Token prices failed:", err);
    const tbl = document.getElementById("table-infmargin");
    if (tbl) {
      tbl.querySelector("thead").innerHTML = "";
      tbl.querySelector("tbody").innerHTML =
        `<tr><td class="muted">Token prices unavailable.</td></tr>`;
    }
  }
}

function renderInferenceMargin() {
  const tbl = document.getElementById("table-infmargin");
  if (!tbl) return;
  // Needs both the token prices and a modeled cost from the latest compute.
  if (!tokenPriceData || !latestTokenRanking) return;
  const cost = latestTokenRanking.cost_per_million_tokens;
  tbl.querySelector("thead").innerHTML =
    `<tr><th>Model</th><th>$/1M in</th><th>$/1M out</th><th>Modeled cost /1M</th><th>Implied margin</th></tr>`;
  tbl.querySelector("tbody").innerHTML = tokenPriceData.models
    .map((m) => {
      const price = m.usd_per_million_output;
      const margin = (price - cost) / price;
      const cls = margin >= 0 ? "good" : "bad";
      return `<tr><td>${esc(m.name)}</td>${num(usd(m.usd_per_million_input, 3))}${num(usd(price, 3))}${num(usd(cost, 3))}<td class="num ${cls}">${pct(margin)}</td></tr>`;
    })
    .join("");
}

function renderDepreciation(results) {
  const tbl = document.getElementById("table-depreciation");
  tbl.querySelector("thead").innerHTML =
    `<tr><th>GPU</th><th>3yr EBITDA swing (fleet)</th><th>3yr $/hr</th><th>4yr $/hr</th><th>5yr $/hr</th><th>6yr $/hr</th></tr>`;
  tbl.querySelector("tbody").innerHTML = results
    .map((r) => {
      const swing = r.ebitda_swing_3v6;
      const sens = r.depreciation_sensitivity;
      return `<tr><td>${esc(r.name)}</td><td class="num" style="color:var(--orange)">${usdCompact(swing.ebitda_delta_usd)}</td>${sens.map((s) => num(usd(s.provisioned_cost)).replace("<td", "<td")).join("")}</tr>`;
    })
    .join("");

  const depreciationOption = chartScaffold({ xData: ["3 yr", "4 yr", "5 yr", "6 yr"] });
  depreciationOption.series = results.map((r, i) => ({
    name: r.name,
    type: "line",
    data: r.depreciation_sensitivity.map((s) => s.provisioned_cost),
    showSymbol: true,
    symbolSize: 6,
    smooth: false,
    lineStyle: { width: 2, color: CHART_PALETTE[i % CHART_PALETTE.length] },
    itemStyle: { color: CHART_PALETTE[i % CHART_PALETTE.length] },
  }));
  renderEChart("depreciation", "chart-depreciation", depreciationOption);

  // Book value curve for the first GPU only, one line per useful life.
  if (results.length === 0) return;
  const first = results[0];
  const bvCurves = first.book_value_curves;
  if (!bvCurves) return;
  const lives = Object.keys(bvCurves).sort((a, b) => Number(a) - Number(b));
  const bookValueOption = chartScaffold({
    xType: "value",
    xFormatter: (v) => `${v}m`,
  });
  bookValueOption.series = lives.map((life, i) => ({
    name: `${life} yr`,
    type: "line",
    data: bvCurves[life].map((p) => [p.month, p.book_value]),
    showSymbol: false,
    smooth: false,
    lineStyle: { width: 2, color: CHART_PALETTE[i % CHART_PALETTE.length] },
  }));
  renderEChart("bookvalue", "chart-bookvalue", bookValueOption);
}

function renderBreakEven(results) {
  const summary = document.getElementById("break-even-summary");
  summary.innerHTML = results
    .map((r) => {
      const be = r.break_even;
      const cheaperClass = be.cheaper === "reserved" ? "be-cheaper" : "";
      return `<div class="be-item"><strong>${esc(r.name)}</strong>Break-even util: <strong>${be.utilization === Infinity ? "∞" : pct(be.utilization)}</strong><br>At current util: <span class="${cheaperClass}">${be.cheaper}</span> saves ${usdCompact(be.savings)}</div>`;
    })
    .join("");

  // Chart - break-even curves for first GPU
  if (results.length === 0) return;
  const first = results[0];
  const breakEvenOption = chartScaffold({
    xData: first.break_even_curve.map((c) => pct(c.utilization)),
  });
  breakEvenOption.series = [
    {
      name: "Reserved",
      type: "line",
      data: first.break_even_curve.map((c) => c.reserved_total_cost),
      showSymbol: false,
      lineStyle: { width: 2, color: "#58a6ff" },
      areaStyle: { color: "rgba(88,166,255,0.05)" },
    },
    {
      name: "On-demand",
      type: "line",
      data: first.break_even_curve.map((c) => c.on_demand_total_cost),
      showSymbol: false,
      lineStyle: { width: 2, color: "#ff6b61" },
    },
  ];
  renderEChart("breakeven", "chart-breakeven", breakEvenOption);
}

// --- Live market prices ----------------------------------------------------------

function renderLivePrices(data) {
  const tbl = document.getElementById("table-live");
  tbl.querySelector("thead").innerHTML =
    `<tr><th>GPU</th><th>Provider</th><th>Tier</th><th>Detail</th><th>$/hr</th><th></th></tr>`;
  // The batch now carries every regional quote; keep this summary table to the
  // cheapest per (gpu, provider) and leave geography to the regional card.
  const cheapest = {};
  for (const p of data.prices) {
    const k = p.gpu + "|" + p.provider;
    if (!(k in cheapest) || p.price_per_hour < cheapest[k].price_per_hour) cheapest[k] = p;
  }
  const rows = Object.values(cheapest).sort(
    (a, b) => a.gpu.localeCompare(b.gpu) || a.price_per_hour - b.price_per_hour
  );
  const tbody = tbl.querySelector("tbody");
  tbody.innerHTML = "";
  // Cheapest live quote per GPU feeds the rent-vs-buy overlay.
  liveRentalPrices = {};
  for (const p of rows) {
    if (!(p.gpu in liveRentalPrices)) liveRentalPrices[p.gpu] = p.price_per_hour;
  }
  for (const p of rows) {
    // Provider fields are external data: build cells via textContent, never innerHTML.
    const tr = document.createElement("tr");

    const tdGpu = document.createElement("td");
    tdGpu.textContent = p.gpu;

    const tdProvider = document.createElement("td");
    if (typeof p.source_url === "string" && p.source_url.startsWith("https://")) {
      const a = document.createElement("a");
      a.href = p.source_url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = p.provider;
      tdProvider.appendChild(a);
    } else {
      tdProvider.textContent = p.provider;
    }

    const tdKind = document.createElement("td");
    tdKind.textContent = p.kind;

    const tdDetail = document.createElement("td");
    tdDetail.className = "muted";
    tdDetail.textContent = p.region ? `${p.detail}, ${p.region}` : p.detail;

    const tdPrice = document.createElement("td");
    tdPrice.className = "num";
    tdPrice.textContent = usd(p.price_per_hour);

    const tdApply = document.createElement("td");
    const btn = document.createElement("button");
    btn.className = "apply-price";
    btn.title = "Sets the model's global on-demand price assumption (applies to all GPUs)";
    btn.textContent = "Use price";
    btn.addEventListener("click", () => {
      document.getElementById("od_price").value = p.price_per_hour.toFixed(2);
      recompute();
    });
    tdApply.appendChild(btn);

    tr.append(tdGpu, tdProvider, tdKind, tdDetail, tdPrice, tdApply);
    tbody.appendChild(tr);
  }

  const fresh = document.getElementById("live-freshness");
  if (data.fetched_at) {
    const when = new Date(data.fetched_at * 1000).toLocaleString();
    fresh.textContent = `Last fetched ${when}${data.stale ? " (stale — upstream unreachable)" : ""}`;
    fresh.classList.toggle("stale", data.stale);
  } else {
    fresh.textContent = "No price data yet — providers unreachable.";
    fresh.classList.add("stale");
  }
  if (data.errors && data.errors.length) console.warn("Provider errors:", data.errors);

  renderKpis(data);
}

// --- KPI hero strip ----------------------------------------------------------------

function renderKpis(data) {
  const strip = document.getElementById("kpi-strip");
  strip.innerHTML = "";

  // Cheapest live quote and quote count per canonical GPU.
  const byGpu = {};
  for (const p of data.prices) {
    (byGpu[p.gpu] ??= []).push(p);
  }
  const order = ["H100", "H200", "B200"];
  for (const gpu of order) {
    const quotes = byGpu[gpu];
    if (!quotes || !quotes.length) continue;
    const cheapest = quotes.reduce((a, b) => (a.price_per_hour <= b.price_per_hour ? a : b));
    const kpi = document.createElement("div");
    kpi.className = "kpi";
    const label = document.createElement("div");
    label.className = "kpi-label";
    label.textContent = `${gpu} · cheapest live`;
    const value = document.createElement("div");
    value.className = "kpi-value";
    value.textContent = usd(cheapest.price_per_hour) + "/hr";
    const sub = document.createElement("div");
    sub.className = "kpi-sub";
    sub.textContent = `${cheapest.provider}${cheapest.region ? " · " + cheapest.region : ""} · ${quotes.length} quotes`;
    const spark = document.createElement("div");
    spark.className = "kpi-spark";
    spark.dataset.gpu = gpu;
    spark.id = `spark-${gpu}`;
    const sparkMeta = document.createElement("div");
    sparkMeta.className = "kpi-spark-meta";
    sparkMeta.dataset.gpu = gpu;
    sparkMeta.innerHTML = "<span>7d trend</span><strong>loading</strong>";
    kpi.append(label, value, sub, spark, sparkMeta);
    strip.appendChild(kpi);
  }

  // Freshness KPI.
  if (data.fetched_at) {
    const kpi = document.createElement("div");
    kpi.className = "kpi";
    const label = document.createElement("div");
    label.className = "kpi-label";
    label.textContent = "Data freshness";
    const value = document.createElement("div");
    value.className = "kpi-value";
    const mins = Math.max(0, Math.round((Date.now() / 1000 - data.fetched_at) / 60));
    value.textContent = mins <= 1 ? "live" : `${mins}m ago`;
    const sub = document.createElement("div");
    sub.className = "kpi-sub";
    sub.textContent = data.stale ? "stale — upstream unreachable" : "polled every 15 min";
    kpi.append(label, value, sub);
    strip.appendChild(kpi);
  }
}

async function loadLivePrices() {
  try {
    const resp = await fetch("/api/prices");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    renderLivePrices(await resp.json());
    recompute(); // re-price rent-vs-buy with the live overlay
  } catch (err) {
    console.error("Live prices failed:", err);
    document.getElementById("live-freshness").textContent = "Live prices unavailable.";
  }
}

// --- Rent vs buy -------------------------------------------------------------------

function renderRentVsBuy(results) {
  const tbl = document.getElementById("table-rentbuy");
  tbl.querySelector("thead").innerHTML =
    `<tr><th>GPU</th><th>Rental $/hr</th><th>Own $/prov-hr</th><th>Break-even util</th><th>Own total</th><th>Rent total</th><th>Verdict</th></tr>`;
  tbl.querySelector("tbody").innerHTML = results
    .map((r) => {
      const v = r.rent_vs_buy;
      const be = v.break_even_utilization === null ? "—" : pct(v.break_even_utilization);
      const verdict = v.cheaper === "own"
        ? `<span class="good">own</span> saves ${usdCompact(v.savings)}`
        : `<span class="bad">rent</span> saves ${usdCompact(v.savings)}`;
      return `<tr><td>${esc(r.name)}</td>${num(usd(v.rental_price_per_hour))}${num(usd(v.owner_cost_per_provisioned_hour))}${num(be)}${num(usdCompact(v.own_total_cost))}${num(usdCompact(v.rent_total_cost))}<td>${verdict}</td></tr>`;
    })
    .join("");

  const labels = results[0].rent_vs_buy_curve.map((p) => pct(p.utilization));
  const option = chartScaffold({ xData: labels, grid: { top: 66 } });
  option.legend = {
    ...option.legend,
    type: "scroll",
    pageTextStyle: { color: CHART_TEXT },
    pageIconColor: "#58a6ff",
    pageIconInactiveColor: CHART_FAINT,
  };
  option.series = results.flatMap((r, i) => {
    const color = CHART_PALETTE[i % 3];
    return [
      {
        name: `${r.name} rent`,
        type: "line",
        data: r.rent_vs_buy_curve.map((p) => p.rent_total_cost),
        showSymbol: false,
        lineStyle: { width: 2, type: "dashed", color },
      },
      {
        name: `${r.name} own`,
        type: "line",
        data: r.rent_vs_buy_curve.map((p) => p.own_total_cost),
        showSymbol: false,
        lineStyle: { width: 2, color },
      },
    ];
  });
  renderEChart("rentbuy", "chart-rentbuy", option);
}

// --- Regional prices & arbitrage ---------------------------------------------------

function renderPriceMap(info) {
  const mapEl = document.getElementById("price-map");
  if (!mapEl || typeof L === "undefined") return;

  if (!priceMap) {
    priceMap = L.map("price-map", { scrollWheelZoom: false, worldCopyJump: true }).setView([25, 10], 2);
    L.tileLayer("https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
      maxZoom: 10,
    }).addTo(priceMap);
  } else {
    priceMapMarkers.forEach((marker) => marker.remove());
  }
  priceMapMarkers = [];

  const base = info.cheapest.price_per_hour;
  for (const q of info.quotes) {
    if (q.lat == null || q.lon == null) continue;
    const lat = Number(q.lat);
    const lon = Number(q.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;

    const ratio = base > 0 ? q.price_per_hour / base : Infinity;
    const color = ratio <= 1.15 ? "#3fd68f" : ratio <= 2 ? "#e8a33d" : "#ff6b61";
    const radius = 6 + 12 * Math.min(1, base / q.price_per_hour);
    const marker = L.circleMarker([lat, lon], {
      radius,
      color,
      fillColor: color,
      fillOpacity: 0.55,
      weight: 1,
    }).addTo(priceMap);

    const popup = document.createElement("div");
    popup.className = "map-popup";
    const region = document.createElement("strong");
    region.className = "map-popup-region";
    region.textContent = q.region || "Unknown region";
    const provider = document.createElement("div");
    provider.className = "map-popup-provider";
    provider.textContent = [q.provider, q.kind].filter(Boolean).join(" · ");
    const detail = document.createElement("div");
    detail.className = "map-popup-detail";
    detail.textContent = q.detail || "";
    const price = document.createElement("div");
    price.className = "map-popup-price";
    price.textContent = usd(q.price_per_hour) + "/GPU-hr";
    popup.append(region, provider, detail, price);
    marker.bindPopup(popup);
    priceMapMarkers.push(marker);
  }

  priceMap.invalidateSize();
}

async function loadRegions() {
  const gpu = document.getElementById("region-gpu").value;
  try {
    const [regResp, spreadResp] = await Promise.all([
      fetch("/api/prices/regions"),
      fetch(`/api/prices/spread?gpu=${encodeURIComponent(gpu)}`),
    ]);
    if (!regResp.ok || !spreadResp.ok) throw new Error("regional fetch failed");
    const reg = await regResp.json();
    const spread = await spreadResp.json();

    document.getElementById("regional-caveat").textContent = reg.caveat;

    // Table: all regional quotes for the selected GPU, cheapest first.
    const tbl = document.getElementById("table-regions");
    tbl.querySelector("thead").innerHTML =
      `<tr><th>Region</th><th>Provider</th><th>Tier</th><th>SKU</th><th>$/GPU-hr</th><th>vs cheapest</th></tr>`;
    const tbody = tbl.querySelector("tbody");
    tbody.innerHTML = "";
    const info = reg.gpus[gpu];
    if (!info) return;
    const base = info.cheapest.price_per_hour;
    for (const q of info.quotes) {
      const tr = document.createElement("tr");
      // External fields (region, detail) rendered via textContent only.
      const cells = [
        q.region,
        q.provider,
        q.kind,
        q.detail,
        usd(q.price_per_hour),
        base > 0 ? "+" + (((q.price_per_hour / base) - 1) * 100).toFixed(0) + "%" : "—",
      ];
      cells.forEach((v, i) => {
        const td = document.createElement("td");
        td.textContent = v;
        if (i >= 4) td.className = "num";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    }
    renderPriceMap(info);

    // Arbitrage line: cheapest region rental vs local ownership cost.
    const summary = document.getElementById("arbitrage-summary");
    summary.innerHTML = "";
    const div = document.createElement("div");
    div.className = "be-item";
    div.textContent =
      `${gpu}: cheapest region ${info.cheapest.region} (${info.cheapest.provider}) at ` +
      `${usd(info.cheapest.price_per_hour)}/hr; spread ${info.spread_ratio ?? "—"}x across ` +
      `${info.quotes.length} regional quotes. Compare with owning at your state's power ` +
      `rate in section 6.`;
    summary.appendChild(div);

    const spreadOption = chartScaffold({
      xType: "time",
      xFormatter: (v) => new Date(v).toLocaleDateString([], { month: "short", day: "numeric" }),
    });
    spreadOption.series = [
      {
        name: `${gpu} cheapest region`,
        type: "line",
        data: spread.batches.map((b) => [b.fetched_at * 1000, b.min_price]),
        showSymbol: false,
        connectNulls: true,
        lineStyle: { width: 2, color: "#3fd68f" },
        areaStyle: { color: "rgba(63,214,143,0.05)" },
      },
      {
        name: `${gpu} priciest region`,
        type: "line",
        data: spread.batches.map((b) => [b.fetched_at * 1000, b.max_price]),
        showSymbol: false,
        connectNulls: true,
        lineStyle: { width: 2, color: "#ff6b61" },
      },
    ];
    renderEChart("spread", "chart-spread", spreadOption);
  } catch (err) {
    console.error("Regions failed:", err);
  }
}

// --- EIA power prices --------------------------------------------------------------

async function loadPowerPrices() {
  const select = document.getElementById("power-state");
  try {
    const resp = await fetch("/api/power");
    if (!resp.ok) return; // 503 = no key configured; keep manual input silently
    const data = await resp.json();
    for (const s of data.states) {
      const opt = document.createElement("option");
      opt.value = s.usd_per_kwh;
      opt.textContent = `${s.name} — $${s.usd_per_kwh.toFixed(3)}`;
      select.appendChild(opt);
    }
    select.title = `EIA industrial rates, ${data.period}`;
    select.addEventListener("change", () => {
      if (select.value === "") return;
      document.getElementById("power_cost").value = select.value;
      recompute();
    });
    select.parentElement.style.display = "";
  } catch (err) {
    console.error("Power prices failed:", err);
  }
}

// --- Benchmark throughput presets ---------------------------------------------

async function loadBenchmarks() {
  const select = document.getElementById("bench-model");
  const note = document.getElementById("bench-note");
  try {
    const resp = await fetch("/api/benchmarks");
    if (!resp.ok) return;
    const data = await resp.json();
    benchmarkData = data;

    for (const [modelId, label] of Object.entries(data.labels)) {
      const opt = document.createElement("option");
      opt.value = modelId;
      opt.textContent = label;
      select.appendChild(opt);
    }

    select.addEventListener("change", () => {
      note.style.display = "none";
      if (!select.value) return;
      const entries = data.models[select.value] || [];
      const byGpu = Object.fromEntries(entries.map((e) => [e.gpu, e]));
      // Apply tokens/sec to any editor row whose name matches a canonical GPU.
      document.querySelectorAll(".gpu-input-row").forEach((row) => {
        const inputs = row.querySelectorAll("input");
        const entry = byGpu[inputs[0].value.trim().toUpperCase()];
        if (entry) inputs[3].value = entry.tokens_per_sec;
      });
      const kinds = [...new Set(entries.map((e) => e.kind))].join(", ");
      note.textContent =
        `Applied registry ${data.registry_version} figures (${kinds}). ` +
        `Open the evidence registry below to inspect test setup, uncertainty, and sources.`;
      note.style.display = "";
      recompute();
    });
    document.getElementById("bench-label").style.display = "";
    setupBenchmarkRegistry(data);
  } catch (err) {
    console.error("Benchmarks failed:", err);
  }
}

function addSelectOptions(id, rows) {
  const select = document.getElementById(id);
  for (const [value, label] of rows) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
  }
}

function setupBenchmarkRegistry(data) {
  const vendors = [...new Set(data.hardware.map((item) => item.vendor))].sort();
  addSelectOptions("registry-vendor", vendors.map((vendor) => [vendor, vendor]));
  addSelectOptions("registry-hardware", data.hardware.map((item) => [item.id, item.display_name]));
  addSelectOptions("registry-model", Object.entries(data.labels));
  document.getElementById("registry-version").textContent = `registry ${data.registry_version}`;

  const covered = new Set(data.entries.map((row) => `${row.gpu}:${row.model}`)).size;
  const possible = data.coverage.length;
  const measured = data.entries.filter((row) => row.classification === "measured").length;
  const estimated = data.entries.filter((row) => row.classification === "estimated").length;
  document.getElementById("registry-summary").innerHTML = `
    <div class="registry-stat"><span>Hardware catalog</span><strong>${data.hardware.length}</strong></div>
    <div class="registry-stat"><span>Model families</span><strong>${Object.keys(data.labels).length}</strong></div>
    <div class="registry-stat"><span>Measured rows</span><strong>${measured}</strong></div>
    <div class="registry-stat"><span>Evidence coverage</span><strong>${Math.round(covered / possible * 100)}%</strong></div>`;
  document.getElementById("registry-footnote").textContent =
    `${data.note} ${estimated} rows are estimates; uncovered pairs are intentionally left unavailable.`;

  for (const id of ["registry-vendor", "registry-hardware", "registry-model", "registry-classification"]) {
    document.getElementById(id).addEventListener("change", renderBenchmarkRegistry);
  }
  renderBenchmarkRegistry();
}

function benchmarkColor(classification) {
  return { measured: "#3fd68f", "vendor-reported": "#4d9fff", estimated: "#e8a33d" }[classification] || "#5c6675";
}

function renderBenchmarkRegistry() {
  if (!benchmarkData) return;
  const vendor = document.getElementById("registry-vendor").value;
  const hardwareId = document.getElementById("registry-hardware").value;
  const model = document.getElementById("registry-model").value;
  const classification = document.getElementById("registry-classification").value;
  const hardwareById = Object.fromEntries(benchmarkData.hardware.map((item) => [item.id, item]));
  const rows = benchmarkData.entries.filter((row) =>
    (!vendor || row.hardware.vendor === vendor) &&
    (!hardwareId || row.gpu === hardwareId) &&
    (!model || row.model === model) &&
    (!classification || row.classification === classification)
  );

  const selectedHardware = hardwareId ? hardwareById[hardwareId] : null;
  const detail = document.getElementById("registry-hardware-detail");
  if (selectedHardware) {
    const sourceRow = benchmarkData.sources.find((item) => item.id === selectedHardware.spec_source_id);
    detail.innerHTML = `
      <span class="hardware-vendor">${esc(selectedHardware.vendor)} · ${esc(selectedHardware.product_type)}</span>
      <h3>${esc(selectedHardware.display_name)}</h3>
      <div class="hardware-specs">
        <div><span>Memory</span><strong>${fmt(selectedHardware.memory_gb, 0)} GB</strong></div>
        <div><span>Power</span><strong>${selectedHardware.power_w == null ? "—" : `${fmt(selectedHardware.power_w, 0)} W`}</strong></div>
        <div><span>Form</span><strong>${esc(selectedHardware.form_factor)}</strong></div>
        <div><span>Own model</span><strong>${selectedHardware.ownership_supported ? "Available" : "Rent only"}</strong></div>
      </div>
      ${sourceRow ? `<a class="hardware-source" href="${esc(sourceRow.url)}" target="_blank" rel="noopener">Official specification ↗</a>` : ""}`;
  } else {
    detail.innerHTML = `<p class="intel-empty">Choose hardware to inspect its memory, power, form factor, and ownership-data coverage.</p>`;
  }

  const body = document.getElementById("registry-table-body");
  body.innerHTML = rows.length ? rows.map((row) => `
    <tr>
      <td><strong>${esc(row.hardware.display_name)}</strong><br><span class="registry-range">${esc(row.hardware.vendor)}</span></td>
      <td>${esc(benchmarkData.labels[row.model] || row.model)}</td>
      <td>${esc(row.engine)} · ${esc(row.precision)}<br><span class="registry-range">${row.gpu_count} accelerator${row.gpu_count === 1 ? "" : "s"} · ${esc(row.scenario)}</span></td>
      <td><span class="registry-throughput">${fmt(row.tokens_per_sec, 0)} tok/s</span><span class="registry-range">${fmt(row.tokens_per_sec_low, 0)}–${fmt(row.tokens_per_sec_high, 0)}</span></td>
      <td><span class="evidence-badge ${esc(row.classification)}">${esc(row.classification)}</span><span class="registry-range">${esc(row.confidence)} confidence</span></td>
      <td><a href="${esc(row.source.url)}" target="_blank" rel="noopener">${esc(row.source.publisher)} ↗</a><span class="registry-range">${esc(row.benchmark_date)}</span></td>
    </tr>`).join("") : `<tr><td colspan="6"><p class="intel-empty">No benchmark matches these filters. This gap is shown as unavailable rather than filled with a guess.</p></td></tr>`;

  const chartRows = rows.slice().sort((a, b) => b.tokens_per_sec - a.tokens_per_sec).slice(0, 12);
  const categories = chartRows.map((row) => `${row.gpu} · ${benchmarkData.labels[row.model] || row.model}`);
  const option = chartScaffold({ xType: "value", yType: "category", legend: false, grid: { left: 155, right: 24, top: 18, bottom: 35 } });
  option.xAxis = option.yAxis;
  option.xAxis.type = "value";
  option.xAxis.axisLabel.formatter = (value) => fmt(value, 0);
  option.yAxis = {
    type: "category", data: categories, inverse: true,
    axisLine: { lineStyle: { color: CHART_GRID } }, axisTick: { show: false },
    axisLabel: { color: CHART_TEXT, fontSize: 10, width: 142, overflow: "truncate" },
  };
  option.tooltip = {
    trigger: "item", backgroundColor: "#1a1f29", borderColor: "#2f3747", textStyle: { color: "#e8edf4" },
    formatter: (params) => {
      const row = params.data.row;
      return `<strong>${esc(row.gpu)} · ${esc(benchmarkData.labels[row.model])}</strong><br>${fmt(row.tokens_per_sec, 0)} tok/s · range ${fmt(row.tokens_per_sec_low, 0)}–${fmt(row.tokens_per_sec_high, 0)}<br>${esc(row.classification)} · ${esc(row.engine)} ${esc(row.precision)}`;
    },
  };
  option.series = [{
    type: "custom",
    renderItem: (params, api) => {
      const low = api.coord([api.value(0), api.value(3)]);
      const high = api.coord([api.value(1), api.value(3)]);
      const point = api.coord([api.value(2), api.value(3)]);
      const color = api.visual("color");
      return { type: "group", children: [
        { type: "line", shape: { x1: low[0], y1: low[1], x2: high[0], y2: high[1] }, style: { stroke: color, lineWidth: 5, opacity: 0.28, lineCap: "round" } },
        { type: "line", shape: { x1: low[0], y1: low[1] - 4, x2: low[0], y2: low[1] + 4 }, style: { stroke: color, lineWidth: 1 } },
        { type: "line", shape: { x1: high[0], y1: high[1] - 4, x2: high[0], y2: high[1] + 4 }, style: { stroke: color, lineWidth: 1 } },
        { type: "circle", shape: { cx: point[0], cy: point[1], r: 4 }, style: { fill: color, stroke: "#0a0c10", lineWidth: 2 } },
      ] };
    },
    encode: { x: [0, 1, 2], y: 3 },
    data: chartRows.map((row, index) => ({ value: [row.tokens_per_sec_low, row.tokens_per_sec_high, row.tokens_per_sec, index], row, itemStyle: { color: benchmarkColor(row.classification) } })),
  }];
  renderEChart("benchmarkRegistry", "chart-benchmark-registry", option);
}

async function loadHistory() {
  const gpu = document.getElementById("history-gpu").value;
  try {
    const resp = await fetch(`/api/prices/history?gpu=${encodeURIComponent(gpu)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    const byProvider = {};
    for (const s of data.snapshots) {
      (byProvider[s.provider] ??= []).push([s.fetched_at * 1000, s.price_per_hour]);
    }
    const option = chartScaffold({
      xType: "time",
      xFormatter: (v) => new Date(v).toLocaleDateString([], { month: "short", day: "numeric" }),
      grid: { top: 52 },
    });
    option.legend = { ...option.legend, type: "scroll" };
    option.dataZoom = [{ type: "inside", xAxisIndex: 0, filterMode: "none" }];
    option.series = Object.entries(byProvider).map(([provider, points], i) => ({
      name: provider,
      type: "line",
      data: points.sort((a, b) => a[0] - b[0]),
      showSymbol: false,
      connectNulls: false,
      sampling: "lttb",
      lineStyle: { width: 1.75, color: CHART_PALETTE[i % CHART_PALETTE.length] },
      emphasis: { focus: "series", lineStyle: { width: 2.5 } },
    }));
    renderEChart("history", "chart-history", option);
  } catch (err) {
    console.error("History failed:", err);
  }
}

// --- Historical prices -------------------------------------------------------------

let historicalData = null;

const HIST_LOG_TRACKS = new Set(["current_ai_sku", "enterprise_pre_llm"]);
const HIST_COLORS = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#bc8cff", "#ff7b72", "#7ee787", "#79c0ff", "#ffa657", "#d2a8ff"];

async function loadHistorical() {
  try {
    const resp = await fetch("/api/prices/historical");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    historicalData = await resp.json();
    renderHistorical();
  } catch (err) {
    console.error("Historical prices failed:", err);
    const desc = document.getElementById("hist-desc");
    if (desc) desc.textContent = "Historical data unavailable.";
  }
}

function histPointStyle(priceType) {
  if (priceType === "system_allocated_capex" || priceType === "oem_estimate") return "triangle";
  if (priceType === "retail_list") return "rect";
  return "circle";
}

function histTypeSuffix(priceType) {
  if (priceType === "system_allocated_capex") return " (system alloc.)";
  if (priceType === "oem_estimate") return " (est.)";
  return "";
}

function renderHistorical() {
  if (!historicalData) return;
  const track = document.getElementById("hist-track").value;
  const real = document.getElementById("hist-real").checked;
  const rows = historicalData.rows.filter((r) => r.track === track);

  // Group by sku so each line/scatter series is one SKU.
  const bySku = new Map();
  for (const r of rows) {
    const y = real ? r.usd_2026 : r.usd_nominal;
    if (y == null) continue;
    if (!bySku.has(r.sku)) bySku.set(r.sku, []);
    bySku.get(r.sku).push({
      value: [new Date(r.date).getTime(), y],
      sku: r.sku,
      price_type: r.price_type,
      confidence: r.confidence,
      period_label: r.period_label,
      symbol: histPointStyle(r.price_type),
    });
  }

  const series = [];
  let i = 0;
  for (const [sku, points] of bySku) {
    points.sort((a, b) => a.value[0] - b.value[0]);
    const color = HIST_COLORS[i % HIST_COLORS.length];
    series.push({
      name: sku,
      type: "line",
      data: points,
      showSymbol: true,
      symbolSize: 9,
      lineStyle: { width: points.length >= 2 ? 1.25 : 0, color, opacity: 0.75 },
      itemStyle: { color },
      emphasis: { focus: "series", scale: 1.35 },
    });
    i += 1;
  }

  const logY = HIST_LOG_TRACKS.has(track);
  const option = chartScaffold({
    xType: "time",
    yType: logY ? "log" : "value",
    xFormatter: (v) => String(new Date(v).getFullYear()),
    grid: { top: 62 },
  });
  option.legend = { ...option.legend, type: "scroll" };
  option.tooltip = {
    trigger: "item",
    backgroundColor: "#1a1f29",
    borderColor: "#2f3747",
    textStyle: { color: "#e8edf4" },
    formatter: (p) => {
      const point = p.data;
      return `<strong>${esc(point.sku)}</strong><br>${usd(point.value[1], 0)} · ${esc(point.period_label)}<br>${esc(point.price_type)}${histTypeSuffix(point.price_type)} · ${esc(point.confidence)}`;
    },
  };
  option.series = series;
  renderEChart("historical", "chart-historical", option);
}

// --- Decision intelligence ----------------------------------------------------

function setIntelLoading(button, loading, label) {
  if (!button) return;
  if (loading) {
    button.dataset.label = button.textContent;
    button.textContent = label;
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
  }
  button.disabled = loading;
}

function intelError(elementId, error) {
  const element = document.getElementById(elementId);
  if (element) element.innerHTML = `<p class="intel-error">${esc(error.message || error)}</p>`;
}

async function loadCollectionHealth() {
  const result = document.getElementById("collection-health");
  const pill = document.getElementById("collection-health-pill");
  const label = document.getElementById("collection-health-label");
  try {
    const resp = await fetch("/api/data-health");
    if (!resp.ok) throw new Error(`Health check returned HTTP ${resp.status}`);
    const data = await resp.json();
    const status = data.status || "no_data";
    pill.dataset.state = status;
    label.textContent = status === "healthy" ? "Market data healthy" : `Market data ${status.replace("_", " ")}`;

    const latest = data.latest_run || {};
    const providers = data.providers || [];
    const completed = latest.finished_at || latest.completed_at || latest.started_at;
    const age = completed ? Math.max(0, Date.now() / 1000 - completed) : null;
    const metrics = `
      <div class="intel-metrics">
        <div class="intel-metric"><span>Latest run</span><strong>${age === null ? "—" : age < 3600 ? `${Math.round(age / 60)}m ago` : `${(age / 3600).toFixed(1)}h ago`}</strong></div>
        <div class="intel-metric"><span>Providers</span><strong>${latest.successful_providers ?? data.successful_providers ?? 0}/${latest.expected_providers ?? data.expected_providers ?? providers.length}</strong></div>
        <div class="intel-metric"><span>Valid quotes</span><strong>${latest.quote_count ?? data.quote_count ?? 0}</strong></div>
      </div>`;
    const rows = providers.length
      ? `<table class="provider-health"><caption class="sr-only">Provider results for the latest collection run</caption><thead><tr><th>Provider</th><th>Status</th><th>Result</th></tr></thead><tbody>${providers.map((provider) => `
          <tr><td>${esc(provider.provider)}</td><td class="status-text-${esc(provider.status)}">${esc(provider.status)}</td><td>${provider.quote_count ?? 0} quotes · ${provider.duration_ms == null ? "—" : `${Math.round(provider.duration_ms)}ms`}</td></tr>`).join("")}</tbody></table>`
      : `<p class="intel-empty">No provider-level run has been recorded yet. The next scheduled collection will populate this view.</p>`;
    result.innerHTML = metrics + rows;
  } catch (error) {
    pill.dataset.state = "failed";
    label.textContent = "Health unavailable";
    intelError("collection-health", error);
  }
}

function updateAlertThreshold() {
  const type = document.getElementById("alert-type").value;
  const label = document.getElementById("alert-threshold-label");
  const text = document.getElementById("alert-threshold-text");
  const input = document.getElementById("alert-threshold");
  const config = {
    price_below: ["Threshold ($/GPU-hr)", "2.00", "0.05"],
    price_change_pct: ["Change threshold (%)", "10", "1"],
    savings_above: ["Savings threshold ($)", "50000", "1000"],
  }[type];
  label.hidden = type === "recommendation_change";
  if (config) {
    text.textContent = config[0];
    input.value = config[1];
    input.step = config[2];
  }
}

async function evaluateWorkload() {
  const button = document.getElementById("evaluate-workload");
  setIntelLoading(button, true, "Evaluating…");
  const body = {
    profile: document.getElementById("workload-profile").value,
    model: document.getElementById("workload-model").value,
    average_input_tokens: parseWholeNumber(document.getElementById("workload-input-tokens").value, 1024),
    average_output_tokens: parseWholeNumber(document.getElementById("workload-output-tokens").value, 256),
    peak_requests_per_second: parseNumber(document.getElementById("workload-rps").value, 2),
    latency_target_seconds: document.getElementById("workload-latency").value === ""
      ? null
      : parseNumber(document.getElementById("workload-latency").value, 2),
    capacity_headroom: parsePercent(document.getElementById("capacity_headroom").value, 15),
  };
  try {
    const resp = await fetch("/api/workloads/evaluate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
    const data = await resp.json();
    const evaluations = data.evaluations || data.results || [];
    document.getElementById("workload-result").innerHTML = `
      <div class="compatibility-list">${evaluations.map((item) => {
        const compatible = item.compatible !== false;
        const evidenceAvailable = item.performance_evidence_available !== false;
        const throughput = item.effective_tokens_per_sec ?? item.tokens_per_sec;
        const detail = compatible && evidenceAvailable
          ? `${throughput == null ? "Throughput unavailable" : `${fmt(throughput, 0)} effective tok/s`} · ${esc(item.confidence || "unknown confidence")} · ${esc(item.provenance || item.benchmark_kind || "estimated")}`
          : compatible
            ? `Fits the registered memory and context limits · speed benchmark not yet available`
          : esc(item.reason || (item.reasons || []).join(" ") || "Does not meet this workload");
        const state = !compatible ? "Excluded" : evidenceAvailable ? "Compatible" : "Fits memory";
        return `<div class="compatibility-row"><strong>${esc(item.gpu)}</strong><span class="compatibility-state ${compatible ? "pass" : "fail"}">${state}</span><span class="compatibility-detail">${detail}</span></div>`;
      }).join("")}</div>
      ${data.note ? `<p class="decision-note">${esc(data.note)}</p>` : ""}`;
  } catch (error) {
    intelError("workload-result", error);
  } finally {
    setIntelLoading(button, false);
  }
}

async function loadWorkloadCatalog() {
  try {
    const resp = await fetch("/api/workloads");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    workloadProfiles = new Map((data.profiles || []).map((profile) => [profile.id, profile]));
    const select = document.getElementById("workload-profile");
    select.innerHTML = "";
    for (const profile of data.profiles || []) {
      const option = document.createElement("option");
      option.value = profile.id;
      option.textContent = profile.label;
      select.appendChild(option);
    }
    const modelSelect = document.getElementById("workload-model");
    modelSelect.innerHTML = "";
    const models = [...(data.models || [])].sort((a, b) =>
      String(b.released_date || "").localeCompare(String(a.released_date || "")) || a.label.localeCompare(b.label)
    );
    for (const model of models) {
      const option = document.createElement("option");
      option.value = model.id;
      const active = model.architecture === "moe" && model.active_parameters_b != null
        ? ` · ${model.active_parameters_b}B active`
        : "";
      option.textContent = `${model.label}${active} · ${model.released_date || "date unknown"}`;
      modelSelect.appendChild(option);
    }
    const applyProfile = () => {
      const profile = workloadProfiles.get(select.value);
      if (!profile) return;
      document.getElementById("workload-model").value = profile.model;
      document.getElementById("workload-input-tokens").value = profile.input_tokens;
      document.getElementById("workload-output-tokens").value = profile.output_tokens;
      document.getElementById("workload-latency").value = profile.max_latency_seconds ?? "";
      document.getElementById("workload-rps").value = profile.max_latency_seconds
        ? String(profile.concurrent_requests / profile.max_latency_seconds)
        : profile.concurrent_requests;
    };
    select.addEventListener("change", applyProfile);
    applyProfile();
  } catch (error) {
    console.error("Workload catalog failed:", error);
  }
}

function renderBacktestChart(points) {
  if (!points?.length) return;
  const option = chartScaffold({ xType: "time", yFormatter: usd, grid: { top: 44 } });
  option.series = [
    { name: "Fixed choice", type: "line", data: points.map((p) => [p.timestamp * 1000, p.fixed_cost]), showSymbol: false, lineStyle: { width: 2 } },
    { name: "Hindsight best", type: "line", data: points.map((p) => [p.timestamp * 1000, p.hindsight_cost]), showSymbol: false, lineStyle: { width: 1.5, type: "dashed" } },
  ];
  renderEChart("backtest", "chart-backtest", option);
}

async function runBacktest() {
  const button = document.getElementById("run-backtest");
  setIntelLoading(button, true, "Replaying…");
  const request = buildRequest();
  const gpu = document.getElementById("backtest-gpu").value;
  const gpuInput = request.gpus.find((item) => item.name === gpu) || request.gpus[0];
  try {
    const resp = await fetch("/api/backtests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gpu,
        decision_at: new Date(`${document.getElementById("backtest-date").value}:00Z`).getTime() / 1000,
        horizon_hours: parseNumber(document.getElementById("backtest-window").value, 168),
        utilization: request.workload.utilization,
        owner_cost_per_hour: gpuInput ? null : 0,
        scenario: request,
      }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
    const data = await resp.json();
    document.getElementById("backtest-result").innerHTML = `
      <div class="intel-metrics">
        <div class="intel-metric"><span>Original choice</span><strong>${esc(data.original_option || data.chosen_option || "—")}</strong></div>
        <div class="intel-metric"><span>Realized cost</span><strong>${data.realized_cost == null ? "Incomplete" : usdCompact(data.realized_cost)}</strong></div>
        <div class="intel-metric"><span>Hindsight best</span><strong>${data.hindsight_best_cost == null ? "Incomplete" : usdCompact(data.hindsight_best_cost)}</strong></div>
        <div class="intel-metric"><span>Regret</span><strong>${data.regret == null ? "Incomplete" : usdCompact(data.regret)}</strong></div>
        <div class="intel-metric"><span>Coverage</span><strong>${pct(data.coverage || 0)}</strong></div>
      </div>
      ${data.incomplete ? `<p class="intel-error">Incomplete result: ${esc(data.coverage_note || "market history has material gaps")}</p>` : ""}`;
    renderBacktestChart(data.points || []);
  } catch (error) {
    intelError("backtest-result", error);
  } finally {
    setIntelLoading(button, false);
  }
}

async function loadAlerts() {
  try {
    const resp = await fetch("/api/alerts");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const rules = data.rules || [];
    if (!rules.length) return;
    document.getElementById("alert-result").innerHTML = `<div class="compatibility-list">${rules.slice(0, 5).map((rule) => `
      <div class="compatibility-row"><strong>${esc(rule.gpu)}</strong><span class="compatibility-state ${rule.active ? "pass" : "fail"}">${rule.active ? "Watching" : "Paused"}</span><span class="compatibility-detail">${esc(rule.description || rule.alert_type)}${rule.threshold == null ? "" : ` · ${fmt(rule.threshold)}`}<br><span class="delivery-state ${esc(rule.latest_delivery?.status || "")}">${esc(rule.delivery_channel === "in_app" ? "in-app" : `${rule.delivery_channel} · ${rule.delivery_target_hint || "configured"}`)}${rule.latest_delivery ? ` · ${esc(rule.latest_delivery.status)}` : ""}</span></span></div>`).join("")}</div>`;
  } catch (error) {
    intelError("alert-result", error);
  }
}

async function loadDeliveryCapabilities() {
  try {
    const resp = await fetch("/api/alerts/delivery-capabilities");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const capabilities = await resp.json();
    const emailOption = document.querySelector("#alert-delivery-channel option[value='email']");
    const webhookOption = document.querySelector("#alert-delivery-channel option[value='webhook']");
    emailOption.disabled = !capabilities.email || !capabilities.external_delivery_configured;
    emailOption.textContent = capabilities.email ? "Email" : "Email (SMTP setup required)";
    webhookOption.disabled = !capabilities.external_delivery_configured;
    webhookOption.textContent = capabilities.external_delivery_configured
      ? "Signed webhook"
      : "Signed webhook (operator setup required)";
  } catch (error) {
    console.error("Delivery capabilities failed:", error);
  }
}

function updateDeliveryFields() {
  const channel = document.getElementById("alert-delivery-channel").value;
  const label = document.getElementById("alert-delivery-target-label");
  const text = document.getElementById("alert-delivery-target-text");
  const input = document.getElementById("alert-delivery-target");
  const tokenLabel = document.getElementById("alert-delivery-token-label");
  const tokenInput = document.getElementById("alert-delivery-token");
  const note = document.getElementById("alert-delivery-note");
  label.hidden = channel === "in_app";
  tokenLabel.hidden = channel === "in_app";
  if (!tokenInput.value) tokenInput.value = sessionStorage.getItem("gpu-alert-token") || "";
  if (channel === "email") {
    text.textContent = "Email address";
    input.type = "email";
    input.placeholder = "you@example.com";
    note.textContent = "Email is queued and retried by the VPS collector.";
  } else if (channel === "webhook") {
    text.textContent = "HTTPS webhook URL";
    input.type = "url";
    input.placeholder = "https://example.com/gpu-alerts";
    note.textContent = "Requests are signed with HMAC-SHA256. The secret is shown once.";
  } else {
    input.value = "";
    tokenInput.value = "";
    note.textContent = "Stored in the dashboard event history.";
  }
}

async function createAlert() {
  const button = document.getElementById("create-alert");
  setIntelLoading(button, true, "Creating…");
  const type = document.getElementById("alert-type").value;
  const deliveryChannel = document.getElementById("alert-delivery-channel").value;
  const deliveryToken = document.getElementById("alert-delivery-token").value;
  const secretBox = document.getElementById("webhook-secret");
  secretBox.hidden = true;
  secretBox.textContent = "";
  try {
    const resp = await fetch("/api/alerts", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(deliveryChannel === "in_app" ? {} : { "X-Alert-Token": deliveryToken }),
      },
      body: JSON.stringify({
        gpu: document.getElementById("alert-gpu").value,
        alert_type: type,
        threshold: type === "recommendation_change" ? null : parseNumber(document.getElementById("alert-threshold").value, 0),
        required_observations: parseWholeNumber(document.getElementById("alert-confirmations").value, 3),
        cooldown_hours: parseNumber(document.getElementById("alert-cooldown").value, 24),
        scenario: buildRequest(),
        delivery_channel: deliveryChannel,
        delivery_target: document.getElementById("alert-delivery-target").value.trim(),
      }),
    });
    const created = await resp.json();
    if (!resp.ok) throw new Error(created.detail || `HTTP ${resp.status}`);
    if (deliveryChannel !== "in_app") {
      sessionStorage.setItem("gpu-alert-token", deliveryToken);
    }
    if (created.webhook_signing_secret) {
      secretBox.textContent = `Signing secret — copy now: ${created.webhook_signing_secret}`;
      secretBox.hidden = false;
    }
    await loadAlerts();
  } catch (error) {
    intelError("alert-result", error);
  } finally {
    setIntelLoading(button, false);
  }
}

// --- API + debounce ------------------------------------------------------------

async function recompute() {
  const loading = document.getElementById("loading");
  loading.classList.add("visible");
  try {
    const resp = await fetch("/compute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildRequest()),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderResults(data);
    syncUrl();
    updateModifiedIndicator();
  } catch (err) {
    console.error("Compute failed:", err);
  } finally {
    loading.classList.remove("visible");
  }
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// --- Wire up controls ----------------------------------------------------------

function wireControl(id) {
  document.getElementById(id).addEventListener("input", debounce(recompute, 300));
  // Any manual edit to a shared control drops the preset back to Custom.
  document.getElementById(id).addEventListener("input", () => {
    const preset = document.getElementById("scenario-preset");
    if (preset) preset.value = "custom";
  });
}

// --- Scenario presets, modified indicator, sparklines, active nav --------------

const SHARED_CONTROL_IDS = ["power_cost", "pue", "opex_frac", "utilization", "od_price",
  "res_price", "res_term", "fleet_size", "rent_horizon", "monthly_demand", "capacity_headroom"];

// A signature of everything a user can tweak (shared controls + GPU editor rows),
// excluding live rental prices which change on their own.
function scenarioSignature() {
  const controls = SHARED_CONTROL_IDS.map((id) => document.getElementById(id).value);
  const gpus = Array.from(document.querySelectorAll(".gpu-input-row")).map((row) =>
    Array.from(row.querySelectorAll("input")).map((inp) => inp.value)
  );
  return JSON.stringify({ controls, gpus });
}

function updateModifiedIndicator() {
  const badge = document.getElementById("nav-modified");
  if (!badge || defaultRequestSnapshot == null) return;
  badge.hidden = scenarioSignature() === defaultRequestSnapshot;
}

function applyPreset(name) {
  const preset = SCENARIO_PRESETS[name];
  if (!preset) return; // "custom" is a no-op
  for (const [id, value] of Object.entries(preset)) {
    const el = document.getElementById(id);
    if (el) el.value = value;
  }
  recompute(); // recompute() also syncs the URL and refreshes the indicator
}

function resetToDefaults() {
  for (const [id, value] of Object.entries(defaultControlValues)) {
    const el = document.getElementById(id);
    if (el) el.value = value;
  }
  // Re-render the whole GPU editor from the pristine defaults so renamed or
  // URL-provided rows are fully restored, not just value-matched by name.
  renderGpuEditor({ gpus: defaultGpus.map((g) => ({ ...g })) });
  const preset = document.getElementById("scenario-preset");
  if (preset) preset.value = "custom";
  recompute();
}

function renderSparkline(gpu, key) {
  fetch(`/api/prices/history?gpu=${encodeURIComponent(gpu)}&hours=168`)
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (!data) return;
      // Twelve-hour market-floor buckets preserve real price steps while preventing
      // intermittent provider availability from becoming visual chatter.
      const bucketSeconds = 12 * 3600;
      const byBucket = new Map();
      for (const s of data.snapshots) {
        const bucket = Math.floor(s.fetched_at / bucketSeconds) * bucketSeconds;
        const cur = byBucket.get(bucket);
        if (cur == null || s.price_per_hour < cur) byBucket.set(bucket, s.price_per_hour);
      }
      const points = [...byBucket.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([timestamp, price]) => ({ x: timestamp * 1000, y: price }));
      if (points.length < 2) return;
      const spark = document.querySelector(`.kpi-spark[data-gpu="${gpu}"]`);
      if (!spark) return;

      const first = points[0].y;
      const latest = points[points.length - 1].y;
      const change = first === 0 ? 0 : (latest - first) / first;
      const direction = change < -0.005 ? "down" : change > 0.005 ? "up" : "flat";
      const color = direction === "down" ? "#3fd68f" : direction === "up" ? "#e8a33d" : "#4d9fff";
      const values = points.map((p) => p.y);
      const low = Math.min(...values);
      const high = Math.max(...values);
      // A minimum 3% visual pad prevents tiny price noise from filling the plot.
      const yPad = Math.max((high - low) * 0.2, latest * 0.03, 0.01);

      const meta = document.querySelector(`.kpi-spark-meta[data-gpu="${gpu}"] strong`);
      if (meta) {
        meta.className = direction;
        meta.textContent = `${change > 0 ? "+" : ""}${pct(change)} · ${points.length} pts`;
      }

      const chartKey = `spark${key}`;
      const areaColor = direction === "down"
        ? "rgba(63,214,143,0.13)"
        : direction === "up" ? "rgba(232,163,61,0.12)" : "rgba(77,159,255,0.11)";
      renderEChart(chartKey, spark.id, {
        animation: false,
        grid: { left: 0, right: 2, top: 4, bottom: 1 },
        tooltip: {
          trigger: "axis",
          confine: true,
          backgroundColor: "#1a1f29",
          borderColor: "#2f3747",
          textStyle: { color: "#e8edf4", fontSize: 11 },
          formatter: (params) => {
            const p = params[0].value;
            return `${new Date(p[0]).toLocaleString([], { weekday: "short", hour: "numeric" })}<br><strong>${usd(p[1])}/hr</strong>`;
          },
        },
        xAxis: { type: "value", show: false, min: points[0].x, max: points[points.length - 1].x },
        yAxis: { type: "value", show: false, min: Math.max(0, low - yPad), max: high + yPad },
        series: [
          {
            name: `${gpu} market floor`,
            type: "line",
            data: points.map((p) => [p.x, p.y]),
            showSymbol: false,
            smooth: false,
            lineStyle: { color, width: 1.75 },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: areaColor },
                { offset: 1, color: "rgba(18,21,28,0)" },
              ]),
            },
          },
          {
            name: "Latest",
            type: "scatter",
            data: [[points[points.length - 1].x, latest]],
            symbolSize: 5,
            itemStyle: { color, borderColor: "#12151c", borderWidth: 1.5 },
            tooltip: { show: false },
          },
        ],
      });
    })
    .catch((err) => console.error(`Sparkline ${gpu} failed:`, err));
}

function renderSparklines() {
  ["H100", "H200", "B200"].forEach((gpu) => renderSparkline(gpu, gpu));
}

function observeSections() {
  const links = new Map(
    [...document.querySelectorAll(".section-nav a[href^='#']")].map((a) => [a.getAttribute("href").slice(1), a])
  );
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        for (const a of links.values()) a.classList.remove("active");
        const active = links.get(entry.target.id);
        if (active) active.classList.add("active");
      }
    },
    { rootMargin: "-40% 0px -55% 0px", threshold: 0 }
  );
  for (const id of links.keys()) {
    const el = document.getElementById(id);
    if (el) observer.observe(el);
  }
}

// --- Init -----------------------------------------------------------------------

async function init() {
  try {
    const resp = await fetch("/defaults");
    const defaults = await resp.json();

    // Populate shared controls
    document.getElementById("power_cost").value = defaults.datacenter.power_cost_per_kwh;
    document.getElementById("pue").value = defaults.datacenter.pue;
    document.getElementById("opex_frac").value = defaults.datacenter.opex_frac_of_capex_per_year * 100;
    document.getElementById("utilization").value = defaults.workload.utilization * 100;
    document.getElementById("od_price").value = defaults.workload.on_demand_price_per_gpu_hour;
    document.getElementById("res_price").value = defaults.workload.reserved_price_per_gpu_hour;
    document.getElementById("res_term").value = defaults.workload.reserved_term_months;
    document.getElementById("fleet_size").value = defaults.fleet_size;
    document.getElementById("monthly_demand").value = defaults.monthly_token_demand / 1e9;
    document.getElementById("capacity_headroom").value = defaults.capacity_headroom * 100;

    // Snapshot the pristine scenario (shared controls + GPU specs) BEFORE any
    // URL overrides mutate defaults.gpus. Clone the array so it stays pristine.
    defaultGpus = defaults.gpus.map((g) => ({ ...g }));
    renderGpuEditor(defaults);
    defaultControlValues = {};
    SHARED_CONTROL_IDS.forEach((id) => { defaultControlValues[id] = document.getElementById(id).value; });
    defaultRequestSnapshot = scenarioSignature();

    // Restore any shared-scenario state from the URL (overrides defaults +
    // mutates defaults.gpus), then re-render the editor so ?gpu=... shows up.
    restoreFromUrl(defaults);
    renderGpuEditor(defaults);

    // Wire shared controls
    SHARED_CONTROL_IDS.forEach(wireControl);

    // Scenario preset + reset + active-section nav
    document.getElementById("scenario-preset").addEventListener("change", (e) => applyPreset(e.target.value));
    document.getElementById("nav-reset").addEventListener("click", (e) => { e.preventDefault(); resetToDefaults(); });
    observeSections();
    window.addEventListener("resize", debounce(resizeCharts, 100));

    // Initial compute + live market data (independent, non-blocking)
    recompute();
    document.getElementById("history-gpu").addEventListener("change", loadHistory);
    document.getElementById("region-gpu").addEventListener("change", loadRegions);
    loadLivePrices().then(() => { loadHistory(); loadRegions(); renderSparklines(); });
    loadPowerPrices();
    loadBenchmarks();
    loadHistorical();
    loadTokenPrices();
    document.getElementById("hist-track").addEventListener("change", renderHistorical);
    document.getElementById("hist-real").addEventListener("change", renderHistorical);

    // Decision intelligence controls are intentionally explicit actions: the
    // user can change assumptions without firing expensive historical queries.
    const oneWeekAgo = new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString().slice(0, 16);
    document.getElementById("backtest-date").value = oneWeekAgo;
    document.getElementById("evaluate-workload").addEventListener("click", evaluateWorkload);
    document.getElementById("refresh-health").addEventListener("click", loadCollectionHealth);
    document.getElementById("run-backtest").addEventListener("click", runBacktest);
    document.getElementById("create-alert").addEventListener("click", createAlert);
    document.getElementById("alert-type").addEventListener("change", updateAlertThreshold);
    document.getElementById("alert-delivery-channel").addEventListener("change", updateDeliveryFields);
    updateAlertThreshold();
    updateDeliveryFields();
    loadCollectionHealth();
    loadWorkloadCatalog();
    loadDeliveryCapabilities();
    loadAlerts();
  } catch (err) {
    console.error("Init failed:", err);
  }
}

init();
