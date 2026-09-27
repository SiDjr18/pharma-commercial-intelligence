"use strict";
/* ==========================================================================
   Pharma Commercial Intelligence — product UI (M11)

   Data contract: this script talks ONLY to the same-origin whitelisted tool API
   (POST /api/tools/<name> -> M8 ToolRegistry). It never computes a business metric:
   every number is displayed exactly as returned by the validated M4–M7 engines.
   The UI only formats, sorts, filters and selects rows, and scales chart geometry.

   Sections: 1 utilities · 2 formatting · 3 tool client · 4 state & router · 5 UI primitives
             6 charts · 7 pages (overview, market, segments, product, company, opportunity,
             scenario, methodology) · 8 init
   ========================================================================== */

// ------------------------------------------------------------------ 1. UTILITIES
const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const byDesc = (k) => (a, b) => (isNum(b[k]) ? b[k] : -Infinity) - (isNum(a[k]) ? a[k] : -Infinity);
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthLabel = (iso) => { if (!iso) return "—"; const [y, m] = String(iso).split("-"); return `${MON[+m - 1]} ${y}`; };
const shortMonth = (iso) => { const [y, m] = String(iso).split("-"); return `${MON[+m - 1]} ’${y.slice(2)}`; };
const windowLabel = (p) => (p && p.cur_start ? `${monthLabel(p.cur_start)} – ${monthLabel(p.cur_end)}` : "");
const priorLabel = (p) => (p && p.prior_start ? `${monthLabel(p.prior_start)} – ${monthLabel(p.prior_end)}` : "no comparison window");
const trunc = (s, px) => { s = String(s ?? ""); const n = Math.max(4, Math.floor(px / 6.4)); return s.length > n ? `${s.slice(0, n - 1)}…` : s; };
const store = {
  get(k) { try { return window.localStorage.getItem(`pci.${k}`); } catch (e) { return null; } },
  set(k, v) { try { window.localStorage.setItem(`pci.${k}`, v); } catch (e) { /* storage unavailable: preference not kept */ } },
};

// ------------------------------------------------------------------ 2. FORMATTING (text only; never derives a metric)
// adaptive precision: a small non-zero value must never be displayed as if it were zero
const adp = (v, d) => { let k = d; while (v && k < 6 && Math.abs(v) < 0.5 * 10 ** -k) k += 1; return k; };
const dp = (v) => { const a = Math.abs(v); return a >= 1000 ? 0 : a >= 10 ? 1 : a >= 0.1 ? 2 : adp(v, 3); };
const loc = (v, d) => v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (v, d, suffix) => { const s = Math.abs(v).toFixed(d); const z = Number(s) === 0; return `${z ? "" : v > 0 ? "+" : "−"}${s}${suffix}`; };
const T = {
  cr: (v) => (isNum(v) ? `₹${loc(v, dp(v))} cr` : "n/a"),
  crAbs: (v) => (isNum(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${loc(Math.abs(v), dp(v))} cr` : "n/a"),
  k: (v) => (isNum(v) ? `${loc(v, Math.abs(v) >= 100 ? 0 : 1)}k` : "n/a"),
  kAbs: (v) => (isNum(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${loc(Math.abs(v), Math.abs(v) >= 100 ? 0 : 1)}k` : "n/a"),
  num: (v, d = 1) => (isNum(v) ? loc(v, d) : "n/a"),
  pct: (v, d = 1) => (isNum(v) ? signed(v, adp(v, d), "%") : "n/a"),
  share: (v, d = 2) => (isNum(v) ? `${v.toFixed(adp(v, d))}%` : "n/a"),
  pp: (v, d = 2) => (isNum(v) ? signed(v, adp(v, d), " pp") : "n/a"),
  ei: (v) => (isNum(v) ? v.toFixed(1) : "n/a"),
  score: (v) => (isNum(v) ? v.toFixed(1) : "not scored"),
  rs: (v) => (isNum(v) ? `₹${loc(v, 2)}` : "n/a"),
  rsAbs: (v) => (isNum(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${loc(Math.abs(v), 2)}` : "n/a"),
  int: (v) => (isNum(v) ? loc(v, 0) : "n/a"),
};
const NA_REASON = {
  prior_unavailable: "No comparison period: the window a year earlier predates the data (June 2021).",
  prior_zero: "Not defined: there were no sales in the comparison period.",
  no_sales: "No sales in either period.",
};
const na = (reason) => `<span class="na" title="${esc(reason || "Not available for this window")}">n/a</span>`;
function delta(v, fmt = T.pct, reason) {
  if (!isNum(v)) return na(reason);
  const t = fmt(v);
  const cls = t.startsWith("+") ? "pos" : t.startsWith("−") ? "neg" : "flat";
  const arr = cls === "pos" ? "▲" : cls === "neg" ? "▼" : "■";
  return `<span class="delta ${cls}"><span class="arr" aria-hidden="true">${arr}</span>${esc(t)}</span>`;
}
const g = (r) => delta(r.value_growth_pct, T.pct, NA_REASON[r.value_growth_status]);
const val = (v, fmt) => (isNum(v) ? esc(fmt(v)) : na());
const scoreClass = (s) => (!isNum(s) ? "" : s < 20 ? "sc1" : s < 40 ? "sc2" : s < 60 ? "sc3" : s < 80 ? "sc4" : "sc5");
const LEVEL_LABEL = {
  total: "Total market", supergroup: "Therapy area", therapy_group: "Therapy group", subgroup: "Therapy subgroup",
  molecule: "Molecule", company: "Company", manufacturer: "Manufacturer", product: "Product (product code)",
  product_subgroup: "Product in subgroup", acute_chronic: "Acute / chronic", indian_mnc: "Indian / MNC",
  plain_combination: "Plain / combination", molecule_count: "Molecule count", dosage_form: "Dosage form", nfc1: "NFC1 form class",
};
const lvl = (k) => LEVEL_LABEL[k] || k;
const BASIS_SHORT = { MONTH: "Month", YTD: "YTD", MAT: "MAT" };

// ------------------------------------------------------------------ 3. TOOL CLIENT (whitelisted tools only; cached, de-duplicated)
async function fetchTool(name, params) {
  let res;
  try {
    res = await fetch(`/api/tools/${name}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(params) });
  } catch (e) {
    throw { code: "NETWORK", message: "The local analytics service did not respond." };
  }
  let body;
  try { body = await res.json(); } catch (e) { throw { code: "INTERNAL_ERROR", message: "The response could not be read." }; }
  if (!body.ok) throw body.error;
  return body.result;
}
const _cache = new Map();
function tool(name, params = {}) {
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== ""));
  const key = `${name}|${JSON.stringify(clean)}`;
  if (_cache.has(key)) return _cache.get(key);
  const p = fetchTool(name, clean);
  _cache.set(key, p);
  p.catch(() => _cache.delete(key));                 // errors are not cached
  if (_cache.size > 140) _cache.delete(_cache.keys().next().value);
  return p;
}

// ------------------------------------------------------------------ 4. STATE & ROUTER
const state = { meta: null, anchor: null, basis: "MAT", page: null, params: {}, areas: [], trendMetric: "value" };
const gp = () => ({ anchor: state.anchor, basis: state.basis });
const oppBasis = () => (state.basis === "MONTH" ? "MAT" : state.basis);
const periodSig = () => `${state.anchor}|${state.basis}`;
const PAGE_TITLE = {
  overview: "Executive Overview", market: "Market Intelligence", therapy: "Segments & mix", product: "Brand & Portfolio",
  company: "Company Intelligence", opportunity: "Opportunity Intelligence", scenario: "Scenario Planning",
  method: "Methodology & Data Quality", nl: "AI Analyst",
};

function parseHash() {
  const h = location.hash.replace(/^#\/?/, "");
  const [page, qs] = h.split("?");
  return { page: PAGE_TITLE[page] ? page : "overview", params: Object.fromEntries(new URLSearchParams(qs || "")) };
}
function href(page, params = {}) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")).toString();
  return `#/${page}${q ? `?${q}` : ""}`;
}
function go(page, params = {}, replace = false) {
  const h = href(page, params);
  if (h === location.hash) return;
  if (replace) { history.replaceState(null, "", h); route(); } else location.hash = h;
}
const setParams = (patch, replace = true) => go(state.page, { ...state.params, ...patch }, replace);

function crumbs(...parts) {
  const items = [`<a href="#/overview">Home</a>`, ...parts.filter(Boolean).map((p, i, a) => (i === a.length - 1
    ? `<span aria-current="page">${esc(p.t)}</span>` : `<a href="${esc(p.h)}">${esc(p.t)}</a>`))];
  $("crumbs").innerHTML = items.join('<span class="sep" aria-hidden="true">/</span>');
}

async function route() {
  const { page, params } = parseHash();
  if (page === "nl") { window.location.assign("/agent"); return; }
  const changed = page !== state.page;
  state.page = page; state.params = params;
  $$("#tabs a").forEach((a) => { if (a.dataset.page === page) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  $$(".page").forEach((s) => s.classList.toggle("hidden", s.id !== `page-${page}`));
  document.title = `${PAGE_TITLE[page]} · Pharma Commercial Intelligence`;
  closeDrawer(true); tip.hide(); sidebar(false);
  crumbs({ t: PAGE_TITLE[page], h: href(page) });
  if (changed) { window.scrollTo(0, 0); }
  try { await PAGES[page](params); } catch (e) { console.error(e); globalError(e); }
}

// ------------------------------------------------------------------ 5. UI PRIMITIVES
const ERR = {
  INVALID_PERIOD: ["Period not available", "Choose a later period ending or another basis. Growth needs a complete comparison window."],
  ENTITY_NOT_FOUND: ["Not found", "Check the name or key, or pick it from the list."],
  INSUFFICIENT_EVIDENCE: ["Insufficient evidence", "The baseline has no units or no market value, so the calculation is undefined. This is not a zero."],
  INVALID_SCENARIO: ["Assumption rejected", "Invalid assumptions are rejected, never corrected. Adjust the value and run again."],
  INVALID_INPUT: ["Request not valid", "Adjust the selection and try again."],
  MISSING_PARAMETER: ["Something is missing", "Complete the required fields."],
  UNSUPPORTED_ANALYSIS: ["Outside the supported analytical model", "The source is a national secondary-sales audit: forecasting, elasticity, prescriber and promotion analysis are not supported."],
  UNSUPPORTED_GEOGRAPHY: ["Geography is not supported", "The source data is national only — there is no state, region, zone or city."],
  UNSUPPORTED_CHANNEL: ["Channel analysis is not supported", "The source has no confirmed channel dimension."],
  UNSUPPORTED_SSA_HSA_DSA: ["SSA/HSA/DSA splits are not supported", "Their meaning in the source is unconfirmed, so they are not used for analysis."],
  UNKNOWN_TOOL: ["Unknown analysis", "This view requested an analysis that is not available."],
  INTERNAL_ERROR: ["The analytics engine could not complete this request", "Try again. If it persists, restart the local application."],
  NETWORK: ["Local service unavailable", "The application server is not responding. Restart it with run_app.py."],
};
const S = {
  loading(el, kind = "block") {
    el._render = null;
    el.setAttribute("aria-busy", "true");
    const lines = { table: '<div class="sk sk-row"></div>'.repeat(7), chart: '<div class="sk sk-chart"></div>',
      kpi: '<div class="sk sk-fig"></div><div class="sk sk-line w60"></div>',
      block: '<div class="sk sk-line w80"></div><div class="sk sk-line w60"></div><div class="sk sk-line w40"></div>' };
    el.innerHTML = `<div class="sk-block${kind === "table" ? " pad" : ""}" role="status" aria-label="Loading">${lines[kind] || lines.block}</div>`;
  },
  msg(el, cls, icon, title, text, code) {
    el._render = null; el._sig = null; el.removeAttribute("aria-busy");
    el.innerHTML = `<div class="state ${cls}" role="status"><div class="ic" aria-hidden="true">${icon}</div><div class="t">${esc(title)}</div>`
      + `${text ? `<p>${text}</p>` : ""}${code ? `<span class="code">${esc(code)}</span>` : ""}</div>`;
  },
  empty(el, title, hint) { S.msg(el, "empty", "–", title, esc(hint || "")); },
  insufficient(el, title, hint) { S.msg(el, "insufficient", "?", title, esc(hint || "")); },
  ambiguous(el, title, hint) { S.msg(el, "ambiguous", "?", title, esc(hint || "")); },
  error(el, e) {
    if (!e || !e.code) { console.error(e); e = { code: "INTERNAL_ERROR" }; }
    const [t, d] = ERR[e.code] || ["Something went wrong", "Try again."];
    const cls = e.code.startsWith("UNSUPPORTED") ? "unsupported" : e.code === "INSUFFICIENT_EVIDENCE" ? "insufficient" : "error";
    const msg = e.message && e.code !== "INTERNAL_ERROR" ? `${esc(e.message)}<br><span class="small">${esc(d)}</span>` : esc(d);
    S.msg(el, cls, "!", t, msg, e.code);
  },
};
function globalError(e) {
  const el = $("error");
  const [t, d] = ERR[e && e.code] || ERR.INTERNAL_ERROR;
  el.innerHTML = `<b>${esc(t)}.</b> ${esc(e && e.message && e.code !== "INTERNAL_ERROR" ? e.message : d)}`;
  el.classList.remove("hidden");
}
/** Load a panel: skeleton after a short delay (no flash for cached data), stale-response guard, uniform errors. */
function load(el, kind, promise, render) {
  const id = (el._lid || 0) + 1;
  el._lid = id;
  const timer = setTimeout(() => { if (el._lid === id) S.loading(el, kind); }, 90);
  return Promise.resolve(promise).then((data) => {
    clearTimeout(timer);
    if (el._lid !== id) return undefined;
    el.removeAttribute("aria-busy");
    try { render(data); } catch (e) { console.error(e); S.error(el, null); }
    return data;
  }, (e) => { clearTimeout(timer); if (el._lid === id) S.error(el, e); return undefined; });
}
/** Render a section only when its inputs change (keeps charts stable while other parts update). */
function once(el, sig, fn) { if (el._sig === sig) return; el._sig = sig; fn(); }
const src = (el, toolName, extra) => { el.innerHTML = `<span>Source: <code>${esc(toolName)}</code></span>${extra ? `<span>${extra}</span>` : ""}`; };
const metaChips = (el, items) => { el.innerHTML = items.filter(Boolean).map((x) => `<span class="meta-chip">${x}</span>`).join(""); };
const periodChips = (p) => [`<b>${esc(BASIS_SHORT[p.basis] || p.basis_label)}</b> · ${esc(windowLabel(p))}`, `vs ${esc(priorLabel(p))}`];

function kpiStrip(el, items) {
  el.innerHTML = `<div class="kpis">${items.map(([l, v, s]) => `<div class="kpi"><div class="l">${esc(l)}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`).join("")}</div>`;
}
function segControl(el, name, options, value, onChange) {
  el.innerHTML = options.map(([v, label, title]) => `<label${title ? ` title="${esc(title)}"` : ""}><input type="radio" name="${esc(name)}" value="${esc(v)}"${v === value ? " checked" : ""}><span>${esc(label)}</span></label>`).join("");
  $$("input", el).forEach((i) => i.addEventListener("change", () => onChange(i.value)));
}
const setSeg = (el, value) => $$("input", el).forEach((i) => { i.checked = i.value === value; });
const opt = (pairs, sel) => pairs.map(([v, l]) => `<option value="${esc(v)}"${v === sel ? " selected" : ""}>${esc(l)}</option>`).join("");
function chips(el, items, onReset) {
  const active = items.filter(Boolean);
  el.innerHTML = active.length
    ? `<span>Active filters:</span>${active.map((c, i) => (c.fixed ? `<span class="chip fixed">${esc(c.t)}</span>`
      : `<span class="chip">${esc(c.t)}<button type="button" data-i="${i}" aria-label="Remove filter ${esc(c.t)}">×</button></span>`)).join("")}`
      + (active.some((c) => !c.fixed) && onReset ? '<button type="button" class="btn btn-ghost btn-sm" data-reset>Clear all</button>' : "")
    : "";
  $$("button[data-i]", el).forEach((b) => b.addEventListener("click", () => active[+b.dataset.i].clear()));
  const r = el.querySelector("[data-reset]");
  if (r) r.addEventListener("click", onReset);
}
async function fillList(listEl, type) {
  listEl.innerHTML = "";
  const rows = await keyOptions(type);
  listEl.innerHTML = rows.map((r) => `<option value="${esc(r.entity_key)}">${r.entity_label !== r.entity_key ? esc(r.entity_label) : ""}</option>`).join("");
}
async function keyOptions(type) {
  const p = gp();
  if (["supergroup", "therapy_group", "subgroup"].includes(type)) return (await tool("get_market_performance", { level: type, ...p, top_n: null })).rows;
  if (type === "molecule") return (await tool("get_market_performance", { level: type, ...p, top_n: 500 })).rows;
  if (type === "company") return (await tool("get_company_performance", { ...p, top_n: null })).rows;
  if ((state.meta.segments || []).includes(type)) return (await tool("get_segment_analysis", { segment: type, ...p })).rows;
  return [];
}

// ---- data table: sortable, keyboard navigable, paged, selection-aware
function dataTable(el, cfg) {
  const rows = cfg.rows || [];
  if (!rows.length) { S.empty(el, cfg.emptyT || "No rows match this selection", cfg.emptyD || "Change the filters or the period."); return; }
  const st = el._dt && el._dt.id === cfg.id ? el._dt : { id: cfg.id, k: cfg.sort ? cfg.sort[0] : null, dir: cfg.sort ? cfg.sort[1] : "desc", shown: cfg.page || 50 };
  el._dt = st;
  const col = cfg.cols.find((c) => c.k === st.k);
  const sorted = col && col.sort ? rows.slice().sort((a, b) => {
    const x = col.sort(a), y = col.sort(b);
    const xn = x === null || x === undefined || (typeof x === "number" && !Number.isFinite(x));
    const yn = y === null || y === undefined || (typeof y === "number" && !Number.isFinite(y));
    if (xn || yn) return xn && yn ? 0 : xn ? 1 : -1;                     // missing values always last
    const c = typeof x === "string" ? x.localeCompare(y) : x - y;
    return st.dir === "asc" ? c : -c;
  }) : rows;
  const vis = sorted.slice(0, st.shown);
  const focusKey = el.contains(document.activeElement) && document.activeElement.dataset ? document.activeElement.dataset.key : null;
  const th = cfg.cols.map((c) => {
    const sortable = !!c.sort;
    const aria = st.k === c.k ? ` aria-sort="${st.dir === "asc" ? "ascending" : "descending"}"` : "";
    const icon = st.k === c.k ? (st.dir === "asc" ? "▲" : "▼") : "↕";
    const inner = sortable ? `<button type="button" class="sort" data-k="${esc(c.k)}">${esc(c.label)}<span class="si" aria-hidden="true">${icon}</span></button>` : esc(c.label);
    return `<th scope="col" class="${c.num ? "n" : ""}"${aria}${c.title ? ` title="${esc(c.title)}"` : ""}>${inner}</th>`;
  }).join("");
  const key = cfg.key || ((r) => r.entity_key);
  const body = vis.map((r, i) => {
    const k = String(key(r));
    const sel = cfg.selected !== undefined && cfg.selected !== null && k === String(cfg.selected);
    const click = cfg.onSelect ? ` class="click${sel ? " sel" : ""}" tabindex="0" data-key="${esc(k)}"${sel ? ' aria-current="true"' : ""}` : "";
    return `<tr data-i="${i}"${click}>${cfg.cols.map((c) => `<td class="${c.num ? "n" : ""}${c.wrap ? " wrap" : ""}">${c.html(r)}</td>`).join("")}</tr>`;
  }).join("");
  const more = sorted.length > vis.length
    ? `<div class="more">Showing ${T.int(vis.length)} of ${T.int(sorted.length)} <button type="button" class="btn btn-sm" data-more>Show ${Math.min(cfg.page || 50, sorted.length - vis.length)} more</button></div>`
    : (cfg.note ? `<div class="more">${cfg.note}</div>` : "");
  el.innerHTML = `<div class="tw${cfg.tall ? " tall" : ""}${cfg.auto ? " auto" : ""}"><table class="dt">${cfg.caption ? `<caption class="sr-only">${esc(cfg.caption)}</caption>` : ""}<thead><tr>${th}</tr></thead><tbody>${body}</tbody></table></div>${more}`;
  $$("button.sort", el).forEach((b) => b.addEventListener("click", () => {
    if (st.k === b.dataset.k) st.dir = st.dir === "asc" ? "desc" : "asc";
    else { st.k = b.dataset.k; st.dir = (cfg.cols.find((c) => c.k === st.k) || {}).asc ? "asc" : "desc"; }
    dataTable(el, cfg);
    const nb = el.querySelector(`button.sort[data-k="${CSS.escape(st.k)}"]`); if (nb) nb.focus();
  }));
  const m = el.querySelector("[data-more]");
  if (m) m.addEventListener("click", () => { st.shown += cfg.page || 50; dataTable(el, cfg); });
  if (cfg.onSelect) {
    $$("tbody tr", el).forEach((tr) => {
      const r = vis[+tr.dataset.i];
      tr.addEventListener("click", () => cfg.onSelect(r));
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); cfg.onSelect(r); }
        else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          const n = e.key === "ArrowDown" ? tr.nextElementSibling : tr.previousElementSibling;
          if (n) n.focus();
        }
      });
    });
    if (focusKey) { const f = el.querySelector(`tr[data-key="${CSS.escape(focusKey)}"]`); if (f) f.focus(); }
  }
}
const colRank = (k = "rank_value") => ({ k, label: "#", num: true, asc: true, sort: (r) => r[k], html: (r) => `<span class="rank">${isNum(r[k]) ? r[k] : "—"}</span>` });
const colEnt = (label, sub) => ({ k: "entity_label", label, asc: true, sort: (r) => String(r.entity_label), html: (r) => `<div class="ent"><b>${esc(r.entity_label)}</b>${sub ? `<span>${sub(r)}</span>` : ""}</div>` });
const colValue = { k: "value_cur", label: "Value", num: true, title: "₹ crore", sort: (r) => r.value_cur, html: (r) => val(r.value_cur, T.cr) };
const colGrowth = { k: "value_growth_pct", label: "Growth", num: true, title: "Value growth vs the same window a year earlier", sort: (r) => r.value_growth_pct, html: g };
const colShare = { k: "value_share_pct", label: "Share", num: true, title: "Value share of the scope", sort: (r) => r.value_share_pct, html: (r) => val(r.value_share_pct, T.share) };
const colShareChg = { k: "value_share_chg_pp", label: "Share Δ", num: true, title: "Share change, percentage points", sort: (r) => r.value_share_chg_pp, html: (r) => delta(r.value_share_chg_pp, T.pp, NA_REASON[r.value_growth_status]) };
const colEI = { k: "evolution_index", label: "EI", num: true, title: "Evolution index: share now ÷ share a year ago × 100 (above 100 = gaining share)", sort: (r) => r.evolution_index, html: (r) => val(r.evolution_index, T.ei) };
const colContrib = { k: "contribution_to_growth_pp", label: "Contribution", num: true, title: "Contribution to the scope's value growth, percentage points", sort: (r) => r.contribution_to_growth_pp, html: (r) => delta(r.contribution_to_growth_pp, T.pp) };
const colUnits = { k: "units_cur", label: "Units", num: true, title: "'000 packs (scale inferred)", sort: (r) => r.units_cur, html: (r) => val(r.units_cur, T.k) };

