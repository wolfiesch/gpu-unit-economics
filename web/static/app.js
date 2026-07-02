"use strict";

// --- State ---------------------------------------------------------------------

let charts = {};
let liveRentalPrices = {}; // canonical GPU name -> cheapest live $/hr, set by loadLivePrices

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
  };
}

// --- Shareable URL state ---------------------------------------------------------

// Short query keys for the shared controls.
const URL_FIELDS = {
  pc: "power_cost", pue: "pue", opex: "opex_frac", util: "utilization",
  od: "od_price", res: "res_price", term: "res_term", fleet: "fleet_size",
  hor: "rent_horizon",
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
  renderHourly(data.results);
  renderTokens(data.token_ranking, data.results);
  renderMargin(data.results);
  renderDepreciation(data.results);
  renderBreakEven(data.results);
  renderRentVsBuy(data.results);
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

  // Chart
  if (charts.margin) charts.margin.destroy();
  charts.margin = new Chart(document.getElementById("chart-margin"), {
    type: "bar",
    data: {
      labels: results.map((r) => r.name),
      datasets: [
        { label: "Price/hr", data: results.map((r) => r.margin.price_per_billable_hour), backgroundColor: "#1f6feb" },
        { label: "Cost/hr", data: results.map((r) => r.margin.cost_per_billable_hour), backgroundColor: "#f85149" },
      ],
    },
    options: chartOpts("$/billable-hour"),
  });
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

  // Chart - depreciation sensitivity lines per GPU
  if (charts.depreciation) charts.depreciation.destroy();
  const colors = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#bc8cff"];
  charts.depreciation = new Chart(document.getElementById("chart-depreciation"), {
    type: "line",
    data: {
      labels: ["3 yr", "4 yr", "5 yr", "6 yr"],
      datasets: results.map((r, i) => ({
        label: r.name,
        data: r.depreciation_sensitivity.map((s) => s.provisioned_cost),
        borderColor: colors[i % colors.length],
        backgroundColor: colors[i % colors.length] + "20",
        tension: 0.1,
      })),
    },
    options: chartOpts("$/provisioned-hour"),
  });
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
  if (charts.breakeven) charts.breakeven.destroy();
  charts.breakeven = new Chart(document.getElementById("chart-breakeven"), {
    type: "line",
    data: {
      labels: first.break_even_curve.map((c) => pct(c.utilization)),
      datasets: [
        { label: "Reserved (flat)", data: first.break_even_curve.map((c) => c.reserved_total_cost), borderColor: "#58a6ff", tension: 0 },
        { label: "On-demand", data: first.break_even_curve.map((c) => c.on_demand_total_cost), borderColor: "#f85149", tension: 0 },
      ],
    },
    options: chartOpts("Total cost over term ($)"),
  });
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
    kpi.append(label, value, sub);
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

  if (charts.rentbuy) charts.rentbuy.destroy();
  const labels = results[0].rent_vs_buy_curve.map((p) => pct(p.utilization));
  charts.rentbuy = new Chart(document.getElementById("chart-rentbuy"), {
    type: "line",
    data: {
      labels,
      datasets: results.flatMap((r, i) => {
        const hue = ["#58a6ff", "#3fb950", "#d29922"][i % 3];
        return [
          { label: `${r.name} rent`, data: r.rent_vs_buy_curve.map((p) => p.rent_total_cost), borderColor: hue, borderDash: [5, 4], pointRadius: 0 },
          { label: `${r.name} own`, data: r.rent_vs_buy_curve.map((p) => p.own_total_cost), borderColor: hue, pointRadius: 0 },
        ];
      }),
    },
    options: chartOpts("Total cost over horizon ($)"),
  });
}

