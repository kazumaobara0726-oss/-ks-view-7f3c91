const state = { data: null, mode: "stocks", selectedId: null, timeframe: "daily" };
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const maxima = { "トレンド構造": 25, "出来高の質": 30, "押し目の質": 20, "下方向ボラティリティ・下落速度": 25 };

function scoreClass(score) {
  if (score >= 80) return "score-high";
  if (score >= 55) return "score-watch";
  if (score >= 40) return "score-low";
  return "score-random";
}

function formatPrice(value, currency = "") {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  const digits = number >= 1000 ? 1 : number >= 10 ? 2 : 4;
  return `${number.toLocaleString("ja-JP", { maximumFractionDigits: digits })}${currency ? ` ${currency}` : ""}`;
}

function allItems() {
  if (!state.data) return [];
  return [...state.data.stocks, ...state.data.indices, ...state.data.sectors];
}

function findItem(id) {
  return allItems().find((item) => item.id === id);
}

function itemsForMode() {
  if (!state.data) return [];
  if (state.mode === "stocks") {
    const byId = new Map(state.data.stocks.map((item) => [item.id, item]));
    return state.data.top20.map((id) => byId.get(id)).filter(Boolean);
  }
  if (state.mode === "indices") return state.data.indices;
  return [...state.data.sectors].sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
}

function renderSummary() {
  const generated = new Date(state.data.generatedAt);
  $("#updated-at").textContent = Number.isNaN(generated.getTime()) ? state.data.generatedAt : generated.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const counts = state.data.counts;
  $("#universe-count").textContent = `${counts.requestedStocks}銘柄（採点可${counts.availableStocks}）`;
}

function renderList() {
  const items = itemsForMode();
  const copy = {
    stocks: ["RANKING", "規律可能性 上位20銘柄"],
    indices: ["MARKET INDICES", "指定指数"],
    sectors: ["SECTOR COMPARISON", "業種別 規律可能性"],
  }[state.mode];
  $("#list-eyebrow").textContent = copy[0];
  $("#list-title").textContent = copy[1];
  $("#list-count").textContent = `${items.length}件`;
  if (!state.selectedId) state.selectedId = items[0]?.id || null;
  $("#ranking-list").innerHTML = items.map((item, index) => {
    const pending = item.pending;
    const sector = state.mode === "sectors";
    const rank = state.mode === "indices" ? "—" : index + 1;
    if (sector) {
      return `<button type="button" class="rank-row sector-row ${state.selectedId === item.id ? "selected" : ""}" data-id="${esc(item.id)}">
        <span class="rank-number">${rank}</span><span class="rank-name">${esc(item.name)}</span><span class="rank-score ${pending ? "score-low" : scoreClass(item.score)}">${pending ? "保留" : item.score}</span>
      </button>`;
    }
    return `<button type="button" class="rank-row ${state.selectedId === item.id ? "selected" : ""}" data-id="${esc(item.id)}">
      <span class="rank-number">${rank}</span><span class="rank-symbol">${esc(item.displaySymbol || item.symbol)}</span><span class="rank-name">${esc(item.name)}</span><span class="rank-score ${pending ? "score-low" : scoreClass(item.score)}">${pending ? "保留" : item.score}</span>
    </button>`;
  }).join("");
  $("#ranking-list").querySelectorAll("[data-id]").forEach((button) => button.addEventListener("click", () => selectItem(button.dataset.id)));
}

function selectItem(id, fromSearch = false) {
  state.selectedId = id;
  state.timeframe = "daily";
  const item = findItem(id);
  if (fromSearch && item?.category === "stock") state.mode = "stocks";
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.mode === state.mode));
  history.replaceState(null, "", `${location.pathname}${location.search}#${encodeURIComponent(id)}`);
  renderList();
  renderDetail();
  $("#search-input").value = "";
  $("#search-results").hidden = true;
  if (window.innerWidth < 861) $("#detail-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function detailHeader(item) {
  const symbol = item.category === "sector" ? "" : `<span class="identity-symbol">${esc(item.displaySymbol || item.symbol)}</span>`;
  const change = Number(item.changePercent || 0);
  const quote = item.price == null ? "" : `<div class="quote"><strong>${formatPrice(item.price, item.currency)}</strong><span class="${change >= 0 ? "positive" : "negative"}">${change >= 0 ? "+" : ""}${change.toFixed(2)}%</span></div>`;
  return `<div class="detail-head"><div class="identity">${symbol}<h2>${esc(item.name)}</h2><p>${esc(item.exchange || "")}　最新完成足 ${esc(item.asOf || "取得待ち")}${item.sectorName ? `　比較先 ${esc(item.sectorName)}` : ""}</p></div>${quote}</div>`;
}