// ---- tooltip
const tip = {
  show(html, x, y) {
    const el = $("tip");
    el.innerHTML = html;
    el.classList.remove("hidden");
    const w = el.offsetWidth, h = el.offsetHeight, vw = window.innerWidth, vh = window.innerHeight;
    let left = x + 14, top = y + 14;
    if (left + w > vw - 8) left = x - w - 14;
    if (top + h > vh - 8) top = y - h - 14;
    el.style.left = `${Math.max(8, left)}px`;
    el.style.top = `${Math.max(8, top)}px`;
  },
  hide() { $("tip").classList.add("hidden"); },
};
const tipRows = (title, rows, note) => `<div class="th">${esc(title)}</div>${rows.map(([k, v]) => `<div class="tr"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("")}${note ? `<div class="tm">${esc(note)}</div>` : ""}`;
function bindTips(root, selector, content) {
  $$(selector, root).forEach((node) => {
    const i = +node.dataset.i;
    node.addEventListener("mousemove", (e) => tip.show(content(i), e.clientX, e.clientY));
    node.addEventListener("mouseleave", () => tip.hide());
    node.addEventListener("focus", () => { const r = node.getBoundingClientRect(); tip.show(content(i), r.right, r.top); });
    node.addEventListener("blur", () => tip.hide());
  });
}

// ---- drawer (detail panel)
let lastFocus = null;
function openDrawer(eyebrow, title) {
  lastFocus = document.activeElement;
  $("drawer-eyebrow").textContent = eyebrow;
  $("drawer-title").textContent = title;
  $("drawer").classList.add("open");
  $("drawer").setAttribute("aria-hidden", "false");
  $("scrim").classList.add("on");
  setTimeout(() => $("drawer-close").focus(), 30);
  return $("drawer-body");
}
function closeDrawer(silent) {
  if (!$("drawer").classList.contains("open")) return;
  if (!silent && state.page === "opportunity" && state.params.focus) {       // user closed: forget the focused entity
    state.params = { ...state.params, focus: "" };
    history.replaceState(null, "", href("opportunity", state.params));
  }
  $("drawer").classList.remove("open");
  $("drawer").setAttribute("aria-hidden", "true");
  $("scrim").classList.remove("on");
  if (!silent && lastFocus && document.contains(lastFocus)) lastFocus.focus();
}
function sidebar(open) {
  $("sidebar").classList.toggle("open", open);
  $("nav-toggle").setAttribute("aria-expanded", String(open));
  if (window.matchMedia("(max-width: 980px)").matches) $("scrim").classList.toggle("on", open || $("drawer").classList.contains("open"));
}

// ------------------------------------------------------------------ 6. CHARTS (SVG strings; CSP-safe; resize-aware)
const ro = new ResizeObserver((entries) => {
  for (const e of entries) {
    const el = e.target, w = Math.round(e.contentRect.width);
    if (el._render && w > 0 && Math.abs((el._w || 0) - w) > 2) el._render();
  }
});
function mount(el, draw) {
  el.classList.add("chart");
  el._render = () => { el._w = Math.round(el.clientWidth); tip.hide(); draw(el._w || 640); };
  el._render();
  ro.observe(el);
}
function niceTicks(lo, hi, n = 5) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const step0 = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(step0)), err = step0 / mag;
  const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 2; v += step) out.push(+v.toFixed(10));
  return out;
}
const tickFmt = (v, step) => {
  const d = step ? Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9))) : (Math.abs(v) >= 100 || v === 0 ? 0 : Math.abs(v) >= 1 ? 1 : 2);
  return loc(v, d);
};
const quantile = (arr, q) => { const s = arr.slice().sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.max(0, Math.round(q * (s.length - 1))))]; };

/** Line chart: cfg {rows, series:[{k,label,cls}], fmt, zero, height, desc, ref:{v,label}} — x = row.period. */
function lineChart(el, cfg) {
  const rows = cfg.rows || [];
  const vals = cfg.series.flatMap((s) => rows.map((r) => r[s.k])).filter(isNum);
  if (!rows.length || !vals.length) { S.empty(el, "No values for this selection", cfg.emptyHint || "The series has no observations in the available window."); return; }
  mount(el, (W0) => {
    const W = Math.max(W0, 280), H = cfg.height || 250, m = { l: 62, r: 12, t: 10, b: 28 };
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (cfg.zero !== false) { lo = Math.min(0, lo); hi = Math.max(0, hi); }
    if (cfg.ref && isNum(cfg.ref.v)) { lo = Math.min(lo, cfg.ref.v); hi = Math.max(hi, cfg.ref.v); }
    const ticks = niceTicks(lo, hi, 5); lo = ticks[0]; hi = ticks[ticks.length - 1];
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const x = (i) => m.l + (rows.length === 1 ? iw / 2 : (i * iw) / (rows.length - 1));
    const y = (v) => m.t + ih * (1 - (v - lo) / (hi - lo || 1));
    let s = `<svg width="${W}" height="${H}" role="img" aria-label="${esc(cfg.desc || "Trend chart")}">`;
    const step = ticks.length > 1 ? ticks[1] - ticks[0] : 1;
    ticks.forEach((t) => { s += `<line class="${t === 0 ? "zero" : "gl"}" x1="${m.l}" x2="${W - m.r}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}"/><text x="${m.l - 8}" y="${(y(t) + 4).toFixed(1)}" text-anchor="end">${esc(cfg.tick ? cfg.tick(t) : tickFmt(t, step))}</text>`; });
    const every = Math.max(1, Math.ceil(rows.length / Math.max(2, Math.floor(iw / 62))));
    rows.forEach((r, i) => { if (i % every === 0) s += `<text x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle">${esc(shortMonth(r.period))}</text>`; });
    if (cfg.ref && isNum(cfg.ref.v)) s += `<line class="ref" x1="${m.l}" x2="${W - m.r}" y1="${y(cfg.ref.v)}" y2="${y(cfg.ref.v)}"/><text class="ref-t" x="${W - m.r}" y="${y(cfg.ref.v) - 5}" text-anchor="end">${esc(cfg.ref.label)}</text>`;
    cfg.series.forEach((se) => {
      let d = "", pen = false;
      rows.forEach((r, i) => { const v = r[se.k]; if (isNum(v)) { d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; } else pen = false; });
      s += `<path class="ln ${se.cls}" d="${d}"/>`;
    });
    s += `<g class="hov" visibility="hidden"><line class="guide" y1="${m.t}" y2="${m.t + ih}"/>${cfg.series.map((se, j) => `<circle class="dotm ${se.cls}" r="4" data-s="${j}"/>`).join("")}</g>`;
    s += `<rect class="hover-cap" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" tabindex="0" role="slider" aria-label="${esc(cfg.desc || "Trend")}: use left and right arrows to read each month" aria-valuemin="0" aria-valuemax="${rows.length - 1}"/>`;
    const legend = `<div class="legend">${cfg.series.map((se) => `<span><i class="${se.cls}"></i>${esc(se.label)}</span>`).join("")}</div>`;
    el.innerHTML = `${legend}${s}</svg>`;
    const cap = el.querySelector(".hover-cap"), hov = el.querySelector(".hov"), guide = hov.querySelector("line");
    let idx = rows.length - 1;
    const at = (i, cx, cy) => {
      idx = clamp(i, 0, rows.length - 1);
      const r = rows[idx];
      guide.setAttribute("x1", x(idx)); guide.setAttribute("x2", x(idx));
      $$("circle", hov).forEach((c) => { const v = r[cfg.series[+c.dataset.s].k]; if (isNum(v)) { c.setAttribute("cx", x(idx)); c.setAttribute("cy", y(v)); c.setAttribute("visibility", "visible"); } else c.setAttribute("visibility", "hidden"); });
      hov.setAttribute("visibility", "visible");
      cap.setAttribute("aria-valuenow", idx);
      cap.setAttribute("aria-valuetext", `${monthLabel(r.period)}: ${cfg.series.map((se) => `${se.label} ${cfg.fmt(r[se.k])}`).join(", ")}`);
      tip.show(tipRows(monthLabel(r.period), cfg.series.map((se) => [se.label, isNum(r[se.k]) ? cfg.fmt(r[se.k]) : "n/a"]), cfg.note), cx, cy);
    };
    const off = () => { hov.setAttribute("visibility", "hidden"); tip.hide(); };
    cap.addEventListener("mousemove", (e) => { const b = cap.getBoundingClientRect(); at(Math.round(((e.clientX - b.left) / b.width) * (rows.length - 1)), e.clientX, e.clientY); });
    cap.addEventListener("mouseleave", off);
    cap.addEventListener("blur", off);
    const kb = () => { const b = cap.getBoundingClientRect(); at(idx, b.left + (x(idx) - m.l), b.top + 10); };
    cap.addEventListener("focus", kb);
    cap.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "Home" || e.key === "End") {
        e.preventDefault();
        idx = e.key === "Home" ? 0 : e.key === "End" ? rows.length - 1 : idx + (e.key === "ArrowRight" ? 1 : -1);
        idx = clamp(idx, 0, rows.length - 1); kb();
      }
    });
  });
}

/** Horizontal bar chart (ranking or diverging). cfg {items:[{key,label,sub,v,cls}], fmt, onSelect, selected, desc, tip(i)} */
function barChart(el, cfg) {
  const items = cfg.items || [];
  if (!items.length) { S.empty(el, cfg.emptyT || "Nothing to chart", cfg.emptyD || "No rows match this selection."); return; }
  mount(el, (W0) => {
    const W = Math.max(W0, 260), rowH = cfg.rowH || 28, top = 6;
    const labelW = Math.round(Math.min(cfg.labelW || 210, W * 0.4)), valW = cfg.valW || 78;
    const H = items.length * rowH + top + 6;
    const vs = items.map((i) => i.v).filter(isNum);
    let lo = Math.min(0, ...vs), hi = Math.max(0, ...vs);
    if (lo === hi) hi = 1;
    const x0 = labelW + 10, x1 = W - valW - 6;
    const sx = (v) => x0 + ((v - lo) / (hi - lo)) * (x1 - x0);
    const focusKey = el.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    let s = `<svg width="${W}" height="${H}" role="${cfg.onSelect ? "group" : "img"}" aria-label="${esc(cfg.desc || "Bar chart")}">`;
    s += `<line class="zero" x1="${sx(0)}" x2="${sx(0)}" y1="${top - 2}" y2="${H - 4}"/>`;
    items.forEach((it, i) => {
      const yy = top + i * rowH, v = it.v, sel = cfg.selected !== undefined && String(it.key) === String(cfg.selected);
      const cls = it.cls || (isNum(v) ? (v < 0 ? "bar-neg" : cfg.posCls || "bar-pos") : "bar-na");
      const a = isNum(v) ? Math.min(sx(0), sx(v)) : sx(0), w = isNum(v) ? Math.max(1, Math.abs(sx(v) - sx(0))) : 24;
      const label = `${it.label}${it.sub ? ` — ${it.sub}` : ""}: ${isNum(v) ? cfg.fmt(v) : "not available"}`;
      const interactive = cfg.onSelect ? ` tabindex="0" role="button" data-key="${esc(it.key)}" aria-label="${esc(label)}"${sel ? ' aria-pressed="true"' : ""}` : ` aria-label="${esc(label)}"`;
      s += `<g class="brow${sel ? " sel" : ""}" data-i="${i}"${interactive}><rect class="hit" x="0" y="${yy}" width="${W}" height="${rowH}"/>`;
      s += `<text class="lbl" x="${labelW}" y="${yy + rowH / 2 + 4}" text-anchor="end">${esc(trunc(it.label, labelW - 4))}</text>`;
      s += `<rect class="${cls}" x="${a.toFixed(1)}" y="${yy + 7}" width="${w.toFixed(1)}" height="${rowH - 14}" rx="1.5"/>`;
      s += `<text class="val" x="${W - 4}" y="${yy + rowH / 2 + 4}" text-anchor="end">${esc(isNum(v) ? cfg.fmt(v) : "n/a")}</text></g>`;
    });
    el.innerHTML = `${s}</svg>`;
    if (cfg.tip) bindTips(el, ".brow", cfg.tip);
    if (cfg.onSelect) {
      $$(".brow", el).forEach((node) => {
        const it = items[+node.dataset.i];
        node.addEventListener("click", () => cfg.onSelect(it));
        node.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); cfg.onSelect(it); } });
      });
      if (focusKey) { const f = el.querySelector(`.brow[data-key="${CSS.escape(focusKey)}"]`); if (f) f.focus(); }
    }
  });
}

/** Paired bars: share now vs share a year earlier for each member (mix shift). */
function mixChart(el, rows, desc) {
  if (!rows.length) { S.empty(el, "No segment members", "This scope has no sales in the selected window."); return; }
  mount(el, (W0) => {
    const W = Math.max(W0, 280), rowH = 40, labelW = Math.round(Math.min(190, W * 0.34)), valW = 150;
    const H = rows.length * rowH + 8;
    const vs = rows.flatMap((r) => [r.value_share_pct, r.value_share_prior_pct]).filter(isNum);
    const hi = Math.max(1, ...vs);
    const x0 = labelW + 10, x1 = W - valW;
    const sx = (v) => x0 + (v / hi) * (x1 - x0);
    let s = `<svg width="${W}" height="${H}" role="img" aria-label="${esc(desc)}">`;
    rows.forEach((r, i) => {
      const yy = 4 + i * rowH;
      s += `<g class="brow" data-i="${i}" tabindex="0" aria-label="${esc(`${r.entity_label}: share ${T.share(r.value_share_pct)}, a year earlier ${T.share(r.value_share_prior_pct)}, change ${T.pp(r.value_share_chg_pp)}`)}"><rect class="hit" x="0" y="${yy}" width="${W}" height="${rowH}"/>`;
      s += `<text class="lbl" x="${labelW}" y="${yy + 19}" text-anchor="end">${esc(trunc(r.entity_label, labelW))}</text>`;
      if (isNum(r.value_share_prior_pct)) s += `<rect class="bar-prior" x="${x0}" y="${yy + 7}" width="${Math.max(1, sx(r.value_share_prior_pct) - x0).toFixed(1)}" height="7" rx="1"/>`;
      if (isNum(r.value_share_pct)) s += `<rect class="bar-main" x="${x0}" y="${yy + 17}" width="${Math.max(1, sx(r.value_share_pct) - x0).toFixed(1)}" height="12" rx="1.5"/>`;
      s += `<text class="val" x="${W - 4}" y="${yy + 19}" text-anchor="end">${esc(T.share(r.value_share_pct, 1))}  ${esc(T.pp(r.value_share_chg_pp, 1))}</text></g>`;
    });
    el.innerHTML = `<div class="legend"><span><i class="sw bar-main"></i>Share now</span><span><i class="sw bar-prior"></i>Share a year earlier</span></div>${s}</svg>`;
    bindTips(el, ".brow", (i) => { const r = rows[i]; return tipRows(r.entity_label, [["Share now", T.share(r.value_share_pct)], ["A year earlier", T.share(r.value_share_prior_pct)], ["Change", T.pp(r.value_share_chg_pp)], ["Value", T.cr(r.value_cur)], ["Growth", T.pct(r.value_growth_pct)]]); });
  });
}