// --- Regional prices & arbitrage ---------------------------------------------------

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

    // Spread-over-time chart: min/max band per snapshot batch.
    const labels = spread.batches.map((b) => new Date(b.fetched_at * 1000).toLocaleString());
    if (charts.spread) charts.spread.destroy();
    charts.spread = new Chart(document.getElementById("chart-spread"), {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: `${gpu} cheapest region`, data: spread.batches.map((b) => b.min_price),
            borderColor: "#3fb950", pointRadius: 2, tension: 0.2 },
          { label: `${gpu} priciest region`, data: spread.batches.map((b) => b.max_price),
            borderColor: "#f85149", pointRadius: 2, tension: 0.2, fill: "-1",
            backgroundColor: "rgba(248, 81, 73, 0.08)" },
        ],
      },
      options: chartOpts(`${gpu} $/GPU-hr across regions`),
    });
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
        `Applied ${data.vintage} figures (${kinds}). ` +
        `MLPerf-derived rows trace to verified closed-division results; ` +
        `illustrative rows are scaled estimates. Verify: ${data.mlperf_portal}`;
      note.style.display = "";
      recompute();
    });
    document.getElementById("bench-label").style.display = "";
  } catch (err) {
    console.error("Benchmarks failed:", err);
  }
}

async function loadHistory() {
  const gpu = document.getElementById("history-gpu").value;
  try {
    const resp = await fetch(`/api/prices/history?gpu=${encodeURIComponent(gpu)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    // Shared timeline: providers can start/stop at different batches, so a
    // category axis needs one label list with nulls where a provider is absent.
    const timestamps = [...new Set(data.snapshots.map((s) => s.fetched_at))].sort((a, b) => a - b);
    const labels = timestamps.map((t) => new Date(t * 1000).toLocaleString());
    const index = new Map(timestamps.map((t, i) => [t, i]));

    const byProvider = {};
    for (const s of data.snapshots) {
      const arr = (byProvider[s.provider] ??= new Array(timestamps.length).fill(null));
      arr[index.get(s.fetched_at)] = s.price_per_hour;
    }
    const colors = ["#58a6ff", "#3fb950", "#d29922", "#f85149"];
    const datasets = Object.entries(byProvider).map(([provider, points], i) => ({
      label: provider,
      data: points,
      borderColor: colors[i % colors.length],
      backgroundColor: colors[i % colors.length],
      tension: 0.2,
      pointRadius: 2,
      spanGaps: true,
    }));

    if (charts.history) charts.history.destroy();
    charts.history = new Chart(document.getElementById("chart-history"), {
      type: "line",
      data: { labels, datasets },
      options: chartOpts(`${gpu} $/GPU-hr`),
    });
  } catch (err) {
    console.error("History failed:", err);
  }
}

// --- Chart defaults ------------------------------------------------------------

function chartOpts(yLabel) {
  return {
    responsive: true,
    plugins: {
      legend: { labels: { color: "#8a94a6", font: { size: 11 }, boxWidth: 12, boxHeight: 12 } },
      tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${usdCompact(ctx.parsed.y)}` } },
    },
    scales: {
      x: { ticks: { color: "#5c6675", font: { size: 10 } }, grid: { color: "#1a1f29" } },
      y: { ticks: { color: "#5c6675", font: { size: 10 }, callback: (v) => usdCompact(v) }, grid: { color: "#1a1f29" } },
    },
  };
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

    // Restore any shared-scenario state from the URL (overrides defaults)
    restoreFromUrl(defaults);

    // Render GPU editor
    renderGpuEditor(defaults);

    // Wire shared controls
    ["power_cost", "pue", "opex_frac", "utilization", "od_price", "res_price", "res_term",
     "fleet_size", "rent_horizon"].forEach(wireControl);

    // Initial compute + live market data (independent, non-blocking)
    recompute();
    document.getElementById("history-gpu").addEventListener("change", loadHistory);
    document.getElementById("region-gpu").addEventListener("change", loadRegions);
    loadLivePrices().then(() => { loadHistory(); loadRegions(); });
    loadPowerPrices();
    loadBenchmarks();
  } catch (err) {
    console.error("Init failed:", err);
  }
}

init();