function chartSvg(rows) {
  if (!rows?.length) return `<div class="empty-state">チャートデータがありません。</div>`;
  const width = 900, height = 360, left = 16, right = 72, top = 18, priceBottom = 258, volumeTop = 282, volumeBottom = 334;
  const values = rows.flatMap((row) => [row[2], row[3], row[6], row[7]]).filter((value) => value != null && Number.isFinite(value));
  let min = Math.min(...values), max = Math.max(...values);
  const pad = Math.max((max - min) * .06, max * .002);
  min -= pad; max += pad;
  const xStep = (width - left - right) / Math.max(1, rows.length);
  const x = (index) => left + xStep * (index + .5);
  const y = (value) => top + (max - value) / Math.max(max - min, 1e-9) * (priceBottom - top);
  const volumeMax = Math.max(...rows.map((row) => row[5] || 0), 1);
  const candleWidth = Math.max(1.5, Math.min(7, xStep * .62));
  const grid = Array.from({ length: 5 }, (_, index) => {
    const value = max - (max - min) * index / 4;
    const py = y(value);
    return `<line x1="${left}" y1="${py}" x2="${width - right}" y2="${py}" stroke="#2a313a" stroke-width="1"/><text x="${width - right + 7}" y="${py + 4}" fill="#8e99a8" font-size="10">${value.toLocaleString("ja-JP", { maximumFractionDigits: value >= 100 ? 1 : 2 })}</text>`;
  }).join("");
  const candles = rows.map((row, index) => {
    const [, open, high, low, close, volume] = row;
    const color = close >= open ? "#34d3a0" : "#ff667d";
    const bodyTop = Math.min(y(open), y(close));
    const bodyHeight = Math.max(1.3, Math.abs(y(open) - y(close)));
    const volumeHeight = (volume || 0) / volumeMax * (volumeBottom - volumeTop);
    return `<g><title>${esc(row[0])} O ${open} H ${high} L ${low} C ${close}</title><line x1="${x(index)}" y1="${y(high)}" x2="${x(index)}" y2="${y(low)}" stroke="${color}" stroke-width="1"/><rect x="${x(index) - candleWidth / 2}" y="${bodyTop}" width="${candleWidth}" height="${bodyHeight}" rx=".5" fill="${color}"/><rect x="${x(index) - candleWidth / 2}" y="${volumeBottom - volumeHeight}" width="${candleWidth}" height="${volumeHeight}" fill="${color}" opacity=".25"/></g>`;
  }).join("");
  const polyline = (position, color) => {
    const points = rows.map((row, index) => row[position] == null ? null : `${x(index)},${y(row[position])}`).filter(Boolean).join(" ");
    return points ? `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.7" vector-effect="non-scaling-stroke"/>` : "";
  };
  const labels = [0, Math.floor((rows.length - 1) / 2), rows.length - 1].map((index) => `<text x="${x(index)}" y="352" text-anchor="middle" fill="#8e99a8" font-size="10">${esc(rows[index][0])}</text>`).join("");
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="ローソク足チャート">${grid}<line x1="${left}" y1="${volumeTop - 8}" x2="${width - right}" y2="${volumeTop - 8}" stroke="#343d48"/>${candles}${polyline(6, "#5f9cff")}${polyline(7, "#f1c75b")}${labels}</svg>`;
}

function metricNumber(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "—";
}

function downsideDiagnostics(frame) {
  const metrics = frame.metrics || {};
  const details = frame.details || {};
  const rows = [
    ["下方向ボラティリティの拡大", `${metricNumber(metrics.downsideExpansion)}倍`, details["下方向ボラティリティの拡大"]],
    ["5日以内の下落速度", `${metricNumber(metrics.fiveDayDropAtr)} ATR`, details["5日以内の下落速度"]],
    ["大幅下落日の頻度", `${Number(metrics.largeDownDayCount || 0)}回`, details["大幅下落日の頻度"]],
    ["急落後の売り継続", metrics.continuationState || "—", details["急落後の売り継続"]],
    ["日中値幅・終値位置", metrics.intradayState || "—", details["日中値幅・終値位置"]],
  ];
  return `<div class="downside-audit"><div class="downside-audit-head"><span>5つの下方向判定</span><strong>合計 −${Number(metrics.downsidePenalty || 0)}</strong></div>${rows.map(([name, stateText, points]) => `<div class="downside-row"><span>${esc(name)}</span><b>${esc(stateText)}</b><strong>${Number(points || 0)}</strong></div>`).join("")}</div>`;
}

function componentSection(item, timeframe) {
  const frame = item.timeframes[timeframe];
  const cards = Object.entries(frame.components).map(([name, score]) => {
    const maximum = maxima[name] || Math.max(score, 1);
    return `<div class="component"><span>${esc(name)}</span><strong>${score}<small>/${maximum}</small></strong><div class="component-bar"><b style="width:${Math.max(0, Math.min(100, score / maximum * 100))}%"></b></div></div>`;
  }).join("");
  const details = Object.entries(frame.details).map(([name, score]) => `<tr><td>${esc(name)}</td><td>${score}</td></tr>`).join("");
  return `<div class="component-grid">${cards}</div>${downsideDiagnostics(frame)}<details class="details-disclosure"><summary>すべての細かい配点を表示</summary><table class="fine-table"><thead><tr><th>判定項目</th><th>点数</th></tr></thead><tbody>${details}</tbody></table></details>`;
}

function auditSection(item) {
  const penalties = item.penalties;
  const coreScore = (item.timeframes.daily.score * 0.5 + item.timeframes.weekly.score * 0.5).toFixed(1);
  const caps = Object.entries(item.caps).filter(([, value]) => value != null);
  const capRows = caps.length ? caps.map(([name, value]) => `<div class="audit-row"><span>${esc(name)}</span><strong>${value}</strong></div>`).join("") : `<div class="audit-row"><span>適用上限</span><strong>なし（110）</strong></div>`;
  const breakdownRows = Object.entries(penalties.breakdown.parts || {}).map(([name, value]) => `<tr><td>${esc(name)}</td><td>${esc(value.state)}</td><td>${value.points}</td></tr>`).join("");
  return `<div class="audit-grid">
    <div class="audit-card"><h4>加点・減点</h4><div class="audit-row"><span>日足50％＋週足50％</span><strong>${coreScore}</strong></div><div class="audit-row"><span>月足構造ボーナス</span><strong>+${item.monthlyBonus.score}</strong></div><div class="audit-row"><span>3年下落減点</span><strong>−${penalties.threeYear.points}</strong></div><div class="audit-row"><span>規律崩れ減点</span><strong>−${penalties.breakdown.points}</strong></div><div class="audit-row"><span>上限適用前</span><strong>${item.afterPenalties}</strong></div></div>
    <div class="audit-card"><h4>上限</h4>${capRows}<div class="audit-row"><span>最終適用上限</span><strong>${item.appliedCap === 110 ? "なし" : item.appliedCap}</strong></div><div class="audit-row"><span>健全な押し目保護</span><strong>${item.diagnostics.healthyPullback ? "適用" : "なし"}</strong></div></div>
  </div><details class="details-disclosure"><summary>規律崩れ点の内訳</summary><table class="fine-table"><thead><tr><th>項目</th><th>状態</th><th>点</th></tr></thead><tbody>${breakdownRows}</tbody></table></details>`;
}

function renderDetail() {
  const item = findItem(state.selectedId) || itemsForMode()[0];
  if (!item) { $("#detail-panel").innerHTML = `<div class="empty-state">表示できるデータがありません。</div>`; return; }
  if (item.pending) {
    const rows = item.charts?.[state.timeframe] || [];
    $("#detail-panel").innerHTML = `${detailHeader(item)}<div class="pending-card"><strong>採点保留</strong><p>${esc(item.reason)}</p></div>${rows.length ? `<div class="section"><div class="section-head"><h3>価格推移</h3>${timeframeButtons()}</div><div class="chart-wrap">${chartSvg(rows)}</div></div>` : ""}`;
    bindTimeframeButtons();
    return;
  }
  const frame = item.timeframes[state.timeframe];
  const warning = item.warning ? `<div class="metric-card warning-card"><span>警告・状態</span><strong>${esc(item.warning)}</strong></div>` : "";
  $("#detail-panel").innerHTML = `${detailHeader(item)}
    <div class="score-hero"><div class="score-main"><span>DISCIPLINE SCORE</span><strong class="${scoreClass(item.score)}">${item.score}</strong><small>/110</small></div><div class="score-context"><div class="metric-card"><span>日足</span><strong>${item.timeframes.daily.score}/100</strong></div><div class="metric-card"><span>週足</span><strong>${item.timeframes.weekly.score}/100</strong></div><div class="metric-card"><span>月足構造ボーナス</span><strong>+${item.monthlyBonus.score}/10</strong></div><div class="metric-card"><span>適用上限</span><strong>${item.appliedCap === 110 ? "なし" : item.appliedCap}</strong></div><div class="metric-card verdict-card"><span>判定</span><strong>${esc(item.verdict)}</strong></div>${warning}</div></div>
    <div class="section"><div class="section-head"><h3>${state.timeframe === "daily" ? "日足" : "週足"}チャート</h3>${timeframeButtons()}</div><div class="chart-wrap">${chartSvg(frame.chart)}</div><div class="chart-legend"><span><i class="legend-dot legend-short"></i>${state.timeframe === "daily" ? "20日線" : "10週線"}</span><span><i class="legend-dot legend-mid"></i>${state.timeframe === "daily" ? "50日線" : "20週線"}</span><span>緑：上昇足　赤：下落足</span></div></div>
    <div class="section"><div class="section-head"><h3>${state.timeframe === "daily" ? "日足" : "週足"}の100点内訳</h3><span class="count-pill">${frame.score}/100</span></div>${componentSection(item, state.timeframe)}</div>
    <div class="section"><div class="section-head"><h3>減点・上限の監査</h3><span class="count-pill">崩れ点 ${item.penalties.breakdown.breakdownPoints}/20</span></div>${auditSection(item)}</div>`;
  bindTimeframeButtons();
}

function timeframeButtons() {
  return `<div class="timeframe-toggle"><button type="button" data-tf="daily" class="${state.timeframe === "daily" ? "active" : ""}">日足</button><button type="button" data-tf="weekly" class="${state.timeframe === "weekly" ? "active" : ""}">週足</button></div>`;
}

function bindTimeframeButtons() {
  $("#detail-panel").querySelectorAll("[data-tf]").forEach((button) => button.addEventListener("click", () => { state.timeframe = button.dataset.tf; renderDetail(); }));
}

function renderSearch(query) {
  const root = $("#search-results");
  const normalized = query.trim().toUpperCase();
  if (!normalized) { root.hidden = true; root.innerHTML = ""; return; }
  const matches = state.data.stocks.filter((item) => `${item.symbol} ${item.name}`.toUpperCase().includes(normalized)).slice(0, 30);
  root.hidden = false;
  root.innerHTML = matches.length ? matches.map((item) => `<button type="button" class="search-result" data-search-id="${esc(item.id)}"><strong>${esc(item.symbol)}</strong><span>${esc(item.name)}${item.sectorName ? ` · ${esc(item.sectorName)}` : ""}</span><b>${item.pending ? "保留" : item.score}</b></button>`).join("") : `<div class="empty-state search-empty">${state.data.counts.requestedStocks}銘柄の中に一致するティッカーがありません。</div>`;
  root.querySelectorAll("[data-search-id]").forEach((button) => button.addEventListener("click", () => selectItem(button.dataset.searchId, true)));
}

async function load() {
  try {
    const response = await fetch(`./data.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    const hash = decodeURIComponent(location.hash.slice(1));
    if (hash && findItem(hash)) {
      state.selectedId = hash;
      state.mode = findItem(hash).category === "index" ? "indices" : findItem(hash).category === "sector" ? "sectors" : "stocks";
    } else {
      state.selectedId = state.data.top20[0] || state.data.indices[0]?.id;
    }
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.mode === state.mode));
    renderSummary(); renderList(); renderDetail();
  } catch (error) {
    $("#detail-panel").innerHTML = `<div class="empty-state">データを読み込めませんでした。時間をおいて再読み込みしてください。<br>${esc(error.message)}</div>`;
  }
}

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  state.mode = tab.dataset.mode; state.selectedId = null; state.timeframe = "daily";
  document.querySelectorAll(".tab").forEach((other) => other.classList.toggle("active", other === tab));
  renderList(); renderDetail();
}));
$("#search-input").addEventListener("input", (event) => renderSearch(event.target.value));
$("#search-input").addEventListener("keydown", (event) => { if (event.key === "Escape") $("#search-results").hidden = true; });
document.addEventListener("click", (event) => { if (!event.target.closest(".search-wrap")) $("#search-results").hidden = true; });
$("#method-button").addEventListener("click", () => $("#method-dialog").showModal());
$("#share-button").addEventListener("click", () => {
  const url = location.href;
  window.open(`https://social-plugins.line.me/lineit/share?url=${encodeURIComponent(url)}`, "_blank", "noopener,noreferrer");
});

load();