/** Scatter / quadrant chart. cfg {pts:[{key,x,y,r,cls,dim}], xDom, yDom, xLog, xRef, yRef, xLabel, yLabel, xFmt, yFmt, quad, onSelect, selected, tip(i), desc} */
function scatter(el, cfg) {
  const pts = cfg.pts.filter((p) => isNum(p.x) && isNum(p.y) && (!cfg.xLog || p.x > 0));
  if (!pts.length) { S.empty(el, "No plottable entities", cfg.emptyD || "No rows with both measures are available for this selection."); return; }
  mount(el, (W0) => {
    const W = Math.max(W0, 300), H = cfg.height || Math.round(clamp(W * 0.56, 300, 460));
    const m = { l: 58, r: 16, t: 12, b: 44 }, iw = W - m.l - m.r, ih = H - m.t - m.b;
    const [xa, xb] = cfg.xDom, [ya, yb] = cfg.yDom;
    const fx = cfg.xLog ? Math.log : (v) => v;
    const fy = cfg.ySym ? (v) => Math.sign(v) * Math.log1p(Math.abs(v) / 10) : (v) => v;   // symmetric log keeps skewed growth readable
    const X = (v) => m.l + ((fx(clamp(v, xa, xb)) - fx(xa)) / (fx(xb) - fx(xa) || 1)) * iw;
    const Y = (v) => m.t + (1 - (fy(clamp(v, ya, yb)) - fy(ya)) / (fy(yb) - fy(ya) || 1)) * ih;
    let s = `<svg width="${W}" height="${H}" role="group" aria-label="${esc(cfg.desc)}">`;
    if (isNum(cfg.xRef) && isNum(cfg.yRef) && cfg.quad) {
      const qx = X(cfg.xRef), qy = Y(cfg.yRef);
      s += `<rect class="qbg hi" x="${qx}" y="${m.t}" width="${m.l + iw - qx}" height="${qy - m.t}"/>`;
    }
    const logTicks = xb / xa > 30 ? [1, 2.5, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000] : [10, 25, 50, 75, 100, 150, 200, 300, 500, 1000];
    const xt = cfg.xLog ? logTicks.filter((t) => t >= xa && t <= xb) : niceTicks(xa, xb, 6).filter((t) => t >= xa && t <= xb);
    const yt = cfg.ySym ? [-100, -50, -25, -10, 0, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000].filter((t) => t >= ya && t <= yb) : niceTicks(ya, yb, 5).filter((t) => t >= ya && t <= yb);
    xt.forEach((t) => { s += `<line class="gl" x1="${X(t)}" x2="${X(t)}" y1="${m.t}" y2="${m.t + ih}"/><text x="${X(t)}" y="${m.t + ih + 16}" text-anchor="middle">${esc(cfg.xTick ? cfg.xTick(t) : tickFmt(t))}</text>`; });
    yt.forEach((t) => { s += `<line class="${t === 0 ? "zero" : "gl"}" x1="${m.l}" x2="${m.l + iw}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${m.l - 8}" y="${Y(t) + 4}" text-anchor="end">${esc(cfg.yTick ? cfg.yTick(t) : tickFmt(t))}</text>`; });
    if (isNum(cfg.xRef)) s += `<line class="ref" x1="${X(cfg.xRef)}" x2="${X(cfg.xRef)}" y1="${m.t}" y2="${m.t + ih}"/>`;
    if (isNum(cfg.yRef)) s += `<line class="ref" x1="${m.l}" x2="${m.l + iw}" y1="${Y(cfg.yRef)}" y2="${Y(cfg.yRef)}"/><text class="ref-t" x="${m.l + 4}" y="${Y(cfg.yRef) - 5}">${esc(cfg.yRefLabel || "")}</text>`;
    if (cfg.quad) {
      const [tl, tr, bl, br] = cfg.quad;
      s += `<text class="quad" x="${m.l + 6}" y="${m.t + 14}">${esc(tl)}</text><text class="quad" x="${m.l + iw - 6}" y="${m.t + 14}" text-anchor="end">${esc(tr)}</text>`;
      s += `<text class="quad" x="${m.l + 6}" y="${m.t + ih - 8}">${esc(bl)}</text><text class="quad" x="${m.l + iw - 6}" y="${m.t + ih - 8}" text-anchor="end">${esc(br)}</text>`;
    }
    s += `<text class="ax-t" x="${m.l + iw / 2}" y="${H - 6}" text-anchor="middle">${esc(cfg.xLabel)}</text>`;
    s += `<text class="ax-t" transform="translate(14 ${m.t + ih / 2}) rotate(-90)" text-anchor="middle">${esc(cfg.yLabel)}</text>`;
    const order = pts.map((p, i) => i).sort((a, b) => (pts[b].r || 5) - (pts[a].r || 5));
    order.forEach((i) => {
      const p = pts[i];
      const clamped = p.x < xa || p.x > xb || p.y < ya || p.y > yb;
      const sel = cfg.selected !== undefined && String(p.key) === String(cfg.selected);
      s += `<circle class="pt ${p.cls || "sc3"}${clamped ? " clamped" : ""}${sel ? " sel" : ""}${p.dim ? " dim" : ""}" data-i="${i}" data-key="${esc(p.key)}" cx="${X(p.x).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="${(p.r || 5).toFixed(1)}" tabindex="${p.dim ? -1 : 0}" role="button" aria-label="${esc(p.aria || p.key)}"/>`;
    });
    el.innerHTML = `${cfg.legend || ""}${s}</svg>${cfg.note ? `<p class="chart-note">${cfg.note}</p>` : ""}`;
    const circles = $$("circle.pt", el);
    bindTips(el, "circle.pt", (i) => cfg.tip(pts[i]));
    circles.forEach((c) => {
      const p = pts[+c.dataset.i];
      if (!cfg.onSelect) return;
      c.addEventListener("click", () => cfg.onSelect(p));
      c.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); cfg.onSelect(p); } });
    });
  });
}

/** Inline score decomposition: weighted component points (engine values) drawn on a 0–100 bar. */
function scoreStack(r, w = 96) {
  if (!isNum(r.score)) return `<span class="badge b-insufficient">Not scored</span>`;
  let x = 0, s = "";
  (r.components || []).forEach((c, i) => {
    if (!isNum(c.weighted_contribution)) return;
    const ww = (c.weighted_contribution / 100) * w;
    s += `<rect class="cmp${i}" x="${x.toFixed(1)}" y="0" width="${Math.max(0, ww).toFixed(1)}" height="10"/>`;
    x += ww;
  });
  const label = (r.components || []).map((c) => `${c.label} ${isNum(c.weighted_contribution) ? c.weighted_contribution.toFixed(1) : "n/a"} pts`).join("; ");
  return `<span class="cellbar"><b>${esc(T.score(r.score))}</b><span class="chart"><svg class="scorebar" width="${w}" height="10" role="img" aria-label="${esc(label)}"><rect x="0" y="0" width="${w}" height="10" fill="none" stroke="#e3e1d9"/>${s}</svg></span></span>`;
}
function sparkline(el, rows, k) {
  const vs = rows.map((r) => r[k]);
  if (!vs.some(isNum)) { el.innerHTML = ""; return; }
  mount(el, (W0) => {
    const W = Math.max(120, Math.min(W0, 360)), H = 34;
    const nums = vs.filter(isNum), lo = Math.min(...nums), hi = Math.max(...nums);
    const x = (i) => 2 + (i * (W - 4)) / (vs.length - 1), y = (v) => 3 + (H - 6) * (1 - (v - lo) / (hi - lo || 1));
    let d = "", pen = false;
    vs.forEach((v, i) => { if (isNum(v)) { d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; } else pen = false; });
    el.innerHTML = `<span class="chart"><svg width="${W}" height="${H}" role="img" aria-label="Moving annual total trend"><path class="ln main" d="${d}"/></svg></span>`;
  });
}

// ------------------------------------------------------------------ 7. PAGES
const TREND_METRICS = {
  value: { label: "Monthly value", series: [{ k: "value_cr", label: "Monthly value (₹ cr)", cls: "main" }, { k: "value_cr_prior", label: "Same month a year earlier", cls: "prior" }], fmt: T.cr, zero: true },
  mat: { label: "MAT value", series: [{ k: "value_mat", label: "MAT value (₹ cr)", cls: "main" }, { k: "value_mat_prior", label: "MAT a year earlier", cls: "prior" }], fmt: T.cr, zero: false },
  matg: { label: "MAT growth", series: [{ k: "value_mat_growth_pct", label: "MAT value growth (%)", cls: "acc" }], fmt: T.pct, zero: true, ref: { v: 0, label: "" } },
  yoy: { label: "Monthly growth", series: [{ k: "value_growth_pct", label: "Value growth vs same month a year earlier (%)", cls: "acc" }], fmt: T.pct, zero: true },
  units: { label: "Units", series: [{ k: "units_k", label: "Units ('000 packs)", cls: "main" }, { k: "units_k_prior", label: "Same month a year earlier", cls: "prior" }], fmt: T.k, zero: true },
};
/** Trend panel with a metric switch. The switch is shared across pages (state.trendMetric). */
function trendPanel(toolsEl, chartEl, rows, what) {
  const draw = () => {
    const mdef = TREND_METRICS[state.trendMetric] || TREND_METRICS.value;
    lineChart(chartEl, { rows, series: mdef.series, fmt: mdef.fmt, zero: mdef.zero, ref: mdef.ref, desc: `${what}: ${mdef.label}, monthly, June 2021 to May 2024`,
      emptyHint: state.trendMetric.startsWith("mat") ? "MAT values start in May 2022; MAT growth needs data from May 2023." : "" });
  };
  if (toolsEl) {
    segControl(toolsEl, `tm-${chartEl.id}`, Object.entries(TREND_METRICS).map(([k, v]) => [k, v.label]), state.trendMetric, (v) => { state.trendMetric = v; store.set("trend", v); draw(); });
    toolsEl.classList.add("seg", "sm");
    toolsEl.setAttribute("role", "radiogroup");
    toolsEl.setAttribute("aria-label", "Trend metric");
  }
  draw();
}

// ================================================================== 7.1 EXECUTIVE OVERVIEW
async function pageOverview() {
  const p = gp();
  // the local server answers one request at a time: ask for the headline figures first, the 2 MB subgroup list last
  const totP = tool("get_market_performance", { level: "total", ...p });
  const trendP = tool("get_market_trends", { level: "total", key: "TOTAL" });
  const thP = tool("get_therapy_performance", { level: "supergroup", ...p });
  const coP = tool("get_company_performance", { ...p, top_n: 12 });
  const prP = tool("get_brand_performance", { ...p, top_n: 10 });
  const opP = tool("get_opportunity_scores", { level: "product", anchor: p.anchor, basis: oppBasis(), top_n: 100 });
  const sgP = tool("get_market_performance", { level: "subgroup", ...p, top_n: null });
  const sig = periodSig();
  if ($("page-overview")._sig === sig) return;           // already rendered for this period
  $("page-overview")._sig = sig;

  load($("ov-signal"), "kpi", Promise.all([totP, trendP, thP]), ([tot, tr, th]) => {
    const t = tot.rows[0], per = tot.period;
    metaChips($("ov-meta"), periodChips(per));
    const lead = th.rows.slice().sort(byDesc("contribution_to_growth_pp"))[0];
    const dir = !isNum(t.value_growth_pct) ? null : t.value_growth_pct >= 0 ? "up" : "down";
    const head = dir
      ? `The national market reached <b>${esc(T.cr(t.value_cur))}</b> in the ${esc(per.basis_label)} to ${esc(monthLabel(per.cur_end))}, <b>${dir} ${esc(T.pct(Math.abs(t.value_growth_pct)).replace("+", ""))}</b> on the same window a year earlier.`
      : `The national market reached <b>${esc(T.cr(t.value_cur))}</b> in the ${esc(per.basis_label)} to ${esc(monthLabel(per.cur_end))}. Growth is not available: the comparison window predates the data.`;
    $("ov-signal").innerHTML = `
      <div class="sig headline"><div class="l">What changed · national market</div><div class="v">${head}</div>
        <div class="s">${esc(windowLabel(per))} vs ${esc(priorLabel(per))}</div><div class="spark" id="ov-spark"></div></div>
      <div class="sig"><div class="l">Value growth</div><div class="v">${g(t)}</div><div class="s">${esc(T.crAbs(t.value_abs_chg))} change in value</div></div>
      <div class="sig"><div class="l">Unit growth</div><div class="v">${delta(t.units_growth_pct, T.pct, NA_REASON[t.value_growth_status])}</div><div class="s">${esc(T.int(t.units_cur))} ('000 packs)</div></div>
      <div class="sig"><div class="l">Largest contributor</div><div class="v">${lead ? delta(lead.contribution_to_growth_pp, T.pp) : na()}</div>
        <div class="s">${lead ? `<a href="${esc(href("market", { level: "supergroup", key: lead.entity_key }))}">${esc(lead.entity_label)}</a> · therapy area, pp of national growth` : "No therapy data"}</div></div>`;
    sparkline($("ov-spark"), tr.rows, "value_mat");
  });

  load($("ov-trend"), "chart", trendP, (tr) => {
    trendPanel($("ov-trend-tools"), $("ov-trend"), tr.rows, "National market");
    src($("ov-trend-src"), "get_market_trends", "total market · 36 dense months");
  });

  load($("ov-contrib"), "chart", thP, (th) => {
    const rows = th.rows.slice().sort((a, b) => Math.abs(b.contribution_to_growth_pp ?? 0) - Math.abs(a.contribution_to_growth_pp ?? 0)).slice(0, 10).sort(byDesc("contribution_to_growth_pp"));
    barChart($("ov-contrib"), {
      items: rows.map((r) => ({ key: r.entity_key, label: r.entity_label, v: r.contribution_to_growth_pp })), fmt: T.pp, labelW: 170,
      desc: "Contribution of the ten largest-moving therapy areas to national value growth, percentage points",
      onSelect: (it) => go("market", { level: "supergroup", key: it.key }),
      tip: (i) => { const r = rows[i]; return tipRows(r.entity_label, [["Contribution", T.pp(r.contribution_to_growth_pp)], ["Value", T.cr(r.value_cur)], ["Growth", T.pct(r.value_growth_pct)], ["Share of market", T.share(r.value_share_pct)]], "Select to open in Market Intelligence"); },
    });
    src($("ov-contrib-src"), "get_therapy_performance", `therapy areas · top 10 by absolute contribution · ${esc(windowLabel(th.period))}`);
  });

  load($("ov-movers"), "chart", sgP, (sg) => {
    const byC = sg.rows.filter((r) => isNum(r.contribution_to_growth_pp)).sort(byDesc("contribution_to_growth_pp"));
    const up = byC.slice(0, 6), down = byC.filter((r) => r.contribution_to_growth_pp < 0).slice(-5);
    const rows = [...up, ...down.filter((r) => !up.includes(r))];
    if (!rows.length) { S.insufficient($("ov-movers"), "No comparison period", "Contribution to growth needs a complete comparison window; choose a later period ending."); return; }
    barChart($("ov-movers"), {
      items: rows.map((r) => ({ key: r.entity_key, label: r.entity_label, v: r.contribution_to_growth_pp })), fmt: (v) => T.pp(v, 3), labelW: 220,
      desc: "Therapy subgroups with the largest positive and negative contribution to national growth",
      onSelect: (it) => go("market", { level: "subgroup", key: it.key }),
      tip: (i) => { const r = rows[i]; return tipRows(r.entity_label, [["Contribution", T.pp(r.contribution_to_growth_pp, 3)], ["Growth", T.pct(r.value_growth_pct)], ["Value", T.cr(r.value_cur)], ["Rank by value", `${r.rank_value} of ${T.int(r.n_entities)}`]], "Select to open the market"); },
    });
    src($("ov-movers-src"), "get_market_performance", `subgroup level · top 6 contributors and ${down.length} largest drags`);
  });

  load($("ov-companies"), "chart", coP, (co) => {
    const rows = co.rows;
    barChart($("ov-companies"), {
      items: rows.map((r) => ({ key: r.entity_key, label: r.entity_label, v: r.value_share_chg_pp })), fmt: T.pp, labelW: 190,
      desc: "Share change of the twelve largest companies, percentage points",
      onSelect: (it) => go("company", { key: it.key }),
      tip: (i) => { const r = rows[i]; return tipRows(`${r.entity_label} (#${r.rank_value})`, [["Share", T.share(r.value_share_pct)], ["Share change", T.pp(r.value_share_chg_pp)], ["Evolution index", T.ei(r.evolution_index)], ["Growth", T.pct(r.value_growth_pct)], ["Value", T.cr(r.value_cur)]], "Ordered by value rank"); },
    });
    src($("ov-companies-src"), "get_company_performance", "12 largest companies by value, in value order");
  });

  load($("ov-opp"), "table", opP, (op) => {
    const counts = {};
    op.rows.forEach((r) => { if (r.matrix_quadrant) counts[r.matrix_quadrant] = (counts[r.matrix_quadrant] || 0) + 1; });
    $("ov-opp").innerHTML = `<div id="ov-opp-list"></div><p class="small muted">Where the top ${T.int(op.row_count)} sit in the opportunity matrix (rows shown):</p>
      <div class="quads">${QUADS.map(([name]) => `<a class="quad-btn${name.startsWith("Outperforming in faster") ? " hi" : ""}" href="${esc(href("opportunity", { quad: name }))}"><div class="c">${T.int(counts[name] || 0)}</div><div class="t">${esc(name)}</div></a>`).join("")}</div>`;
    dataTable($("ov-opp-list"), { id: "ov-opp", rows: op.rows.slice(0, 6), auto: true, caption: "Top descriptive opportunity scores",
      cols: [colRank("opportunity_rank"), { k: "brand", label: "Product in market", html: (r) => `<div class="ent"><b>${esc(r.brand)} · ${esc(r.company)}</b><span>${esc(r.subgroup)}</span></div>` },
        { k: "score", label: "Score & decomposition", num: true, html: (r) => scoreStack(r, 100) }],
      onSelect: (r) => go("opportunity", { focus: r.entity_key }) });
    src($("ov-opp-src"), "get_opportunity_scores", `${esc(op.methodology.version)} · top 100 product-in-market scores · ${esc(op.period.basis_label)}${state.basis === "MONTH" ? " (scoring uses MAT when Month is selected)" : ""}`);
  });

  load($("ov-products"), "table", prP, (pr) => {
    dataTable($("ov-products"), {
      id: "ov-products", rows: pr.rows, key: (r) => r.entity_key, caption: "Leading products", auto: true,
      cols: [colRank(), { k: "brand", label: "Product", asc: true, sort: (r) => r.brand, html: (r) => `<div class="ent"><b>${esc(r.brand)}</b><span>${esc(r.company)} · code ${esc(r.entity_key)}</span></div>` },
        colValue, colGrowth, colShare, colShareChg, colEI, colContrib],
      onSelect: (r) => go("product", { code: r.entity_key }),
    });
    src($("ov-products-src"), "get_brand_performance", "national scope · top 10 by value");
  });

  const paths = $("ov-paths");
  load(paths, "block", Promise.allSettled([thP, sgP, coP, opP, prP]), (res) => {
    const [th, sg, co, op, pr] = res.map((r) => (r.status === "fulfilled" ? r.value : null));
    const items = [];
    const topArea = th && th.rows.slice().sort(byDesc("contribution_to_growth_pp"))[0];
    if (topArea && isNum(topArea.contribution_to_growth_pp)) items.push([`Why is ${topArea.entity_label} driving national growth?`, `${T.pp(topArea.contribution_to_growth_pp)} contribution · growth ${T.pct(topArea.value_growth_pct)}`, href("market", { level: "supergroup", key: topArea.entity_key }), "Market"]);
    const drag = sg && sg.rows.filter((r) => isNum(r.contribution_to_growth_pp)).sort(byDesc("contribution_to_growth_pp")).pop();
    if (drag && drag.contribution_to_growth_pp < 0) items.push([`What is holding back ${drag.entity_label}?`, `Largest negative contributor among subgroups · ${T.pp(drag.contribution_to_growth_pp, 3)} · growth ${T.pct(drag.value_growth_pct)}`, href("market", { level: "subgroup", key: drag.entity_key }), "Market"]);
    const loser = co && co.rows.filter((r) => isNum(r.value_share_chg_pp)).sort((a, b) => a.value_share_chg_pp - b.value_share_chg_pp)[0];
    if (loser && loser.value_share_chg_pp < 0) items.push([`Why is ${loser.entity_label} losing share?`, `Largest share loss among the 12 largest companies · ${T.pp(loser.value_share_chg_pp)} · EI ${T.ei(loser.evolution_index)}`, href("company", { key: loser.entity_key }), "Company"]);
    const gainer = pr && pr.rows.filter((r) => isNum(r.value_share_chg_pp)).sort(byDesc("value_share_chg_pp"))[0];
    if (gainer && gainer.value_share_chg_pp > 0) items.push([`What is behind ${gainer.brand}'s share gain?`, `${gainer.company} · largest share gain among the top 10 products · ${T.pp(gainer.value_share_chg_pp)}`, href("product", { code: gainer.entity_key }), "Product"]);
    const top = op && op.rows[0];
    if (top) items.push([`What evidence supports ${top.brand} in ${top.subgroup}?`, `Rank 1 descriptive opportunity score · ${T.score(top.score)} · EI ${T.ei(top.evolution_index)} · market growth ${T.pct(top.market_value_growth_pct)}`, href("opportunity", { focus: top.entity_key }), "Evidence"]);
    if (!items.length) { S.insufficient(paths, "No investigation paths for this period", "Growth-based paths need a complete comparison window. Choose a later period ending."); return; }
    paths.innerHTML = `<ul class="paths">${items.map(([t, d, h, k], i) => `<li><a href="${esc(h)}"><span class="n">${String(i + 1).padStart(2, "0")}</span><span><span class="t">${esc(t)}</span><span class="d">${esc(d)}</span></span><span class="go">${esc(k)} →</span></a></li>`).join("")}</ul>`;
  });
}

// ================================================================== 7.2 MARKET INTELLIGENCE
const MARKET_LEVELS = [["supergroup", "Therapy area"], ["therapy_group", "Therapy group"], ["subgroup", "Subgroup"], ["molecule", "Molecule"]];
function marketListP(level, area) {
  const p = gp();
  if (area) return tool("get_therapy_performance", { level, ...p, within_supergroup: area, top_n: null });
  return tool("get_market_performance", { level, ...p, top_n: level === "molecule" ? 500 : null });
}
async function pageMarket(params) {
  const level = MARKET_LEVELS.some(([k]) => k === params.level) ? params.level : "subgroup";
  const area = level === "therapy_group" || level === "subgroup" ? (params.area || "") : "";
  const q = (params.q || "").trim();
  setSeg($("mk-level"), level);
  $("mk-area").value = area;
  $("mk-area").disabled = !(level === "therapy_group" || level === "subgroup");
  if (document.activeElement !== $("mk-filter")) $("mk-filter").value = q;
  chips($("mk-chips"), [
    { t: `Level: ${lvl(level)}`, fixed: true },
    area && { t: `Therapy area: ${area} (shares within area)`, clear: () => setParams({ area: "", key: "" }) },
    q && { t: `Name contains “${q}”`, clear: () => setParams({ q: "" }) },
  ], () => go("market", { level }));
  const res = await load($("mk-table"), "table", marketListP(level, area), () => {});
  if (!res) { S.error($("mk-contrib"), { code: "INTERNAL_ERROR" }); $("mk-detail").innerHTML = ""; return; }
  metaChips($("mk-meta"), periodChips(res.period));
  const rows = q ? res.rows.filter((r) => String(r.entity_label).toLowerCase().includes(q.toLowerCase())) : res.rows;
  const key = params.key && res.rows.some((r) => r.entity_key === params.key) ? params.key : (rows[0] && rows[0].entity_key);
  const selRow = res.rows.find((r) => r.entity_key === key);
  crumbs({ t: "Market Intelligence", h: href("market", { level }) }, { t: lvl(level), h: href("market", { level }) }, selRow && { t: selRow.entity_label });
  const select = (r) => setParams({ key: r.entity_key || r.key }, false);
  $("mk-rank-q").textContent = `${lvl(level)} markets ranked by value${area ? ` within ${area}` : ""}. Select one to analyse it.`;
  dataTable($("mk-table"), {
    id: `mk-${level}`, rows, selected: key, onSelect: select, page: 60, tall: true, sort: ["value_cur", "desc"], caption: "Market ranking",
    emptyT: q ? `No ${lvl(level).toLowerCase()} names contain “${q}”` : "No markets", emptyD: "Clear the name filter or change the level.",
    cols: [colRank(), colEnt("Market", (r) => (r.supergroup && !area ? esc(r.supergroup) : "")), colValue, colGrowth],
    note: res.total_rows > res.row_count ? `Top ${T.int(res.row_count)} of ${T.int(res.total_rows)} by value (molecule list is capped).` : "",
  });
  src($("mk-table-src"), area ? "get_therapy_performance" : "get_market_performance", `${esc(lvl(level))} · ${esc(windowLabel(res.period))}${area ? " · shares within therapy area" : ""}`);

  const byC = rows.filter((r) => isNum(r.contribution_to_growth_pp)).sort(byDesc("contribution_to_growth_pp"));
  const pos = byC.filter((r) => r.contribution_to_growth_pp >= 0).slice(0, 8), neg = byC.filter((r) => r.contribution_to_growth_pp < 0).slice(-6);
  const crow = [...pos, ...neg];
  if (!rows.length) S.empty($("mk-contrib"), "No markets match the filter", "Clear the name filter to see contributions.");
  else if (!crow.length) S.insufficient($("mk-contrib"), "Contribution not available", "Contribution to growth needs a complete comparison window. Choose a later period ending.");
  else barChart($("mk-contrib"), {
    items: crow.map((r) => ({ key: r.entity_key, label: r.entity_label, v: r.contribution_to_growth_pp })), fmt: (v) => T.pp(v, 3), labelW: 230, selected: key,
    desc: `Largest positive and negative contributions to growth, ${lvl(level)} level`, onSelect: (it) => setParams({ key: it.key }, false),
    tip: (i) => { const r = crow[i]; return tipRows(r.entity_label, [["Contribution", T.pp(r.contribution_to_growth_pp, 3)], ["Growth", T.pct(r.value_growth_pct)], ["Value", T.cr(r.value_cur)], ["Share", T.share(r.value_share_pct)]]); },
  });
  src($("mk-contrib-src"), area ? "get_therapy_performance" : "get_market_performance", `contribution_to_growth_pp · ${pos.length} largest positive, ${neg.length} largest negative${q ? " (name filter applied)" : ""}`);

  if (!selRow) {
    S.empty($("mk-detail"), "No market selected", "Select a market in the ranking or the contribution chart.");
    ["mk-products", "mk-companies"].forEach((id) => S.empty($(id), "No market selected", ""));
    $("mk-products-q").textContent = "Largest products in the selected market with their share movement.";
    $("mk-products-src").innerHTML = ""; $("mk-companies-src").innerHTML = "";
    return;
  }
  once($("mk-detail"), `${periodSig()}|${level}|${area}|${key}`, () => marketDetail(level, area, selRow, res));
}
function marketDetail(level, area, r, res) {
  const el = $("mk-detail");
  const scenarioType = "MARKET_GROWTH";
  const askable = level === "subgroup" || level === "supergroup";
  const ask = askable ? `/agent?q=${encodeURIComponent(`market trend for ${level === "subgroup" ? "subgroup" : "therapy area"} "${r.entity_label}"`)}` : null;
  el.innerHTML = `
    <div class="detail-head">
      <div><p class="eyebrow">${esc(lvl(level))}${area ? ` · within ${esc(area)}` : ""}</p><h2 id="h-mk-detail">${esc(r.entity_label)}</h2>
        <p class="sub">Rank ${esc(r.rank_value)} of ${esc(T.int(r.n_entities))} by value${area ? " in the therapy area" : ""} · ${esc(windowLabel(res.period))} vs ${esc(priorLabel(res.period))}</p></div>
      <div class="row">
        <a class="btn btn-sm" href="${esc(href("scenario", { type: scenarioType, etype: level, ekey: r.entity_key }))}">Model market growth</a>
        ${level === "subgroup" ? `<a class="btn btn-sm" href="${esc(href("opportunity", { level: "market", focus: r.entity_key }))}">Opportunity evidence</a>` : ""}
        <a class="btn btn-sm" href="${esc(href("company", { scope: level, skey: r.entity_key }))}">Companies in this market</a>
        ${ask ? `<a class="btn btn-sm" href="${esc(ask)}">Ask the AI Analyst</a>` : ""}
      </div>
    </div>
    <div id="mk-kpis"></div>
    <div class="row between"><h3 class="small muted">Market trend</h3><div id="mk-trend-tools"></div></div>
    <div id="mk-trend"></div>`;
  kpiStrip($("mk-kpis"), [
    ["Value", esc(T.cr(r.value_cur)), `${esc(T.crAbs(r.value_abs_chg))} vs prior`],
    ["Growth", g(r), "value, year on year"],
    [area ? "Share of area" : "Share of market", esc(T.share(r.value_share_pct)), delta(r.value_share_chg_pp, T.pp, NA_REASON[r.value_growth_status])],
    ["Evolution index", val(r.evolution_index, T.ei), "100 = holding share"],
    ["Contribution", delta(r.contribution_to_growth_pp, T.pp), "pp of scope growth"],
    ["Units", esc(T.k(r.units_cur)), delta(r.units_growth_pct, T.pct, NA_REASON[r.value_growth_status])],
  ]);
  load($("mk-trend"), "chart", tool("get_market_trends", { level, key: r.entity_key }), (tr) => trendPanel($("mk-trend-tools"), $("mk-trend"), tr.rows, r.entity_label));

  // products: who drove the change
  const prodP = tool("get_brand_performance", { ...gp(), market_level: level, market_key: r.entity_key, top_n: 15 });
  $("mk-products-q").textContent = `Largest products in ${r.entity_label}. Shares and contributions are within this market.`;
  const drawProducts = (pr) => {
    const view = $("mk-prod-view").querySelector("input:checked")?.value || "share";
    const rows = pr.rows;
    if (view === "table") {
      dataTable($("mk-products"), { id: "mk-prod-t", rows, auto: true, cols: [colRank(), { k: "brand", label: "Product", asc: true, sort: (x) => x.brand, html: (x) => `<div class="ent"><b>${esc(x.brand)}</b><span>${esc(x.company)} · ${esc(x.entity_key)}</span></div>` }, colValue, colShare, colShareChg, colEI],
        onSelect: (x) => go("product", { code: x.entity_key, mk: level === "subgroup" ? r.entity_key : "" }) });
      return;
    }
    const k = view === "share" ? "value_share_chg_pp" : "contribution_to_growth_pp";
    barChart($("mk-products"), {
      items: rows.map((x) => ({ key: x.entity_key, label: `${x.brand} · ${x.company}`, v: x[k] })), fmt: view === "share" ? T.pp : (v) => T.pp(v, 3), labelW: 230,
      desc: `${view === "share" ? "Share change" : "Contribution to market growth"} of the 15 largest products in ${r.entity_label}`,
      onSelect: (it) => go("product", { code: it.key, mk: level === "subgroup" ? r.entity_key : "" }),
      tip: (i) => { const x = rows[i]; return tipRows(`${x.brand} (${x.company})`, [["Product code", x.entity_key], ["Share in market", T.share(x.value_share_pct)], ["Share change", T.pp(x.value_share_chg_pp)], ["Contribution", T.pp(x.contribution_to_growth_pp, 3)], ["Growth", T.pct(x.value_growth_pct)], ["Value", T.cr(x.value_cur)]], "Ordered by value rank · select for the product profile"); },
    });
  };
  segControl($("mk-prod-view"), "mk-prod-view", [["share", "Share Δ"], ["contrib", "Contribution"], ["table", "Table"]], "share", () => prodP.then(drawProducts).catch((e) => S.error($("mk-products"), e)));
  load($("mk-products"), "chart", prodP, drawProducts);
  src($("mk-products-src"), "get_brand_performance", `top 15 products by value in ${esc(r.entity_label)}`);

  load($("mk-companies"), "table", tool("get_company_performance", { ...gp(), market_level: level, market_key: r.entity_key, top_n: 10 }), (co) => {
    dataTable($("mk-companies"), { id: "mk-co", rows: co.rows, auto: true, caption: "Companies in the selected market",
      cols: [colRank(), colEnt("Company", (x) => esc(x.indian_mnc || "")), colShare, colShareChg, colEI, colGrowth],
      onSelect: (x) => go("company", { scope: level, skey: r.entity_key, key: x.entity_key }) });
    src($("mk-companies-src"), "get_company_performance", `top 10 companies in ${esc(r.entity_label)}`);
  });
}

// ================================================================== 7.3 SEGMENTS & MIX
async function pageTherapy(params) {
  const segs = state.meta.segments;
  const seg = segs.includes(params.seg) ? params.seg : "acute_chronic";
  const scopes = ["total", "supergroup", "therapy_group", "subgroup", "molecule", "company"];
  const scope = scopes.includes(params.scope) ? params.scope : "total";
  const skey = scope === "total" ? "" : (params.skey || "");
  $("sg-seg").value = seg; $("sg-scope").value = scope;
  $("sg-key-wrap").classList.toggle("hidden", scope === "total");
  if (document.activeElement !== $("sg-key")) $("sg-key").value = skey;
  if ($("sg-key-list")._type !== scope) { $("sg-key-list")._type = scope; if (scope !== "total") fillList($("sg-key-list"), scope).catch(() => {}); }
  chips($("sg-chips"), [{ t: `Segment: ${lvl(seg)}`, fixed: true }, scope !== "total" && skey ? { t: `${lvl(scope)}: ${skey}`, clear: () => setParams({ scope: "total", skey: "" }) } : { t: "Scope: total market", fixed: true }], () => go("therapy", { seg }));
  crumbs({ t: "Market Intelligence", h: href("market") }, { t: "Segments & mix", h: href("therapy") }, { t: lvl(seg) });
  const els = ["sg-mix", "sg-growth", "sg-table"].map($);
  if (scope !== "total" && !skey) { els.forEach((el) => S.empty(el, `Choose a ${lvl(scope).toLowerCase()}`, "Pick a scope member and apply, or switch the scope to the total market.")); return; }
  const args = { segment: seg, ...gp() };
  if (scope !== "total") Object.assign(args, { market_level: scope, market_key: skey });
  const p = tool("get_segment_analysis", args);
  const scopeName = scope === "total" ? "the total market" : skey;
  load(els[0], "chart", p, (res) => {
    metaChips($("sg-meta"), periodChips(res.period));
    mixChart(els[0], res.rows.slice().sort(byDesc("value_cur")), `${lvl(seg)} mix of ${scopeName}: share now vs a year earlier`);
    src($("sg-mix-src"), "get_segment_analysis", `${esc(lvl(seg))} within ${esc(scopeName)}`);
  });
  load(els[1], "chart", p, (res) => {
    const rows = res.rows.slice().sort(byDesc("value_cur"));
    barChart(els[1], { items: rows.map((r) => ({ key: r.entity_key, label: r.entity_label, v: r.value_growth_pct })), fmt: T.pct, labelW: 150,
      desc: `Value growth by ${lvl(seg)} member`,
      tip: (i) => { const r = rows[i]; return tipRows(r.entity_label, [["Growth", T.pct(r.value_growth_pct)], ["Value", T.cr(r.value_cur)], ["Share", T.share(r.value_share_pct)]], r.value_growth_status !== "ok" ? NA_REASON[r.value_growth_status] : ""); } });
  });
  load(els[2], "table", p, (res) => dataTable(els[2], { id: `sg-${seg}`, rows: res.rows, auto: true, sort: ["value_cur", "desc"], caption: "Segment detail",
    cols: [colEnt(lvl(seg)), colValue, colGrowth, colShare, { k: "value_share_prior_pct", label: "Share a year earlier", num: true, sort: (r) => r.value_share_prior_pct, html: (r) => val(r.value_share_prior_pct, T.share) }, colShareChg, colContrib, colUnits] }));
}

// ================================================================== 7.4 BRAND & PORTFOLIO
async function pageProduct(params) {
  const code = params.code || "";
  const q = (params.q || "").trim();
  const area = params.area || "";
  if (document.activeElement !== $("pr-q")) $("pr-q").value = q;
  $("pr-area").value = area;
  chips($("pr-chips"), [
    q && { t: `Search: “${q}”`, clear: () => setParams({ q: "" }) },
    code && { t: `Product code ${code}`, clear: () => setParams({ code: "", mk: "" }) },
    !code && { t: area ? `Leaderboard: ${area}` : "Leaderboard: national", fixed: true },
  ], () => go("product", {}));
  $("pr-results-wrap").classList.toggle("hidden", !q);
  $("pr-board").classList.toggle("hidden", !!code);
  $("pr-detail").classList.toggle("hidden", !code);
  crumbs({ t: "Brand & Portfolio", h: href("product") }, code ? { t: `Product ${code}` } : q ? { t: `Search “${q}”` } : null);
  if (q) productSearch(q, code);
  if (code) productDetail(code);                       // tool results are cached: re-running is cheap and keeps panels consistent
  if (code) productMarket(code, params.mk || "");
  else once($("pr-board"), `${periodSig()}|${area}`, () => productBoard(area));
}
function productSearch(q, code) {
  const el = $("pr-results");
  if (q.length < 2) { S.empty(el, "Type at least 2 characters", "Brand search is a literal, case-insensitive substring match."); return; }
  load(el, "table", tool("find_products", { name_contains: q, limit: 100 }), (res) => {
    const rows = res.rows;
    const byBrand = {};
    rows.forEach((r) => { const b = String(r.brand).toLowerCase(); byBrand[b] = (byBrand[b] || 0) + 1; });
    const exact = rows.filter((r) => String(r.brand).toLowerCase() === q.toLowerCase());
    const codes = new Set(exact.map((r) => r.prod_code));
    let lead = `${T.int(res.total_rows)} product${res.total_rows === 1 ? "" : "s"} match “${q}”${res.total_rows > rows.length ? ` (first ${rows.length} shown)` : ""}.`;
    if (codes.size > 1) lead = `“${q}” is a shared brand name: ${codes.size} different products (product codes) carry it. Choose the product you mean — the application never picks one for you.`;
    $("pr-results-q").textContent = lead;
    if (!rows.length) { S.empty(el, `No brand names contain “${q}”`, "Check the spelling or search a shorter fragment."); return; }
    const sorted = [...exact, ...rows.filter((r) => !exact.includes(r))];
    dataTable(el, {
      id: `pr-s-${q}`, rows: sorted, key: (r) => r.prod_code, selected: code, auto: false, caption: "Matching products",
      cols: [
        { k: "brand", label: "Brand (label)", asc: true, sort: (r) => r.brand, html: (r) => `<b>${esc(r.brand)}</b>${byBrand[String(r.brand).toLowerCase()] > 1 ? ' <span class="badge b-assumed" title="Several products share this brand name">Shared name</span>' : ""}` },
        { k: "company", label: "Company", asc: true, sort: (r) => r.company, html: (r) => esc(r.company) },
        { k: "prod_code", label: "Product code (key)", asc: true, sort: (r) => r.prod_code, html: (r) => `<span class="badge b-key">${esc(r.prod_code)}</span>` },
        { k: "prod_launch_month", label: "Launched", sort: (r) => r.prod_launch_month, html: (r) => esc(r.prod_launch_month ? monthLabel(r.prod_launch_month) : "unknown") },
      ],
      onSelect: (r) => setParams({ code: r.prod_code, mk: "" }, false),
    });
    $("pr-clarify").innerHTML = codes.size > 1 && !code ? `<div class="callout warn"><b>Clarification needed.</b> Several products share the brand name “${esc(q)}”. Their figures are never combined; select one product code to continue.</div>` : "";
  });
}
function productBoard(area) {
  const args = { ...gp(), market_level: area ? "supergroup" : undefined, market_key: area || undefined, top_n: 25 };
  const p = tool("get_brand_performance", args);
  load($("pr-board-table"), "table", p, (res) => {
    metaChips($("pr-meta"), periodChips(res.period));
    dataTable($("pr-board-table"), { id: `pr-board-${area}`, rows: res.rows, caption: "Product leaderboard", tall: true,
      cols: [colRank(), { k: "brand", label: "Product", asc: true, sort: (r) => r.brand, html: (r) => `<div class="ent"><b>${esc(r.brand)}</b><span>${esc(r.company)} · code ${esc(r.entity_key)}</span></div>` }, colValue, colGrowth, colShare, colShareChg, colEI],
      onSelect: (r) => setParams({ code: r.entity_key, mk: "" }, false) });
    src($("pr-board-src"), "get_brand_performance", `${area ? esc(area) : "national"} · top 25 by value`);
  });
  load($("pr-shift"), "chart", p, (res) => {
    const rows = res.rows.slice(0, 15);
    barChart($("pr-shift"), { items: rows.map((r) => ({ key: r.entity_key, label: `${r.brand} · ${r.company}`, v: r.value_share_chg_pp })), fmt: T.pp, labelW: 220,
      desc: "Share change of the 15 largest products", onSelect: (it) => setParams({ code: it.key, mk: "" }, false),
      tip: (i) => { const r = rows[i]; return tipRows(`${r.brand} (${r.company})`, [["Product code", r.entity_key], ["Share", T.share(r.value_share_pct)], ["Share change", T.pp(r.value_share_chg_pp)], ["EI", T.ei(r.evolution_index)]], "Ordered by value rank"); } });
  });
}
function productDetail(code) {
  const p = gp();
  if ($("pr-detail")._code !== code) {
    $("pr-detail")._code = code;
    $("h-pr-title").textContent = `Product ${code}`;
    $("pr-sub").textContent = "Loading product identity…";
    $("pr-identity").innerHTML = "";
  }
  const shareP = tool("get_brand_share", { prod_code: code, market_level: "total", market_key: "TOTAL", ...p });
  load($("pr-kpis"), "kpi", shareP, (sh) => {
    const s = sh.rows[0];
    metaChips($("pr-meta"), periodChips(sh.period));
    $("h-pr-title").textContent = s.brand;
    $("pr-sub").textContent = `${s.company} · national position, ${windowLabel(sh.period)}`;
    crumbs({ t: "Brand & Portfolio", h: href("product") }, { t: `${s.brand} (${code})` });
    const idn = (launch, shared) => {
      $("pr-identity").innerHTML = `<div><div class="l">Product (key)</div><div class="v key">${esc(code)}</div></div>`
        + `<div><div class="l">Brand (label)</div><div class="v">${esc(s.brand)}${shared > 1 ? ` <span class="badge b-assumed" title="${esc(`${shared} products carry this brand name`)}">Shared name</span>` : ""}</div></div>`
        + `<div><div class="l">Company</div><div class="v"><a href="${esc(href("company", { key: s.company }))}">${esc(s.company)}</a></div></div>`
        + `<div><div class="l">Launched</div><div class="v">${esc(launch)}</div></div>`;
    };
    idn("…", 0);
    if (String(s.brand).length >= 2) {
      tool("find_products", { name_contains: s.brand, limit: 100 }).then((f) => {
        const same = f.rows.filter((r) => String(r.brand).toLowerCase() === String(s.brand).toLowerCase());
        const me = f.rows.find((r) => String(r.prod_code) === String(code));
        idn(me && me.prod_launch_month ? monthLabel(me.prod_launch_month) : "unknown", new Set(same.map((r) => r.prod_code)).size);
      }).catch(() => idn("unknown", 0));
    }
    $("pr-actions").innerHTML = `<a class="btn btn-sm" href="${esc(href("scenario", { type: "PRICE_CHANGE", etype: "product", ekey: code }))}">Model a price change</a>`
      + `<a class="btn btn-sm" href="${esc(`/agent?q=${encodeURIComponent(`product ${code} performance`)}`)}">Ask the AI Analyst</a>`;
    kpiStrip($("pr-kpis"), [
      ["Value (national)", esc(T.cr(s.value_cur)), `${esc(T.crAbs(s.value_abs_chg))} vs prior`],
      ["Growth", g(s), "value, year on year"],
      ["National share", esc(T.share(s.value_share_pct, 3)), delta(s.value_share_chg_pp, T.pp, NA_REASON[s.value_growth_status])],
      ["Evolution index", val(s.evolution_index, T.ei), "vs national market"],
      ["National rank", `${esc(s.rank_value)}`, `of ${esc(T.int(s.n_entities))} products`],
      ["Units", esc(T.k(s.units_cur)), delta(s.units_growth_pct, T.pct, NA_REASON[s.value_growth_status])],
    ]);
    // markets where it competes: product-in-subgroup rows (opportunity engine carries the subgroup context)
    const mp = tool("get_opportunity_scores", { level: "product", anchor: p.anchor, basis: oppBasis(), company: s.company, include_insufficient: true, top_n: null });
    load($("pr-markets"), "table", mp, (op) => {
      const rows = op.rows.filter((r) => String(r.prod_code) === String(code)).sort(byDesc("value_cur"));
      state.prMarkets = { code, rows, op, sig: periodSig() };
      if (state.page === "product" && state.params.code === code) productMarket(code, state.params.mk || "");
    });
  }).then((d) => { if (!d) { $("h-pr-title").textContent = `Product ${code}`; $("pr-sub").textContent = ""; $("pr-identity").innerHTML = ""; ["pr-trend", "pr-pos", "pr-markets"].forEach((id) => S.empty($(id), "Product not available", "")); } });
}
function renderProductMarkets() {
  const st = state.prMarkets;
  if (!st) return;
  const mk = state.params.mk || (st.rows[0] && st.rows[0].subgroup);
  dataTable($("pr-markets"), {
    id: `pr-mk-${st.code}`, rows: st.rows, key: (r) => r.subgroup, selected: mk, auto: true, caption: "Markets where the product competes",
    emptyT: "No market positions for this period", emptyD: "The product has no sales in the selected window.",
    cols: [
      { k: "subgroup", label: "Therapy subgroup (market)", asc: true, sort: (r) => r.subgroup, html: (r) => `<div class="ent"><b>${esc(r.subgroup)}</b><span>${esc(r.supergroup)}</span></div>` },
      colValue, colGrowth,
      { k: "value_share_in_market_pct", label: "Share in market", num: true, sort: (r) => r.value_share_in_market_pct, html: (r) => val(r.value_share_in_market_pct, T.share) },
      colEI,
      { k: "market_value_growth_pct", label: "Market growth", num: true, sort: (r) => r.market_value_growth_pct, html: (r) => delta(r.market_value_growth_pct) },
      { k: "score", label: "Opportunity", num: true, title: "Descriptive opportunity score (0–100)", sort: (r) => r.score, html: (r) => (isNum(r.score) ? `<b>${esc(T.score(r.score))}</b>` : `<span class="badge b-insufficient" title="${esc(r.insufficient_reason || "")}">Not scored</span>`) },
      { k: "matrix_quadrant", label: "Matrix position", wrap: true, sort: (r) => r.matrix_quadrant, html: (r) => esc(r.matrix_quadrant || "—") },
    ],
    onSelect: (r) => setParams({ mk: r.subgroup }, false),
  });
  src($("pr-markets-src"), "get_opportunity_scores", `product-in-subgroup rows for this product · ${esc(st.op.period.basis_label)}${state.basis === "MONTH" ? " (Month selected: market positions use MAT)" : ""} · ${esc(st.op.methodology.version)}`);
}
function productMarket(code, explicitMk) {
  const st = state.prMarkets && state.prMarkets.code === code && state.prMarkets.sig === periodSig() ? state.prMarkets : null;
  if (st) renderProductMarkets();
  // the trend stays national unless the user explicitly selects a market
  const mk0 = explicitMk;
  once($("pr-trend"), `${periodSig()}|${code}|${mk0}`, () => {
    const mk = mk0;
    const args = mk ? { prod_code: code, market_level: "subgroup", market_key: mk } : { prod_code: code };
    $("pr-trend-q").textContent = mk ? `Trend of the product's packs within ${mk}.` : "National trend across all the product's markets.";
    load($("pr-trend"), "chart", tool("get_brand_growth", args), (tr) => {
      trendPanel($("pr-trend-tools"), $("pr-trend"), tr.rows, mk ? `Product ${code} in ${mk}` : `Product ${code}`);
      src($("pr-trend-src"), "get_brand_growth", mk ? `restricted to ${esc(mk)}` : "all markets");
    });
  });
  const el = $("pr-pos");
  const mk = explicitMk || (st && st.rows[0] ? st.rows[0].subgroup : "");
  const auto = !explicitMk && !!mk;
  if (!mk) {
    if (!st) { S.loading(el, "kpi"); el._sig = null; }
    else S.empty(el, "No market positions", "The product has no sales in any therapy subgroup in this window.");
    return;
  }
  once(el, `${periodSig()}|${code}|${mk}`, () => load(el, "kpi", tool("get_brand_share", { prod_code: code, market_level: "subgroup", market_key: mk, ...gp() }), (sh) => {
    const s = sh.rows[0];
    const opRow = state.prMarkets && state.prMarkets.rows.find((r) => r.subgroup === mk);
    el.innerHTML = `<p class="small"><b>${esc(mk)}</b>${auto ? ' <span class="muted">(largest market; select another below)</span>' : ""}</p><div id="pr-pos-k"></div>
      <div class="row">
        <a class="btn btn-sm" href="${esc(href("market", { level: "subgroup", key: mk }))}">Open market</a>
        ${opRow ? `<button class="btn btn-sm" type="button" id="pr-pos-ev">Opportunity evidence</button>` : ""}
        <a class="btn btn-sm" href="${esc(href("scenario", { type: "MARKET_SHARE", etype: "product", ekey: code, ml: "subgroup", mk }))}">Model a share target</a>
      </div>`;
    kpiStrip($("pr-pos-k"), [
      ["Share in market", esc(T.share(s.value_share_pct)), delta(s.value_share_chg_pp, T.pp, NA_REASON[s.value_growth_status])],
      ["Rank in market", esc(s.rank_value), `of ${esc(T.int(s.n_entities))} products`],
      ["Evolution index", val(s.evolution_index, T.ei), "vs this market"],
      ["Contribution", delta(s.contribution_to_growth_pp, T.pp), "pp of market growth"],
      ["Market value", esc(T.cr(s.scope_value_cur)), opRow ? `market growth ${delta(opRow.market_value_growth_pct)}` : ""],
    ]);
    const ev = $("pr-pos-ev");
    if (ev) ev.addEventListener("click", () => oppDrawer("product", opRow.entity_key, opRow.entity_label));
    src($("pr-pos-src"), "get_brand_share", `${esc(mk)} · ${esc(windowLabel(sh.period))}`);
  }));
}

// ================================================================== 7.5 COMPANY INTELLIGENCE
async function pageCompany(params) {
  const scopes = state.meta.company_scopes;
  const scope = scopes.includes(params.scope) ? params.scope : "total";
  const skey = scope === "total" ? "" : (params.skey || "");
  const key = params.key || "";
  const q = (params.q || "").trim();
  $("co-scope").value = scope;
  $("co-key-wrap").classList.toggle("hidden", scope === "total");
  $("co-run").classList.toggle("hidden", scope === "total");
  if (document.activeElement !== $("co-key")) $("co-key").value = skey;
  if (document.activeElement !== $("co-filter")) $("co-filter").value = q;
  if ($("co-key-list")._type !== scope) { $("co-key-list")._type = scope; if (scope !== "total") fillList($("co-key-list"), scope).catch(() => {}); }
  chips($("co-chips"), [
    scope !== "total" && skey ? { t: `${lvl(scope)}: ${skey}`, clear: () => setParams({ scope: "total", skey: "" }) } : { t: "Scope: total market", fixed: true },
    q && { t: `Name contains “${q}”`, clear: () => setParams({ q: "" }) },
    key && { t: `Company: ${key}`, clear: () => setParams({ key: "" }) },
  ], () => go("company", {}));
  crumbs({ t: "Company Intelligence", h: href("company") }, scope !== "total" && skey ? { t: `${lvl(scope)}: ${skey}`, h: href("company", { scope, skey }) } : null, key ? { t: key } : null);
  if (scope !== "total" && !skey) {
    ["co-table", "co-shift"].forEach((id) => S.empty($(id), `Choose a ${lvl(scope).toLowerCase()}`, "Pick a scope member and apply, or switch back to the total market."));
    $("co-detail").classList.add("hidden");
    return;
  }
  const args = { ...gp(), market_level: scope === "total" ? undefined : scope, market_key: skey || undefined, top_n: null };
  const res = await load($("co-table"), "table", tool("get_company_performance", args), () => {});
  if (!res) { S.empty($("co-shift"), "No companies", ""); $("co-detail").classList.add("hidden"); return; }
  metaChips($("co-meta"), periodChips(res.period));
  const rows = q ? res.rows.filter((r) => String(r.entity_label).toLowerCase().includes(q.toLowerCase())) : res.rows;
  dataTable($("co-table"), {
    id: `co-${scope}-${skey}`, rows, selected: key, tall: true, page: 50, sort: ["value_cur", "desc"], caption: "Company ranking",
    emptyT: q ? `No company names contain “${q}”` : "No companies in this scope", emptyD: "Clear the filter or change the scope.",
    cols: [colRank(), colEnt("Company", (r) => esc(r.indian_mnc || "")), colValue, colGrowth, colShare, colShareChg, colEI],
    onSelect: (r) => setParams({ key: r.entity_key }, false),
  });
  src($("co-table-src"), "get_company_performance", `${scope === "total" ? "total market" : `${esc(lvl(scope))} ${esc(skey)}`} · ${T.int(res.total_rows)} companies`);
  const top = res.rows.slice().sort(byDesc("value_cur")).slice(0, 15);
  barChart($("co-shift"), { items: top.map((r) => ({ key: r.entity_key, label: r.entity_label, v: r.value_share_chg_pp })), fmt: T.pp, labelW: 190, selected: key,
    desc: "Share change of the 15 largest companies in the scope", onSelect: (it) => setParams({ key: it.key }, false),
    tip: (i) => { const r = top[i]; return tipRows(`${r.entity_label} (#${r.rank_value})`, [["Share", T.share(r.value_share_pct)], ["Share change", T.pp(r.value_share_chg_pp)], ["EI", T.ei(r.evolution_index)], ["Growth", T.pct(r.value_growth_pct)]], "Ordered by value"); } });
  const row = key && res.rows.find((r) => r.entity_key === key);
  $("co-detail").classList.toggle("hidden", !key);
  if (!key) return;
  if (!row) {
    $("co-detail").classList.remove("hidden");
    $("h-co-title").textContent = key;
    $("co-sub").textContent = "";
    S.empty($("co-kpis"), "This company has no sales in the selected scope", "Switch the scope to the total market to see its full profile.");
    ["co-mix", "co-products", "co-positions"].forEach((id) => S.empty($(id), "No data in scope", ""));
    return;
  }
  once($("co-detail"), `${periodSig()}|${scope}|${skey}|${key}`, () => companyDetail(row, scope, skey, res));
}
function companyDetail(r, scope, skey, res) {
  const scopeName = scope === "total" ? "the total market" : skey;
  $("h-co-title").textContent = r.entity_label;
  $("co-sub").textContent = `${r.indian_mnc ? `${r.indian_mnc} company · ` : ""}rank ${r.rank_value} of ${T.int(r.n_entities)} in ${scopeName} · ${windowLabel(res.period)}`;
  $("co-actions").innerHTML = `<a class="btn btn-sm" href="${esc(href("opportunity", { company: r.entity_key }))}">Opportunity evidence</a>`
    + `<a class="btn btn-sm" href="${esc(href("scenario", { type: "MARKET_SHARE", etype: "company", ekey: r.entity_key, ml: scope === "total" ? "total" : scope, mk: scope === "total" ? "TOTAL" : skey }))}">Model a share target</a>`;
  kpiStrip($("co-kpis"), [
    ["Value", esc(T.cr(r.value_cur)), `${esc(T.crAbs(r.value_abs_chg))} vs prior`],
    ["Growth", g(r), "value, year on year"],
    [`Share of ${scope === "total" ? "market" : "scope"}`, esc(T.share(r.value_share_pct)), delta(r.value_share_chg_pp, T.pp, NA_REASON[r.value_growth_status])],
    ["Evolution index", val(r.evolution_index, T.ei), "100 = holding share"],
    ["Contribution", delta(r.contribution_to_growth_pp, T.pp), "pp of scope growth"],
    ["Units", esc(T.k(r.units_cur)), delta(r.units_growth_pct, T.pct, NA_REASON[r.value_growth_status])],
  ]);
  const drawMix = () => {
    const seg = $("co-mix-seg").value;
    load($("co-mix"), "chart", tool("get_segment_analysis", { segment: seg, ...gp(), market_level: "company", market_key: r.entity_key }), (m) => {
      mixChart($("co-mix"), m.rows.slice().sort(byDesc("value_cur")), `${lvl(seg)} composition of ${r.entity_label}'s national portfolio`);
      src($("co-mix-src"), "get_segment_analysis", `${esc(lvl(seg))} · company scope (national)`);
    });
  };
  $("co-mix-seg").onchange = drawMix;
  drawMix();
  $("co-products-q").textContent = `${r.entity_label}'s largest products; shares are of ${scopeName}.`;
  load($("co-products"), "table", tool("get_brand_performance", { ...gp(), market_level: scope === "total" ? undefined : scope, market_key: skey || undefined, company: r.entity_key, top_n: 15 }), (pr) => {
    dataTable($("co-products"), { id: `co-pr-${r.entity_key}`, rows: pr.rows, auto: true, caption: "Company's leading products",
      cols: [colRank(), { k: "brand", label: "Product", asc: true, sort: (x) => x.brand, html: (x) => `<div class="ent"><b>${esc(x.brand)}</b><span>code ${esc(x.entity_key)}</span></div>` }, colValue, colGrowth, colShare, colShareChg],
      onSelect: (x) => go("product", { code: x.entity_key }) });
    src($("co-products-src"), "get_brand_performance", `company filter · shares of ${esc(scopeName)} · rank is within the scope`);
  });
  load($("co-positions"), "table", tool("get_opportunity_scores", { level: "product", anchor: state.anchor, basis: oppBasis(), company: r.entity_key, include_insufficient: true, top_n: null }), (op) => {
    const rows = op.rows.slice().sort(byDesc("value_cur")).slice(0, 25);
    dataTable($("co-positions"), { id: `co-pos-${r.entity_key}`, rows, auto: true, caption: "Largest product-in-market positions",
      cols: [
        { k: "brand", label: "Product", asc: true, sort: (x) => x.brand, html: (x) => `<div class="ent"><b>${esc(x.brand)}</b><span>code ${esc(x.prod_code)}</span></div>` },
        { k: "subgroup", label: "Market (subgroup)", asc: true, sort: (x) => x.subgroup, html: (x) => `<div class="ent"><b>${esc(x.subgroup)}</b><span>${esc(x.supergroup)}</span></div>` },
        colValue,
        { k: "value_share_in_market_pct", label: "Share in market", num: true, sort: (x) => x.value_share_in_market_pct, html: (x) => val(x.value_share_in_market_pct, T.share) },
        colEI,
        { k: "market_value_growth_pct", label: "Market growth", num: true, sort: (x) => x.market_value_growth_pct, html: (x) => delta(x.market_value_growth_pct) },
        { k: "score", label: "Opportunity", num: true, sort: (x) => x.score, html: (x) => (isNum(x.score) ? `<b>${esc(T.score(x.score))}</b>` : `<span class="badge b-insufficient" title="${esc(x.insufficient_reason || "")}">Not scored</span>`) },
      ],
      onSelect: (x) => go("product", { code: x.prod_code, mk: x.subgroup }) });
    src($("co-positions-src"), "get_opportunity_scores", `25 largest product-in-subgroup positions · ${esc(op.period.basis_label)}${state.basis === "MONTH" ? " (Month selected: uses MAT)" : ""} · ${esc(op.methodology.version)}`);
  });
}

// ================================================================== 7.6 OPPORTUNITY INTELLIGENCE
const QUADS = [
  ["Underperforming in faster-growing market", "Losing share in a market growing faster than the national market"],
  ["Outperforming in faster-growing market", "Gaining share (EI ≥ 100) in a market growing faster than the national market"],
  ["Underperforming in slower-growing market", "Losing share in a market growing slower than the national market"],
  ["Outperforming in slower-growing market", "Gaining share in a market growing slower than the national market"],
];
function oppMatrix(el, res, o = {}) {
  const level = res.filters.level;
  const rows = res.rows;
  const nat = res.national_value_growth_pct;
  const eiRef = res.methodology.thresholds.matrix_ei_threshold;
  const legend = `<div class="legend"><span><i class="sw sc1"></i>Score 0–20</span><span><i class="sw sc3"></i>40–60</span><span><i class="sw sc5"></i>80–100</span><span><i class="sw hollow"></i>Beyond axis range (true value in tooltip)</span><span>Size = value</span></div>`;
  const vmax = Math.max(...rows.map((r) => (isNum(r.value_cur) ? r.value_cur : 0)), 1e-9);
  const rad = (v) => (o.compact ? 3 : 3.5) + (o.compact ? 6 : 9) * Math.sqrt(Math.max(0, isNum(v) ? v : 0) / vmax);
  const dim = (r) => (o.quad ? r.matrix_quadrant !== o.quad : false);
  if (level === "product") {
    const pts = rows.filter((r) => isNum(r.score)).map((r) => ({ key: r.entity_key, x: r.evolution_index, y: r.market_value_growth_pct, r: rad(r.value_cur), cls: scoreClass(r.score), dim: dim(r), row: r,
      aria: `${r.brand} (${r.company}) in ${r.subgroup}: score ${T.score(r.score)}, evolution index ${T.ei(r.evolution_index)}, market growth ${T.pct(r.market_value_growth_pct)}` }));
    const xs = pts.map((p) => p.x).filter((v) => isNum(v) && v > 0), ys = pts.map((p) => p.y).filter(isNum);
    const xa = Math.max(1, Math.min(quantile(xs, 0.01) || 50, eiRef * 0.8)), xb = Math.min(25000, Math.max(quantile(xs, 0.99) || 150, eiRef * 1.25));
    let ya = Math.min(quantile(ys, 0.01) ?? 0, isNum(nat) ? nat : 0, 0), yb = Math.max(quantile(ys, 0.99) ?? 10, isNum(nat) ? nat : 0, 10);
    ya = ya < 0 ? ya * 1.15 : ya; yb *= 1.15;
    const notPlotted = rows.length - pts.length;
    scatter(el, {
      pts, xDom: [xa * 0.9, xb * 1.1], yDom: [ya, yb], xLog: true, ySym: true, xRef: eiRef, yRef: nat, yRefLabel: isNum(nat) ? `National growth ${T.pct(nat)}` : "",
      xLabel: "Relative momentum — evolution index (log scale; 100 = holding share)", yLabel: "Market growth (%, compressed scale)", yTick: (t) => `${loc(t, 0)}%`, xTick: (t) => loc(t, t < 10 ? 1 : 0),
      quad: o.compact ? null : ["Underperforming · faster market", "Outperforming · faster market", "Underperforming · slower market", "Outperforming · slower market"],
      legend: o.compact ? "" : legend, height: o.compact ? 300 : undefined, selected: o.selected, onSelect: o.onSelect,
      desc: `Opportunity matrix: ${pts.length} scored products-in-market by evolution index and market growth`,
      note: `${o.compact ? "Colour = score band; size = value. " : ""}Dashed lines: EI ${eiRef} and national market growth ${isNum(nat) ? T.pct(nat) : "n/a"}.${notPlotted ? ` ${notPlotted} insufficient-evidence row${notPlotted === 1 ? " is" : "s are"} not plotted (not scored, not zero).` : ""}`,
      tip: (p) => { const r = p.row; return tipRows(`${r.brand} · ${r.company}`, [["Market", r.subgroup], ["Score", T.score(r.score)], ["Evolution index", T.ei(r.evolution_index)], ["Market growth", T.pct(r.market_value_growth_pct)], ["Share in market", T.share(r.value_share_in_market_pct)], ["Value", T.cr(r.value_cur)]], r.matrix_quadrant || ""); },
    });
  } else {
    const pts = rows.filter((r) => isNum(r.score)).map((r) => ({ key: r.entity_key, x: r.value_growth_pct, y: r.value_share_of_total_pct, r: rad(r.value_cur), cls: scoreClass(r.score), row: r,
      aria: `${r.entity_label}: score ${T.score(r.score)}, market growth ${T.pct(r.value_growth_pct)}, share of total ${T.share(r.value_share_of_total_pct)}` }));
    const xs = pts.map((p) => p.x).filter(isNum), ys = pts.map((p) => p.y).filter((v) => isNum(v) && v > 0);
    let xa = Math.min(quantile(xs, 0.03) ?? -10, isNum(nat) ? nat : 0, 0), xb = Math.max(quantile(xs, 0.97) ?? 20, isNum(nat) ? nat : 0);
    const pad = (xb - xa) * 0.08 || 1; xa -= pad; xb += pad;
    const ya = 0, yb = Math.max(quantile(ys, 0.97) || 1, 0.1) * 1.1;
    scatter(el, {
      pts, xDom: [xa, xb], yDom: [ya, yb], xRef: nat, xLabel: "Market value growth (%)", yLabel: "Share of total market (%)", xTick: (t) => `${loc(t, 0)}%`, yTick: (t) => `${t}%`,
      legend: o.compact ? "" : legend, selected: o.selected, onSelect: o.onSelect,
      desc: `Market opportunity map: ${pts.length} scored subgroups by growth and size`,
      note: `Dashed line: national market growth ${isNum(nat) ? T.pct(nat) : "n/a"}. Market-level scores use market growth and market size; the four-quadrant matrix applies to products-in-market.`,
      tip: (p) => { const r = p.row; return tipRows(r.entity_label, [["Score", T.score(r.score)], ["Market growth", T.pct(r.value_growth_pct)], ["Share of total", T.share(r.value_share_of_total_pct)], ["Value", T.cr(r.value_cur)], ["Therapy area", r.supergroup]]); },
    });
  }
}
async function pageOpportunity(params) {
  const level = params.level === "market" ? "market" : "product";
  const area = params.area || "";
  const company = level === "product" ? (params.company || "") : "";
  const top = ["50", "100", "250", "500"].includes(params.top) ? +params.top : 100;
  const ins = params.ins === "1";
  const quad = level === "product" ? (params.quad || "") : "";
  setSeg($("op-level"), level);
  $("op-area").value = area; $("op-top").value = String(top); $("op-ins").checked = ins;
  $("op-company-wrap").classList.toggle("hidden", level !== "product");
  if (document.activeElement !== $("op-company")) $("op-company").value = company;
  if (!$("op-company-list")._filled) { $("op-company-list")._filled = true; fillList($("op-company-list"), "company").catch(() => { $("op-company-list")._filled = false; }); }
  chips($("op-chips"), [
    { t: level === "product" ? "Products in market" : "Markets (subgroups)", fixed: true },
    { t: `Basis: ${BASIS_SHORT[oppBasis()]}${state.basis === "MONTH" ? " (Month is not allowed for scoring)" : ""}`, fixed: true },
    area && { t: `Therapy area: ${area}`, clear: () => setParams({ area: "" }) },
    company && { t: `Company: ${company}`, clear: () => setParams({ company: "" }) },
    ins && { t: "Including insufficient evidence", clear: () => setParams({ ins: "" }) },
    quad && { t: `Quadrant: ${quad}`, clear: () => setParams({ quad: "" }) },
  ], () => go("opportunity", { level }));
  crumbs({ t: "Opportunity Intelligence", h: href("opportunity") }, { t: level === "product" ? "Products in market" : "Markets" });
  const args = { level, anchor: state.anchor, basis: oppBasis(), market_level: area ? "supergroup" : undefined, market_key: area || undefined, company: company || undefined, include_insufficient: ins, top_n: top };
  const res = await load($("op-list"), "table", tool("get_opportunity_scores", args), () => {});
  if (!res) { ["op-matrix", "op-quads", "op-method"].forEach((id) => S.empty($(id), "No results", "")); return; }
  const m = res.methodology;
  metaChips($("op-meta"), [`<b>${esc(m.version)}</b> · fingerprint <code>${esc(m.fingerprint)}</code>`, `${esc(res.period.basis_label)} · ${esc(windowLabel(res.period))}`]);
  $("op-matrix-q").textContent = level === "product"
    ? "Where is the evidence strongest? Relative momentum (x) against market growth (y); the shaded quadrant gains share in markets growing faster than the national market."
    : "Which markets combine growth and size? Market growth (x) against share of the total market (y).";
  const visible = quad ? res.rows.filter((r) => r.matrix_quadrant === quad) : res.rows;
  oppMatrix($("op-matrix"), res, { quad, selected: params.focus, onSelect: (pt) => oppDrawer(level, pt.key, pt.row.entity_label) });
  src($("op-matrix-src"), "get_opportunity_scores", `${T.int(res.row_count)} rows shown of ${T.int(res.total_rows)} matching · national reference population ${T.int(m.population_size)} scored`);

  // quadrants
  if (level === "product") {
    const counts = {};
    res.rows.forEach((r) => { if (r.matrix_quadrant) counts[r.matrix_quadrant] = (counts[r.matrix_quadrant] || 0) + 1; });
    $("op-quads").innerHTML = `<div class="quads">${QUADS.map(([name, d]) => `<button type="button" class="quad-btn${name.startsWith("Outperforming in faster") ? " hi" : ""}" data-q="${esc(name)}" aria-pressed="${quad === name}"><div class="c">${T.int(counts[name] || 0)}</div><div class="t"><b>${esc(name)}</b><br>${esc(d)}</div></button>`).join("")}</div>
      <p class="chart-note">Status counts for this selection: ${T.int(res.status_counts.SCORED)} scored, ${T.int(res.status_counts.INSUFFICIENT_EVIDENCE)} with insufficient evidence${ins ? "" : " (hidden; toggle “Include insufficient evidence” to list them)"}.</p>`;
    $$(".quad-btn", $("op-quads")).forEach((b) => b.addEventListener("click", () => setParams({ quad: quad === b.dataset.q ? "" : b.dataset.q })));
  } else {
    $("op-quads").innerHTML = `<div class="callout accent"><b>Market-level scoring</b> combines market growth and market size (percentiles within all scored subgroups). The four-quadrant matrix is defined for products in a market only.</div>
      <p class="chart-note">Status counts: ${T.int(res.status_counts.SCORED)} scored, ${T.int(res.status_counts.INSUFFICIENT_EVIDENCE)} with insufficient evidence.</p>`;
  }

  dataTable($("op-list"), {
    id: `op-${level}`, rows: visible, sort: ["opportunity_rank", "asc"], page: 50, tall: true, caption: "Ranked opportunities", selected: params.focus,
    emptyT: quad ? "No rows in this quadrant" : "No opportunities match", emptyD: quad ? "Clear the quadrant filter or show more rows." : "Widen the filters (therapy area, company) or include insufficient evidence.",
    cols: [
      colRank("opportunity_rank"),
      level === "product"
        ? { k: "brand", label: "Product in market", asc: true, sort: (r) => r.brand, html: (r) => `<div class="ent"><b>${esc(r.brand)} · ${esc(r.company)}</b><span>${esc(r.subgroup)} · code ${esc(r.prod_code)}</span></div>` }
        : colEnt("Market (subgroup)", (r) => esc(r.supergroup || "")),
      { k: "score", label: "Score & decomposition", num: true, sort: (r) => r.score, html: (r) => scoreStack(r) },
      { k: "score_status", label: "Status", sort: (r) => r.score_status, html: (r) => (r.score_status === "SCORED" ? '<span class="badge b-scored">Scored</span>' : `<span class="badge b-insufficient" title="${esc(r.insufficient_reason || "")}">Insufficient evidence</span>`) },
      level === "product"
        ? { k: "matrix_quadrant", label: "Matrix position", wrap: true, sort: (r) => r.matrix_quadrant, html: (r) => esc(r.matrix_quadrant || "—") }
        : { k: "value_growth_pct", label: "Market growth", num: true, sort: (r) => r.value_growth_pct, html: (r) => delta(r.value_growth_pct) },
      { k: "positive_drivers", label: "Leading driver", wrap: true, html: (r) => (r.positive_drivers && r.positive_drivers.length ? esc(r.positive_drivers[0]) : `<span class="muted">${esc(r.insufficient_reason || "none above the driver threshold")}</span>`) },
    ],
    onSelect: (r) => oppDrawer(level, r.entity_key, r.entity_label),
  });
  $("op-legend").innerHTML = `<div class="legend">${m.components.map((c, i) => `<span><i class="sw cmp${i}"></i>${esc(`${c.label} (${Math.round(c.weight * 100)}%)`)}</span>`).join("")}<span>Points sum to the score (0–100)</span></div>`;
  src($("op-list-src"), "get_opportunity_scores", `ordered by opportunity rank · ${esc(m.version)} · ${esc(res.caveats[0] || "")}`);
  once($("op-method"), `${m.version}|${m.fingerprint}|${level}`, () => { $("op-method").innerHTML = methodologyBlock(m, res.caveats); });
  if (params.focus && !$("drawer").classList.contains("open")) {
    const fr = res.rows.find((r) => r.entity_key === params.focus);
    oppDrawer(level, params.focus, fr ? fr.entity_label : params.focus);
  }
}
function methodologyBlock(m, caveats) {
  const th = m.thresholds || {};
  return `<div class="grid"><div class="span-6"><dl class="kv"><dt>Version</dt><dd><b>${esc(m.version)}</b></dd><dt>Fingerprint</dt><dd><code>${esc(m.fingerprint)}</code></dd>
      <dt>Normalisation</dt><dd>${esc(m.normalization)} (0–1 percentile, higher = stronger evidence)</dd><dt>Reference population</dt><dd>${esc(m.reference_population)} · ${T.int(m.population_size)} scored</dd></dl></div>
    <div class="span-6"><table class="dt"><thead><tr><th>Component</th><th>Metric</th><th class="n">Weight</th></tr></thead><tbody>${m.components.map((c, i) => `<tr><td><span class="legend"><span><i class="sw cmp${i}"></i>${esc(c.label)}</span></span></td><td><code>${esc(c.metric)}</code></td><td class="n">${esc(Math.round(c.weight * 100))}%</td></tr>`).join("")}</tbody></table></div></div>
    <dl class="kv"><dt>Evidence thresholds</dt><dd>Minimum prior value ${esc(th.min_prior_value_cr)} ₹ cr · at least ${esc(th.min_active_products)} active products in the market (below these: <span class="badge b-insufficient">Insufficient evidence</span>, never zero)</dd>
      <dt>Drivers / constraints</dt><dd>Component percentile ≥ ${esc(th.driver_threshold)} is a positive driver; ≤ ${esc(th.constraint_threshold)} is a constraint</dd>
      <dt>Matrix</dt><dd>EI threshold ${esc(th.matrix_ei_threshold)}; market growth compared with the ${esc(th.matrix_market_growth_reference)} market</dd></dl>
    <div class="callout plain small">${(caveats || []).map(esc).join(" ")}</div>`;
}
function oppDrawer(level, key, label) {
  const body = openDrawer(level === "product" ? "Opportunity evidence · product in market" : "Opportunity evidence · market", label);
  if (state.page === "opportunity" && state.params.focus !== key) {
    state.params = { ...state.params, focus: key };
    history.replaceState(null, "", href("opportunity", state.params));
  }
  load(body, "block", tool("get_opportunity_detail", { level, entity_key: key, anchor: state.anchor, basis: oppBasis() }), (d) => {
    const r = d.rows[0];
    const scored = isNum(r.score);
    const links = level === "product"
      ? `<a class="btn btn-sm" href="${esc(href("product", { code: r.prod_code, mk: r.subgroup }))}">Product profile</a><a class="btn btn-sm" href="${esc(href("market", { level: "subgroup", key: r.subgroup }))}">Market</a>`
      : `<a class="btn btn-sm" href="${esc(href("market", { level: "subgroup", key: r.entity_key }))}">Market profile</a>`;
    const comp = (r.components || []).map((c, i) => `<tr><td><span class="legend"><span><i class="sw cmp${i}"></i>${esc(c.label)}</span></span></td><td class="n">${esc(c.metric === "value_share_in_market_pct" || c.metric === "value_share_of_total_pct" ? T.share(c.raw, 3) : c.metric === "evolution_index" ? T.ei(c.raw) : c.metric.includes("growth") ? T.pct(c.raw) : T.num(c.raw, 2))}</td><td class="n">${isNum(c.normalized) ? (c.normalized * 100).toFixed(1) : "—"}</td><td class="n">${esc(Math.round(c.weight * 100))}%</td><td class="n"><b>${isNum(c.weighted_contribution) ? c.weighted_contribution.toFixed(2) : "—"}</b></td></tr>`).join("");
    body.innerHTML = `
      <div class="row between"><div class="row">${scored ? `<span class="badge b-scored">Scored</span><span class="badge b-descriptive">Descriptive — not a forecast</span>` : `<span class="badge b-insufficient">Insufficient evidence</span>`}</div><div class="row">${links}</div></div>
      ${scored ? `<div class="kpis"><div class="kpi"><div class="l">Opportunity score</div><div class="v">${esc(T.score(r.score))}<span class="muted"> / 100</span></div></div><div class="kpi"><div class="l">National rank</div><div class="v">${esc(r.opportunity_rank ?? "—")}</div><div class="s">of ${T.int(d.methodology.population_size)} scored</div></div>${r.matrix_quadrant ? `<div class="kpi"><div class="l">Matrix position</div><div class="v small">${esc(r.matrix_quadrant)}</div></div>` : ""}</div>`
        : `<div class="state insufficient"><div class="ic">?</div><div class="t">Not scored — insufficient evidence</div><p>${esc(r.insufficient_reason || "The evidence thresholds are not met.")}</p><p class="small">This is not a score of zero: there is not enough observed evidence to rank this entity.</p></div>`}
      <h3>Why this score</h3>
      ${scored ? `<p class="small">Each component's raw metric is converted to a national percentile and weighted; the points sum to the score.</p><div class="tw auto"><table class="dt"><thead><tr><th>Component</th><th class="n">Observed</th><th class="n">Percentile</th><th class="n">Weight</th><th class="n">Points</th></tr></thead><tbody>${comp}</tbody></table></div>` : `<p class="small">Components are shown for transparency where available; no points are awarded.</p><div class="tw auto"><table class="dt"><thead><tr><th>Component</th><th class="n">Observed</th><th class="n">Percentile</th><th class="n">Weight</th><th class="n">Points</th></tr></thead><tbody>${comp}</tbody></table></div>`}
      <h3>Positive drivers</h3>${(r.positive_drivers || []).length ? `<ul>${r.positive_drivers.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<p class="muted">No component at or above the driver threshold.</p>'}
      <h3>Constraints &amp; limitations</h3>${(r.constraints || []).length ? `<ul>${r.constraints.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : '<p class="muted">None recorded.</p>'}
      <h3>Supporting evidence (observed)</h3>
      <dl class="kv">${level === "product"
        ? `<dt>Product</dt><dd>${esc(r.brand)} · ${esc(r.company)} · code ${esc(r.prod_code)}</dd><dt>Market</dt><dd>${esc(r.subgroup)} (${esc(r.supergroup)})</dd><dt>Value</dt><dd>${esc(T.cr(r.value_cur))} (prior ${esc(T.cr(r.value_prior))})</dd><dt>Growth</dt><dd>${delta(r.value_growth_pct)}</dd><dt>Market value</dt><dd>${esc(T.cr(r.market_value_cur))}, growth ${delta(r.market_value_growth_pct)}</dd><dt>Share in market</dt><dd>${esc(T.share(r.value_share_in_market_pct, 3))}</dd><dt>Evolution index</dt><dd>${esc(T.ei(r.evolution_index))}</dd><dt>Active products in market</dt><dd>${esc(T.int(r.active_products_in_market))}</dd>`
        : `<dt>Therapy area</dt><dd>${esc(r.supergroup)} · ${esc(r.therapy_group)} · ${esc(r.acute_chronic)}</dd><dt>Value</dt><dd>${esc(T.cr(r.value_cur))} (prior ${esc(T.cr(r.value_prior))})</dd><dt>Growth</dt><dd>${delta(r.value_growth_pct)}</dd><dt>Share of total market</dt><dd>${esc(T.share(r.value_share_of_total_pct, 3))}</dd><dt>Packs</dt><dd>${esc(T.int(r.n_packs))}</dd>`}
        <dt>Period</dt><dd>${esc(d.period.basis_label)} · ${esc(windowLabel(d.period))}</dd></dl>
      <h3>Methodology</h3><dl class="kv"><dt>Version</dt><dd>${esc(d.methodology.version)} · <code>${esc(d.methodology.fingerprint)}</code></dd><dt>Source</dt><dd><code>get_opportunity_detail</code></dd></dl>
      <p class="muted">${(d.caveats || []).map(esc).join(" ")}</p>`;
  });
}

// ================================================================== 7.7 SCENARIO PLANNING
const SC_TYPES = {
  PRICE_CHANGE: { t: "Price change", d: "₹ per pack moves by X% at constant volume", fields: ["price_change_pct"] },
  VOLUME_CHANGE: { t: "Volume change", d: "Units move by X% at constant price", fields: ["volume_change_pct"] },
  PRICE_VOLUME_CHANGE: { t: "Price and volume", d: "Both move; shows price, volume and interaction effects", fields: ["price_change_pct", "volume_change_pct"] },
  MARKET_GROWTH: { t: "Market growth", d: "A market grows by X% (market entities only)", fields: ["market_growth_pct"] },
  MARKET_SHARE: { t: "Market share target", d: "A product or company reaches a target share of a market", fields: ["target_share_pct", "market_growth_pct"] },
};
const SC_FIELD = {
  price_change_pct: { l: "Price change", unit: "%", min: -50, max: 50, step: 0.5, def: 5, hint: "Allowed by the engine: above −100% up to +1000%." },
  volume_change_pct: { l: "Volume change", unit: "%", min: -50, max: 50, step: 0.5, def: -2, hint: "Allowed: −100% to +1000%." },
  market_growth_pct: { l: "Market growth", unit: "%", min: -30, max: 60, step: 0.5, def: 5, hint: "Allowed: −100% to +1000%." },
  target_share_pct: { l: "Target share", unit: "%", min: 0, max: 100, step: 0.1, def: 10, hint: "Share of the selected market, 0–100%." },
};
const SC_PARAM = { price_change_pct: "p", volume_change_pct: "v", market_growth_pct: "g", target_share_pct: "s" };
const MARKET_ETYPES = ["total", "supergroup", "therapy_group", "subgroup", "molecule"];
function scEntityTypes(type) {
  if (type === "MARKET_GROWTH") return MARKET_ETYPES;
  if (type === "MARKET_SHARE") return ["product", "company"];
  return state.meta.entity_types;
}
async function pageScenario(params) {
  const type = SC_TYPES[params.type] ? params.type : "PRICE_CHANGE";
  const etypes = scEntityTypes(type);
  const etype = etypes.includes(params.etype) ? params.etype : (type === "MARKET_SHARE" ? "company" : "subgroup");
  const ekey = etype === "total" ? "TOTAL" : (params.ekey || "");
  const mls = ["", ...state.meta.market_levels, "company"];
  const ml = mls.includes(params.ml) ? params.ml : (type === "MARKET_SHARE" ? "total" : "");
  const mk = ml === "total" ? "TOTAL" : (params.mk || "");
  crumbs({ t: "Scenario Planning", h: href("scenario") }, { t: SC_TYPES[type].t });
  metaChips($("sc-meta"), [`<b>${esc(state.meta.scenario_methodology_version)}</b>`, `${esc(BASIS_SHORT[state.basis])} · period ending ${esc(monthLabel(state.anchor))}`]);
  // builder
  if ($("sc-types")._v !== type) {
    const hadFocus = $("sc-types").contains(document.activeElement);
    $("sc-types")._v = type;
    setTimeout(() => { if (hadFocus) { const c = $("sc-types").querySelector("input:checked"); if (c) c.focus(); } }, 0);
    $("sc-types").innerHTML = Object.entries(SC_TYPES).map(([k, v]) => `<label class="type-opt"><input type="radio" name="sc-type" value="${k}"${k === type ? " checked" : ""}><span><b>${esc(v.t)}</b><em>${esc(v.d)}</em></span></label>`).join("");
    $$("input", $("sc-types")).forEach((i) => i.addEventListener("change", () => {
      const nt = i.value, ets = scEntityTypes(nt);
      setParams({ type: nt, etype: ets.includes(etype) ? etype : (nt === "MARKET_SHARE" ? "company" : "subgroup"), ekey: ets.includes(etype) ? ekey : "", ml: nt === "MARKET_SHARE" ? (ml || "total") : ml, mk: nt === "MARKET_SHARE" ? (ml ? mk : "TOTAL") : mk, p: "", v: "", g: "", s: "" });
    }));
  }
  $("sc-etype").innerHTML = opt(etypes.map((e) => [e, lvl(e)]), etype);
  $("sc-key").disabled = etype === "total";
  if (document.activeElement !== $("sc-key")) $("sc-key").value = ekey;
  $("sc-key-hint").textContent = etype === "product" ? "Search by brand name below; the product code is the key." : etype === "product_subgroup" ? "Product-in-subgroup key (as shown in Opportunity Intelligence)." : etype === "manufacturer" ? "Manufacturer code." : etype === "total" ? "The national market." : "";
  if ($("sc-key-list")._type !== etype) { $("sc-key-list")._type = etype; fillList($("sc-key-list"), etype).catch(() => {}); }
  scPicker(etype);
  $("sc-ml").innerHTML = opt(mls.map((x) => [x, x ? lvl(x) : type === "MARKET_SHARE" ? "Choose a market" : "No restriction"]), ml);
  $("sc-mk-wrap").classList.toggle("hidden", !ml || ml === "total");
  if (document.activeElement !== $("sc-mk")) $("sc-mk").value = ml === "total" ? "" : mk;
  if ($("sc-mk-list")._type !== ml) { $("sc-mk-list")._type = ml; if (ml && ml !== "total") fillList($("sc-mk-list"), ml).catch(() => {}); }
  $("sc-scope-hint").textContent = type === "MARKET_SHARE" ? "Required: the market that is the share denominator." : "Optional: restricts the baseline to the entity's packs in that market.";
  // assumptions
  const fields = SC_TYPES[type].fields;
  if ($("sc-assump")._v !== type) {
    $("sc-assump")._v = type;
    $("sc-assump").innerHTML = fields.map((f) => {
      const d = SC_FIELD[f], optional = type === "MARKET_SHARE" && f === "market_growth_pct";
      return `<div class="field"><label for="sa-${f}">${esc(d.l)} (${d.unit})${optional ? ' <span class="muted">optional</span>' : ""}</label>
        <div class="range-row"><input type="range" id="sr-${f}" min="${d.min}" max="${d.max}" step="${d.step}" aria-label="${esc(d.l)} slider"><input type="number" class="input num" id="sa-${f}" step="any" inputmode="decimal"></div>
        <span class="hint">${esc(d.hint)}${optional ? " Leave blank for 0%." : ""}</span></div>`;
    }).join("");
    fields.forEach((f) => {
      const n = $(`sa-${f}`), r = $(`sr-${f}`);
      r.addEventListener("input", () => { n.value = r.value; n.removeAttribute("aria-invalid"); scLive(); });
      n.addEventListener("input", () => { if (n.value !== "" && isNum(Number(n.value))) r.value = clamp(Number(n.value), +r.min, +r.max); scLive(); });
    });
  }
  fields.forEach((f) => {
    const d = SC_FIELD[f], optional = type === "MARKET_SHARE" && f === "market_growth_pct";
    const pv = params[SC_PARAM[f]];
    const v = pv !== undefined ? pv : optional ? "" : String(d.def);
    if (document.activeElement !== $(`sa-${f}`)) $(`sa-${f}`).value = v;
    $(`sr-${f}`).value = v === "" ? (optional ? 0 : d.def) : clamp(Number(v), d.min, d.max);
  });
  await runScenario();
}
const scLive = debounce(() => { if ($("sc-live").checked) scSync(true); }, 280);
function scRead() {
  const type = $("sc-types").querySelector("input:checked")?.value || "PRICE_CHANGE";
  const etype = $("sc-etype").value;
  const out = { type, etype, ekey: etype === "total" ? "TOTAL" : $("sc-key").value.trim(), ml: $("sc-ml").value, mk: $("sc-ml").value === "total" ? "TOTAL" : $("sc-mk").value.trim() };
  SC_TYPES[type].fields.forEach((f) => { out[SC_PARAM[f]] = $(`sa-${f}`).value.trim(); });
  return out;
}
function scSync(replace) { go("scenario", scRead(), replace); }
function scPicker(etype) {
  const el = $("sc-picker");
  if (etype !== "product") { el.innerHTML = ""; el._init = false; return; }
  if (el._init) return;
  el._init = true;
  el.innerHTML = `<div class="row"><label class="sr-only" for="sc-psearch">Search brand name</label><input id="sc-psearch" class="input" type="search" placeholder="Find product by brand name" autocomplete="off"><button class="btn btn-sm" type="button" id="sc-pgo">Search</button></div><div id="sc-pres"></div>`;
  const run = () => {
    const q = $("sc-psearch").value.trim();
    const out = $("sc-pres");
    if (q.length < 2) { out.innerHTML = '<p class="hint">Type at least 2 characters.</p>'; return; }
    load(out, "block", tool("find_products", { name_contains: q, limit: 50 }), (res) => {
      if (!res.rows.length) { out.innerHTML = `<p class="hint">No brand names contain “${esc(q)}”.</p>`; return; }
      const codes = new Set(res.rows.filter((r) => String(r.brand).toLowerCase() === q.toLowerCase()).map((r) => r.prod_code));
      out.innerHTML = `${codes.size > 1 ? `<p class="hint">“${esc(q)}” is shared by ${codes.size} products — choose one; none is selected automatically.</p>` : ""}<div class="picker-results" role="listbox" aria-label="Matching products">${res.rows.map((r) => `<button type="button" role="option" data-code="${esc(r.prod_code)}"><b>${esc(r.brand)}</b> <span>· ${esc(r.company)} · code ${esc(r.prod_code)}</span></button>`).join("")}</div>`;
      $$("button[data-code]", out).forEach((b) => b.addEventListener("click", () => { $("sc-key").value = b.dataset.code; out.innerHTML = ""; scSync(false); }));
    });
  };
  $("sc-pgo").addEventListener("click", run);
  $("sc-psearch").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); run(); } });
}
async function runScenario() {
  const params = state.params;
  const type = SC_TYPES[params.type] ? params.type : "PRICE_CHANGE";
  const b = scRead();
  const out = $("sc-out");
  if (!b.ekey) { S.empty(out, "Choose an entity", `Pick a ${lvl(b.etype).toLowerCase()} in step 2 to calculate the scenario.`); $("sc-out-src").innerHTML = ""; return; }
  if (type === "MARKET_SHARE" && (!b.ml || (b.ml !== "total" && !b.mk))) { S.empty(out, "Choose the share denominator", "A market-share scenario needs the market in which the share is measured."); return; }
  const assumptions = {};
  let bad = null;
  SC_TYPES[type].fields.forEach((f) => {
    const raw = $(`sa-${f}`).value.trim();
    const optional = type === "MARKET_SHARE" && f === "market_growth_pct";
    if (raw === "") { if (!optional) bad = f; return; }
    const v = Number(raw);
    if (!isNum(v)) { bad = f; return; }
    assumptions[f] = v;                                    // passed unchanged: the engine validates (rejects, never corrects)
  });
  SC_TYPES[type].fields.forEach((f) => $(`sa-${f}`).removeAttribute("aria-invalid"));
  if (bad) { $(`sa-${bad}`).setAttribute("aria-invalid", "true"); S.msg(out, "error", "!", "Assumption needed", esc(`Enter a number for ${SC_FIELD[bad].l.toLowerCase()}.`), "INVALID_SCENARIO"); return; }
  const args = { scenario_type: type, entity_type: b.etype, entity_key: b.ekey, assumptions, ...gp() };
  if (b.ml) Object.assign(args, { market_level: b.ml, market_key: b.mk });
  $("sc-run").setAttribute("aria-busy", "true");
  await load(out, "block", tool("run_scenario", args), (r) => scenarioResult(out, r));
  $("sc-run").removeAttribute("aria-busy");
}
function scenarioResult(out, r) {
  const B = r.baseline, A = r.assumptions, C = r.scenario_result, D = r.absolute_change, P = r.percentage_change;
  const aText = Object.entries(A).filter(([k]) => SC_FIELD[k]).map(([k, v]) => `${SC_FIELD[k].l} ${k === "target_share_pct" ? T.share(v, 2) : T.pct(v, 2)}`).join(" · ") || "—";
  const measures = [
    ["Value|₹ crore", "value_cr", "value_cr", "value_pct", T.cr, T.crAbs],
    ["Units|'000 packs", "units_k", "units_k", "units_pct", T.k, T.kAbs],
    ["Counting units|'000", "qty_k", "qty_k", "qty_pct", T.k, T.kAbs],
    ["Price|₹ per pack", "price_rs_per_pack", "price_rs_per_pack", "price_pct", T.rs, T.rsAbs],
    ["Price per counting unit|₹", "price_rs_per_counting_unit", "price_rs_per_counting_unit", null, T.rs, T.rsAbs],
    ["Market value|₹ crore", "market_value_cr", "market_value_cr", "market_value_pct", T.cr, T.crAbs],
    ["Share of market|% of market value", "share_pct", "share_pp", null, (v) => T.share(v, 3), (v) => T.pp(v, 3)],
  ].filter(([, k]) => k in C);
  const body = measures.map(([l, k, dk, pk, f, fa], i) => `<tr><td>${esc(l.split("|")[0])}<span class="unit">${esc(l.split("|")[1])}</span></td><td>${val(B[k], f)}</td>${i === 0 ? `<td rowspan="${measures.length}" class="wrap">${esc(aText)}</td>` : ""}<td class="calc">${val(C[k], f)}</td><td>${dk in D ? delta(D[dk], fa) : '<span class="muted">—</span>'}</td><td>${pk && pk in P ? delta(P[pk], (v) => T.pct(v, 2)) : '<span class="muted">—</span>'}</td></tr>`).join("");
  const dec = D.value_decomposition_cr;
  out.innerHTML = `
    <div class="detail-head"><div><p class="eyebrow">${esc(SC_TYPES[r.scenario_type].t)} · ${esc(lvl(r.entity.entity_type))}</p><h2>${esc(r.entity.entity_label)}</h2>
      <p class="sub">${esc(r.period.basis_label)} · ${esc(monthLabel(r.period.window_start))} – ${esc(monthLabel(r.period.window_end))}${r.entity.scope ? ` · within ${esc(lvl(r.entity.scope.market_level))} ${esc(r.entity.scope.market_key)}` : ""}</p></div>
      <div class="row"><span class="badge b-observed">Observed baseline</span><span class="badge b-assumed">Assumed</span><span class="badge b-calculated">Calculated</span></div></div>
    <p class="small"><b>Scenario analysis — not a forecast.</b> The calculated result is arithmetic on the observed baseline under the stated assumption.</p>
    <div class="eq-wrap"><table class="equation"><thead><tr><th>Measure</th><th>Observed baseline</th><th>+ Assumption</th><th>= Calculated result</th><th>Absolute change</th><th>% change</th></tr></thead><tbody>${body}</tbody></table></div>
    <div class="grid" id="sc-charts"><div class="span-6"><h3 class="small muted">Baseline vs scenario value</h3><div id="sc-c1"></div></div><div class="span-6"><h3 class="small muted">${dec ? "What moves the value (₹ cr)" : "Change in value"}</h3><div id="sc-c2"></div></div></div>
    ${A.implicit_premises && A.implicit_premises.length ? `<div class="callout warn"><b>Implicit premises of this assumption</b><ul>${A.implicit_premises.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
    <details class="audit"><summary>Calculation basis, units and limitations</summary>
      <div class="grid"><div class="span-6"><h3 class="small muted">Calculation basis</h3><ul>${(r.calculation_basis || []).map((x) => `<li class="small">${esc(x)}</li>`).join("")}</ul></div>
      <div class="span-6"><h3 class="small muted">Assumptions &amp; limitations</h3><ul>${(r.assumptions_and_limitations || []).map((x) => `<li class="small">${esc(x)}</li>`).join("")}</ul></div></div>
      <dl class="kv">${Object.entries(r.units || {}).map(([k, v]) => `<dt><code>${esc(k)}</code></dt><dd>${esc(v)}</dd>`).join("")}</dl>
    </details>`;
  const pairs = [["Value", B.value_cr, C.value_cr]];
  if ("market_value_cr" in B) pairs.push(["Market value", B.market_value_cr, C.market_value_cr]);
  barChart($("sc-c1"), { items: pairs.flatMap(([l, b, c]) => [{ key: `${l}-b`, label: `${l} · baseline`, v: b, cls: "bar-prior" }, { key: `${l}-c`, label: `${l} · scenario`, v: c, cls: "bar-main" }]), fmt: T.cr, labelW: 140, valW: 110, desc: "Observed baseline value and calculated scenario value" });
  if (dec) barChart($("sc-c2"), { items: [["Price effect", dec.price_effect], ["Volume effect", dec.volume_effect], ["Interaction (price × volume)", dec.price_x_volume_interaction]].map(([l, v]) => ({ key: l, label: l, v })), fmt: T.crAbs, labelW: 170, valW: 110, desc: "Decomposition of the value change into price, volume and interaction effects (engine-calculated)" });
  else barChart($("sc-c2"), { items: [{ key: "d", label: "Value change", v: D.value_cr }, ...("market_value_cr" in D ? [{ key: "m", label: "Market value change", v: D.market_value_cr }] : [])], fmt: T.crAbs, labelW: 150, valW: 110, desc: "Absolute change in value" });
  src($("sc-out-src"), "run_scenario", `${esc(r.methodology_version)} · scenario id <code>${esc(r.scenario_id)}</code> · ${esc((r.evidence && r.evidence.baseline_source) || "")}`);
}

// ================================================================== 7.8 METHODOLOGY & DATA QUALITY
async function pageMethod(params) {
  if (params.s && $(params.s)) setTimeout(() => $(params.s).scrollIntoView({ behavior: "smooth", block: "start" }), 30);
  crumbs({ t: "Methodology & Data Quality", h: href("method") }, params.s ? { t: ($(params.s)?.querySelector("h2")?.textContent) || "" } : null);
  const m = state.meta;
  metaChips($("me-meta"), [`Data <b>${esc(monthLabel(m.periods[0]))} – ${esc(monthLabel(m.periods[m.periods.length - 1]))}</b> · ${m.periods.length} months`, `Tool API <b>${esc(m.tool_api_version)}</b>`]);
  if ($("page-method")._sig === periodSig()) return;
  $("page-method")._sig = periodSig();
  $("me-versions").innerHTML = `<dl class="kv"><dt>Opportunity scoring</dt><dd><b>${esc(m.opportunity_methodology_version)}</b> · fingerprint <code>${esc(m.opportunity_fingerprint)}</code></dd>
    <dt>Scenario engine</dt><dd><b>${esc(m.scenario_methodology_version)}</b></dd><dt>Tool API</dt><dd><b>${esc(m.tool_api_version)}</b></dd>
    <dt>Periods</dt><dd>${esc(monthLabel(m.periods[0]))} – ${esc(monthLabel(m.periods[m.periods.length - 1]))} (${m.periods.length} months)</dd></dl>`;
  load($("me-dq-live"), "block", tool("get_data_quality_status"), (res) => {
    const c = res.coverage || {};
    const st = (s) => `<span class="badge ${s === "PASS" ? "b-pass" : s === "FAIL" ? "b-fail" : "b-info"}">${esc(s)}</span>`;
    $("me-dq-live").innerHTML = `<p>${st(res.status)} ${esc(monthLabel(c.first_month))} – ${esc(monthLabel(c.last_month))} · ${esc(c.months)} months · ${esc(c.packs)} packs · geography: ${esc(c.geography)}</p>
      <div class="tw auto"><table class="dt"><thead><tr><th scope="col">Check</th><th scope="col">Result</th><th scope="col" class="n">Observed count</th><th scope="col" class="n">Expected</th></tr></thead><tbody>${
      (res.rows || []).map((r) => `<tr><td>${esc(r.check)}</td><td>${st(r.status)}</td><td class="n">${esc(r.observed_count)}</td><td class="n">${r.expected_count === null ? "—" : esc(r.expected_count)}</td></tr>`).join("")}</tbody></table></div>
      <p class="muted small">Tool: <code>get_data_quality_status</code> · counts only, no business metric</p>`;
  });
  load($("me-build"), "block", tool("get_market_performance", { level: "total", ...gp() }), (res) => {
    const ev = res.evidence || {};
    $("me-build").innerHTML = `<dl class="kv"><dt>Analytical build</dt><dd>${esc(ev.processed_built_at || "n/a")}</dd><dt>Source fingerprint</dt><dd><code>${esc(String(ev.source_sha256 || "").slice(0, 16))}</code> (SHA-256 prefix of the private source; the file itself is never exposed)</dd>
      <dt>Units (as returned)</dt><dd>${Object.entries(res.units || {}).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join(" · ")}</dd><dt>Standing caveats</dt><dd>${(res.caveats || []).map(esc).join(" ")}</dd></dl>`;
  });
  // period windows, exactly as the engine returns them for each basis
  const tl = $("me-timeline");
  load(tl, "chart", Promise.allSettled(["MONTH", "YTD", "MAT"].map((b) => tool("get_market_performance", { level: "total", anchor: state.anchor, basis: b }))), (res) => {
    const periods = m.periods, n = periods.length;
    mount(tl, (W0) => {
      const W = Math.max(W0, 300), lw = 96, rowH = 30, H = 3 * rowH + 30;
      const x = (iso) => lw + (periods.indexOf(iso) / n) * (W - lw - 8);
      const cw = (W - lw - 8) / n;
      let s = `<svg width="${W}" height="${H}" role="img" aria-label="Current and comparison windows for Month, calendar YTD and MAT">`;
      periods.forEach((p, i) => { if (p.endsWith("-01-01") || i === 0) s += `<line class="gl" x1="${x(p)}" x2="${x(p)}" y1="0" y2="${3 * rowH}"/><text x="${x(p) + 2}" y="${3 * rowH + 14}">${esc(shortMonth(p))}</text>`; });
      ["MONTH", "YTD", "MAT"].forEach((b, i) => {
        const y = i * rowH + 6, r = res[i];
        s += `<text class="lbl" x="0" y="${y + 13}">${esc(BASIS_SHORT[b])}</text>`;
        if (r.status !== "fulfilled") { s += `<text x="${lw}" y="${y + 13}">not available for this period ending</text>`; return; }
        const p = r.value.period;
        if (p.prior_start) s += `<rect class="bar-prior" x="${x(p.prior_start)}" y="${y + 4}" width="${x(p.prior_end) - x(p.prior_start) + cw}" height="10" rx="2"/>`;
        s += `<rect class="bar-main" x="${x(p.cur_start)}" y="${y + 4}" width="${x(p.cur_end) - x(p.cur_start) + cw}" height="10" rx="2"/>`;
        if (!p.prior_start) s += `<text x="${lw}" y="${y + 26}" class="ref-t">no complete comparison window → growth n/a</text>`;
      });
      tl.innerHTML = `<div class="legend"><span><i class="sw bar-main"></i>Current window</span><span><i class="sw bar-prior"></i>Comparison window (12 months earlier)</span></div>${s}</svg>`;
    });
  });
  load($("me-opp-live"), "block", tool("get_opportunity_scores", { level: "product", anchor: state.anchor, basis: oppBasis(), top_n: 1 }), (op) => { $("me-opp-live").innerHTML = methodologyBlock(op.methodology, op.caveats); });
  const contracts = fetch("/api/tools").then((r) => r.json());
  load($("me-scn-live"), "block", contracts, (c) => {
    const t = c.tools.find((x) => x.name === "run_scenario");
    $("me-scn-live").innerHTML = `<dl class="kv"><dt>Version</dt><dd><b>${esc(m.scenario_methodology_version)}</b></dd><dt>Types</dt><dd>${m.scenario_types.map((x) => esc(SC_TYPES[x] ? SC_TYPES[x].t : x)).join(" · ")}</dd>
      <dt>Limits</dt><dd>${(t ? t.limitations : []).map(esc).join(" ")}</dd></dl>`;
  });
  load($("me-tools"), "table", contracts, (c) => {
    dataTable($("me-tools"), { id: "me-tools", rows: c.tools, key: (t) => t.name, auto: true, caption: "Whitelisted analytical tools",
      cols: [{ k: "name", label: "Tool", asc: true, sort: (t) => t.name, html: (t) => `<code>${esc(t.name)}</code>` }, { k: "purpose", label: "Purpose", wrap: true, html: (t) => esc(t.purpose) },
        { k: "p", label: "Parameters (* required)", wrap: true, html: (t) => esc(Object.keys(t.input_schema.properties).map((k) => k + (t.input_schema.required.includes(k) ? "*" : "")).join(", ") || "none") }] });
  });
}

const PAGES = { overview: pageOverview, market: pageMarket, therapy: pageTherapy, product: pageProduct, company: pageCompany,
  opportunity: pageOpportunity, scenario: pageScenario, method: pageMethod, nl: async () => {} };

// ------------------------------------------------------------------ 8. INIT
function bindStatic() {
  $("nav-toggle").addEventListener("click", () => sidebar(!$("sidebar").classList.contains("open")));
  $("scrim").addEventListener("click", () => { closeDrawer(); sidebar(false); });
  $("drawer-close").addEventListener("click", () => closeDrawer());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { tip.hide(); if ($("drawer").classList.contains("open")) closeDrawer(); else sidebar(false); }
    if (e.key === "Tab" && $("drawer").classList.contains("open")) {           // keep focus inside the open dialog
      const f = $$("#drawer a[href], #drawer button, #drawer [tabindex='0'], #drawer input, #drawer select").filter((n) => n.offsetParent !== null);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
  });
  window.addEventListener("scroll", () => tip.hide(), { passive: true });
  // global period
  $("g-anchor").addEventListener("change", () => { state.anchor = $("g-anchor").value; store.set("anchor", state.anchor); route(); });
  // market
  $("mk-area").addEventListener("change", () => setParams({ area: $("mk-area").value, key: "" }));
  $("mk-filter").addEventListener("input", debounce(() => setParams({ q: $("mk-filter").value.trim() }), 220));
  $("mk-reset").addEventListener("click", () => go("market", { level: "subgroup" }));
  // segments
  $("sg-seg").addEventListener("change", () => setParams({ seg: $("sg-seg").value }));
  $("sg-scope").addEventListener("change", () => setParams({ scope: $("sg-scope").value, skey: "" }));
  $("sg-run").addEventListener("click", () => setParams({ skey: $("sg-key").value.trim() }));
  $("sg-key").addEventListener("keydown", (e) => { if (e.key === "Enter") setParams({ skey: $("sg-key").value.trim() }); });
  $("sg-reset").addEventListener("click", () => go("therapy", {}));
  // product
  const search = () => { const q = $("pr-q").value.trim(); setParams({ q, code: "", mk: "" }, false); };
  $("pr-search").addEventListener("click", search);
  $("pr-q").addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  $("pr-area").addEventListener("change", () => setParams({ area: $("pr-area").value, code: "", mk: "" }));
  $("pr-reset").addEventListener("click", () => go("product", {}));
  // company
  $("co-scope").addEventListener("change", () => setParams({ scope: $("co-scope").value, skey: "", key: "" }));
  $("co-run").addEventListener("click", () => setParams({ skey: $("co-key").value.trim(), key: "" }));
  $("co-key").addEventListener("keydown", (e) => { if (e.key === "Enter") setParams({ skey: $("co-key").value.trim(), key: "" }); });
  $("co-filter").addEventListener("input", debounce(() => setParams({ q: $("co-filter").value.trim() }), 220));
  $("co-reset").addEventListener("click", () => go("company", {}));
  // opportunity
  $("op-area").addEventListener("change", () => setParams({ area: $("op-area").value, focus: "" }));
  $("op-top").addEventListener("change", () => setParams({ top: $("op-top").value }));
  $("op-ins").addEventListener("change", () => setParams({ ins: $("op-ins").checked ? "1" : "" }));
  $("op-company").addEventListener("change", () => setParams({ company: $("op-company").value.trim(), focus: "" }));
  $("op-reset").addEventListener("click", () => go("opportunity", {}));
  // scenario
  $("sc-etype").addEventListener("change", () => go("scenario", { ...scRead(), ekey: $("sc-etype").value === "total" ? "TOTAL" : "" }, true));
  $("sc-key").addEventListener("change", () => scSync(false));
  $("sc-ml").addEventListener("change", () => go("scenario", { ...scRead(), mk: $("sc-ml").value === "total" ? "TOTAL" : "" }, true));
  $("sc-mk").addEventListener("change", () => scSync(true));
  $("sc-run").addEventListener("click", () => { const before = location.hash; scSync(true); if (location.hash === before) runScenario(); });
}

async function init() {
  bindStatic();
  state.meta = await tool("get_application_metadata");
  const m = state.meta;
  if (m.dataset === "synthetic") {                     // M13: never let fictional numbers look like real sales
    $("ds-badge").classList.remove("hidden");
    $("ds-note").textContent = "SYNTHETIC dataset: fictional, structure-only; values describe an invented market";
    document.body.classList.add("is-synthetic");
    $("me-syn-note").classList.remove("hidden");
  }
  const saved = store.get("anchor");
  state.anchor = m.periods.includes(saved) ? saved : m.default_anchor;
  state.basis = m.bases.includes(store.get("basis")) ? store.get("basis") : "MAT";
  state.trendMetric = TREND_METRICS[store.get("trend")] ? store.get("trend") : "value";
  $("g-anchor").innerHTML = opt([...m.periods].reverse().map((p) => [p, monthLabel(p)]), state.anchor);
  segControl($("g-basis"), "g-basis", m.bases.map((b) => [b, BASIS_SHORT[b], m.basis_labels[b]]), state.basis, (v) => { state.basis = v; store.set("basis", v); route(); });
  segControl($("mk-level"), "mk-level", MARKET_LEVELS, "subgroup", (v) => setParams({ level: v, key: "", area: v === "therapy_group" || v === "subgroup" ? state.params.area : "" }));
  segControl($("op-level"), "op-level", [["product", "Products in market"], ["market", "Markets"]], "product", (v) => go("opportunity", { level: v }));
  $("sg-seg").innerHTML = opt(m.segments.map((s) => [s, lvl(s)]), "acute_chronic");
  $("sg-scope").innerHTML = opt(["total", "supergroup", "therapy_group", "subgroup", "molecule", "company"].map((s) => [s, s === "total" ? "Total market" : lvl(s)]), "total");
  $("co-scope").innerHTML = opt(m.company_scopes.map((s) => [s, s === "total" ? "Total market" : lvl(s)]), "total");
  $("co-mix-seg").innerHTML = opt(m.segments.filter((s) => s !== "indian_mnc").map((s) => [s, lvl(s)]), "acute_chronic");
  window.addEventListener("hashchange", route);
  if (!location.hash) history.replaceState(null, "", "#/overview");
  route();
  // therapy-area pickers load after the first page has asked for its data (never blocks first paint)
  tool("get_market_performance", { level: "supergroup", anchor: m.default_anchor, basis: "MAT", top_n: null }).then((areas) => {
    state.areas = areas.rows.map((r) => r.entity_key).sort();
    const o = opt(state.areas.map((a) => [a, a]), "");
    ["mk-area", "pr-area", "op-area"].forEach((id) => $(id).insertAdjacentHTML("beforeend", o));
    if (["market", "product", "opportunity"].includes(state.page) && (state.params.area)) route();
  }).catch(() => { /* area pickers stay at "all"; every page still works */ });
}
init().catch((e) => { console.error(e); globalError(e); });
