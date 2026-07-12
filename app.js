const state = {
  data: null,
  mode: "stocks",
  selectedId: null,
  timeframe: "daily",
  historyPeriod: "6m",
  smoothing: false,
  selectedHistoryDate: null,
};

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const trendText = (value) => String(value ?? "")
  .replaceAll("歴史的な高規律上昇", "歴史的な高順張り")
  .replaceAll("規律崩れ・ランダム性が高い", "順張り不成立・ランダム性が高い")
  .replaceAll("中長期高規律", "中長期順張り")
  .replaceAll("高規律・ハイリスク", "強い順張り・ハイリスク")
  .replaceAll("規律崩れ上限", "順張り崩れ上限")
  .replaceAll("規律的上昇が終了", "順張り上昇が終了")
  .replaceAll("規律維持", "順張り維持");
const maxima = { "直線上昇・高値圏上昇": 35, "出来高の質": 30, "下方向ボラティリティ・下落速度": 25, "ATH位置・移動平均線構造": 10 };

const H = {
  DATE: 0, FINAL: 1, BASE: 2, AFTER: 3, DAILY: 4, WEEKLY: 5, MONTHLY: 6,
  LINEAR: 7, VOLUME: 8, DOWNSIDE: 9, ATH: 10, POST_DAY: 11, POST_WEEK: 12,
  CAP: 13, FLAGS: 14, ER20: 15, ATR_EXPANSION: 16, CLOSE: 17, DOWNSIDE_EXPANSION: 18, BREAKDOWN: 19,
  OPEN: 20, HIGH: 21, LOW: 22, PRICE_VOLUME: 23,
};

const EVENTS = {
  DOWNSIDE: 1,
  RANDOM: 2,
  BREAKDOWN: 4,
  BUBBLE: 8,
  POST_BEARISH: 16,
  MONTHLY: 32,
  ATH: 64,
};

function scoreBand(score) {
  if (score >= 100) return "歴史的な高順張り";
  if (score >= 90) return "極めて高い";
  if (score >= 80) return "高い";
  if (score >= 70) return "やや高い";
  if (score >= 55) return "変調・監視";
  if (score >= 40) return "低い";
  return "順張り不成立・ランダム";
}

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

function signed(value, digits = 0) {
  const number = Number(value || 0);
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}`;
}

function shortDate(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? value : `${date.getUTCFullYear()}/${date.getUTCMonth() + 1}/${date.getUTCDate()}`;
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

function miniSparkline(item) {
  const rows = (item.scoreHistory || []).slice(-60);
  if (rows.length < 2) return `<span class="mini-spark-empty">履歴待ち</span>`;
  const width = 104, height = 30, pad = 2;
  const x = (index) => pad + index / Math.max(1, rows.length - 1) * (width - pad * 2);
  const y = (value) => pad + (110 - Number(value)) / 110 * (height - pad * 2);
  const points = rows.map((row, index) => `${x(index).toFixed(2)},${y(row[H.FINAL]).toFixed(2)}`).join(" ");
  const color = rows.at(-1)[H.FINAL] >= 70 ? "#16855b" : rows.at(-1)[H.FINAL] >= 55 ? "#286bd6" : rows.at(-1)[H.FINAL] >= 40 ? "#b7791f" : "#cf3b50";
  return `<svg class="mini-spark" viewBox="0 0 ${width} ${height}" role="img" aria-label="直近60営業日のスコア推移"><line x1="0" y1="${y(55)}" x2="${width}" y2="${y(55)}" stroke="#d9dee7" stroke-width="1"/><polyline points="${points}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

function historyChange(item, periods = 20) {
  const rows = item.scoreHistory || [];
  if (rows.length <= periods) return null;
  return Number(rows.at(-1)[H.FINAL]) - Number(rows.at(-1 - periods)[H.FINAL]);
}

function renderList() {
  const items = itemsForMode();
  const copy = {
    stocks: ["RANKING", "順張りスコア 上位20銘柄"],
    indices: ["MARKET INDICES", "指定指数"],
    sectors: ["SECTOR COMPARISON", "業種別 順張りスコア"],
  }[state.mode];
  $("#list-eyebrow").textContent = copy[0];
  $("#list-title").textContent = copy[1];
  $("#list-count").textContent = `${items.length}件`;
  if (!state.selectedId) state.selectedId = items[0]?.id || null;
  $("#ranking-list").innerHTML = items.map((item, index) => {
    const pending = item.pending;
    const rank = state.mode === "indices" ? "—" : index + 1;
    const symbol = item.category === "sector" ? "" : esc(item.displaySymbol || item.symbol);
    const change = pending ? null : historyChange(item);
    return `<button type="button" class="rank-row ${item.category === "sector" ? "sector-row" : ""} ${state.selectedId === item.id ? "selected" : ""}" data-id="${esc(item.id)}">
      <span class="rank-number">${rank}</span>
      <span class="rank-identity"><strong>${symbol || esc(item.name)}</strong><small>${symbol ? esc(item.name) : scoreBand(item.score || 0)}</small></span>
      <span class="rank-trend">${pending ? `<span class="mini-spark-empty">採点保留</span>` : miniSparkline(item)}<small>20日前比 ${change == null ? "—" : signed(change)}</small></span>
      <span class="rank-state">${pending ? "保留" : scoreBand(item.score)}</span>
      <span class="rank-score ${pending ? "score-low" : scoreClass(item.score)}">${pending ? "—" : item.score}</span>
    </button>`;
  }).join("");
  $("#ranking-list").querySelectorAll("[data-id]").forEach((button) => button.addEventListener("click", () => selectItem(button.dataset.id)));
}

function selectItem(id, fromSearch = false) {
  state.selectedId = id;
  state.timeframe = "daily";
  state.historyPeriod = "6m";
  state.smoothing = false;
  state.selectedHistoryDate = null;
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
  const width = 900, height = 320, left = 16, right = 72, top = 18, priceBottom = 232, volumeTop = 254, volumeBottom = 296;
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
    return `<line x1="${left}" y1="${py}" x2="${width - right}" y2="${py}" stroke="#e4e8ee"/><text x="${width - right + 7}" y="${py + 4}" fill="#6c7787" font-size="10">${value.toLocaleString("ja-JP", { maximumFractionDigits: value >= 100 ? 1 : 2 })}</text>`;
  }).join("");
  const candles = rows.map((row, index) => {
    const [, open, high, low, close, volume] = row;
    const color = close >= open ? "#16855b" : "#d84b61";
    const bodyTop = Math.min(y(open), y(close));
    const bodyHeight = Math.max(1.3, Math.abs(y(open) - y(close)));
    const volumeHeight = (volume || 0) / volumeMax * (volumeBottom - volumeTop);
    return `<g><line x1="${x(index)}" y1="${y(high)}" x2="${x(index)}" y2="${y(low)}" stroke="${color}"/><rect x="${x(index) - candleWidth / 2}" y="${bodyTop}" width="${candleWidth}" height="${bodyHeight}" rx=".5" fill="${color}"/><rect x="${x(index) - candleWidth / 2}" y="${volumeBottom - volumeHeight}" width="${candleWidth}" height="${volumeHeight}" fill="${color}" opacity=".18"/></g>`;
  }).join("");
  const labels = [0, Math.floor((rows.length - 1) / 2), rows.length - 1].map((index) => `<text x="${x(index)}" y="316" text-anchor="middle" fill="#6c7787" font-size="10">${esc(rows[index][0])}</text>`).join("");
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="ローソク足チャート">${grid}${candles}${labels}</svg>`;
}

function weekKey(value) {
  const date = new Date(`${value}T00:00:00Z`);
  const offset = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - offset);
  return date.toISOString().slice(0, 10);
}

function rawHistoryRowsForPeriod(item) {
  const all = item.scoreHistory || [];
  if (!all.length) return [];
  const latest = Date.parse(`${all.at(-1)[H.DATE]}T00:00:00Z`);
  const days = { "1m": 31, "3m": 93, "6m": 186, "1y": 366, "3y": 365 * 3 + 15 }[state.historyPeriod] || 186;
  return all.filter((row) => Date.parse(`${row[H.DATE]}T00:00:00Z`) >= latest - days * 86400000);
}

function historyRowsForPeriod(item) {
  let rows = rawHistoryRowsForPeriod(item);
  if (state.historyPeriod === "3y") {
    const weekly = new Map();
    rows.forEach((row) => weekly.set(weekKey(row[H.DATE]), row));
    rows = [...weekly.values()];
  }
  return rows;
}

function historyPrice(row, index, fallback = H.CLOSE) {
  const value = Number(row[index]);
  if (Number.isFinite(value)) return value;
  const fallbackValue = Number(row[fallback]);
  return Number.isFinite(fallbackValue) ? fallbackValue : 0;
}

function priceRowsForPeriod(item) {
  const rows = rawHistoryRowsForPeriod(item);
  if (state.historyPeriod !== "3y") return rows;
  const weekly = new Map();
  rows.forEach((row) => {
    const key = weekKey(row[H.DATE]);
    const current = weekly.get(key);
    if (!current) {
      const first = [...row];
      first[H.OPEN] = historyPrice(row, H.OPEN);
      first[H.HIGH] = historyPrice(row, H.HIGH);
      first[H.LOW] = historyPrice(row, H.LOW);
      first[H.CLOSE] = historyPrice(row, H.CLOSE);
      first[H.PRICE_VOLUME] = historyPrice(row, H.PRICE_VOLUME, H.PRICE_VOLUME);
      weekly.set(key, first);
      return;
    }
    current[H.DATE] = row[H.DATE];
    current[H.HIGH] = Math.max(historyPrice(current, H.HIGH), historyPrice(row, H.HIGH));
    current[H.LOW] = Math.min(historyPrice(current, H.LOW), historyPrice(row, H.LOW));
    current[H.CLOSE] = historyPrice(row, H.CLOSE);
    current[H.PRICE_VOLUME] = historyPrice(current, H.PRICE_VOLUME, H.PRICE_VOLUME) + historyPrice(row, H.PRICE_VOLUME, H.PRICE_VOLUME);
  });
  return [...weekly.values()];
}

function chartX(rows, width, left, right) {
  const first = Date.parse(`${rows[0][H.DATE]}T00:00:00Z`);
  const last = Date.parse(`${rows.at(-1)[H.DATE]}T00:00:00Z`);
  return (row) => left + (Date.parse(`${row[H.DATE]}T00:00:00Z`) - first) / Math.max(last - first, 86400000) * (width - left - right);
}

function priceHistorySvg(rows) {
  if (rows.length < 2) return `<div class="empty-state compact-empty">価格履歴を準備中です。</div>`;
  const width = 900, height = 290, left = 54, right = 18, top = 16, priceBottom = 210, volumeTop = 226, volumeBottom = 260;
  const highs = rows.map((row) => historyPrice(row, H.HIGH));
  const lows = rows.map((row) => historyPrice(row, H.LOW));
  let min = Math.min(...lows), max = Math.max(...highs);
  const pad = Math.max((max - min) * .08, max * .004, .01);
  min -= pad; max += pad;
  const x = chartX(rows, width, left, right);
  const y = (value) => top + (max - value) / Math.max(max - min, 1e-9) * (priceBottom - top);
  const volumeMax = Math.max(...rows.map((row) => historyPrice(row, H.PRICE_VOLUME, H.PRICE_VOLUME)), 1);
  const candleWidth = Math.max(1.4, Math.min(8, (width - left - right) / Math.max(rows.length, 1) * .62));
  const grid = Array.from({ length: 4 }, (_, index) => {
    const value = max - (max - min) * index / 3;
    const py = y(value);
    return `<line x1="${left}" y1="${py}" x2="${width - right}" y2="${py}" stroke="#e7eaf0"/><text x="${left - 7}" y="${py + 4}" text-anchor="end" fill="#6c7787" font-size="10">${value.toLocaleString("ja-JP", { maximumFractionDigits: value >= 100 ? 1 : 2 })}</text>`;
  }).join("");
  const candles = rows.map((row) => {
    const open = historyPrice(row, H.OPEN);
    const high = historyPrice(row, H.HIGH);
    const low = historyPrice(row, H.LOW);
    const close = historyPrice(row, H.CLOSE);
    const volume = historyPrice(row, H.PRICE_VOLUME, H.PRICE_VOLUME);
    const color = close >= open ? "#16855b" : "#cf3b50";
    const bodyTop = Math.min(y(open), y(close));
    const bodyHeight = Math.max(1.4, Math.abs(y(open) - y(close)));
    const volumeHeight = volume / volumeMax * (volumeBottom - volumeTop);
    return `<g class="price-candle"><line x1="${x(row)}" y1="${y(high)}" x2="${x(row)}" y2="${y(low)}" stroke="${color}" vector-effect="non-scaling-stroke"/><rect x="${x(row) - candleWidth / 2}" y="${bodyTop}" width="${candleWidth}" height="${bodyHeight}" rx=".5" fill="${color}"/><rect class="price-volume" x="${x(row) - candleWidth / 2}" y="${volumeBottom - volumeHeight}" width="${candleWidth}" height="${volumeHeight}" fill="${color}" opacity=".22"/></g>`;
  }).join("");
  const labels = [rows[0], rows[Math.floor((rows.length - 1) / 2)], rows.at(-1)].map((row) => `<text x="${x(row)}" y="${height - 7}" text-anchor="middle" fill="#6c7787" font-size="10">${shortDate(row[H.DATE])}</text>`).join("");
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="株価ローソク足チャート">${grid}<line x1="${left}" y1="${priceBottom + 8}" x2="${width - right}" y2="${priceBottom + 8}" stroke="#dfe4eb"/><text x="${left}" y="${volumeTop - 4}" fill="#6c7787" font-size="9">出来高</text>${candles}${labels}</svg>`;
}

function smoothedScores(rows, period = 5) {
  return rows.map((row, index) => {
    if (index < period - 1) return null;
    const values = rows.slice(index - period + 1, index + 1).map((target) => Number(target[H.FINAL]));
    return values.reduce((sum, value) => sum + value, 0) / values.length;
  });
}

function markerColor(flags) {
  if (flags & (EVENTS.DOWNSIDE | EVENTS.RANDOM | EVENTS.BREAKDOWN | EVENTS.POST_BEARISH)) return "#cf3b50";
  if (flags & EVENTS.BUBBLE) return "#8b5cf6";
  if (flags & EVENTS.MONTHLY) return "#d18b16";
  return "#16855b";
}

function scoreHistorySvg(rows) {
  if (rows.length < 2) return `<div class="empty-state compact-empty">スコア履歴を準備中です。</div>`;
  const width = 900, height = 340, left = 48, right = 18, top = 14, bottom = 38;
  const x = chartX(rows, width, left, right);
  const y = (value) => top + (110 - Number(value)) / 110 * (height - top - bottom);
  const bands = [
    [100, 110, "#e3f5ec"], [90, 100, "#edf7ef"], [80, 90, "#edf4ff"], [70, 80, "#f3f6ff"],
    [55, 70, "#fff8df"], [40, 55, "#fff1e2"], [0, 40, "#fdebed"],
  ].map(([low, high, color]) => `<rect x="${left}" y="${y(high)}" width="${width - left - right}" height="${y(low) - y(high)}" fill="${color}"/>`).join("");
  const ticks = [0, 20, 40, 55, 70, 80, 90, 100, 110].map((value) => `<line x1="${left}" y1="${y(value)}" x2="${width - right}" y2="${y(value)}" stroke="#dfe4eb"/><text x="${left - 7}" y="${y(value) + 4}" text-anchor="end" fill="#667085" font-size="10">${value}</text>`).join("");
  const finalPoints = rows.map((row) => `${x(row).toFixed(2)},${y(row[H.FINAL]).toFixed(2)}`).join(" ");
  const basePoints = rows.map((row) => `${x(row).toFixed(2)},${y(row[H.BASE]).toFixed(2)}`).join(" ");
  const smooth = smoothedScores(rows);
  const smoothPoints = rows.map((row, index) => smooth[index] == null ? null : `${x(row).toFixed(2)},${y(smooth[index]).toFixed(2)}`).filter(Boolean).join(" ");
  const markers = rows.map((row) => {
    const flags = Number(row[H.FLAGS] || 0);
    if (!flags) return "";
    return `<circle cx="${x(row)}" cy="${y(row[H.FINAL])}" r="4.5" fill="${markerColor(flags)}" stroke="#fff" stroke-width="2" pointer-events="none"/>`;
  }).join("");
  const selectedDate = state.selectedHistoryDate || rows.at(-1)[H.DATE];
  const selected = rows.find((row) => row[H.DATE] === selectedDate) || rows.at(-1);
  const selectedPoint = `<line class="history-selection-line" x1="${x(selected)}" y1="${top}" x2="${x(selected)}" y2="${height - bottom}" stroke="#5b6472" stroke-dasharray="2 4"/><circle class="history-selection-dot" cx="${x(selected)}" cy="${y(selected[H.FINAL])}" r="5" fill="#fff" stroke="#174ea6" stroke-width="3"/>`;
  const hitWidth = (width - left - right) / Math.max(1, rows.length - 1);
  const hits = rows.map((row) => `<rect class="history-hit" data-history-date="${row[H.DATE]}" data-history-x="${x(row)}" data-history-y="${y(row[H.FINAL])}" x="${x(row) - hitWidth / 2}" y="${top}" width="${Math.max(hitWidth, 5)}" height="${height - top - bottom}" fill="transparent" tabindex="0" aria-label="${row[H.DATE]} スコア${row[H.FINAL]}"/>`).join("");
  const labels = [rows[0], rows[Math.floor((rows.length - 1) / 2)], rows.at(-1)].map((row) => `<text x="${x(row)}" y="${height - 8}" text-anchor="middle" fill="#667085" font-size="10">${shortDate(row[H.DATE])}</text>`).join("");
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="順張りスコアの推移">${bands}${ticks}<polyline points="${basePoints}" fill="none" stroke="#697586" stroke-width="1.4" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/><polyline points="${finalPoints}" fill="none" stroke="#174ea6" stroke-width="2.7" vector-effect="non-scaling-stroke"/>${state.smoothing ? `<polyline points="${smoothPoints}" fill="none" stroke="#d18b16" stroke-width="1.8" vector-effect="non-scaling-stroke"/>` : ""}${markers}${selectedPoint}${hits}${labels}</svg>`;
}

function historyPeriodButtons() {
  return `<div class="period-toggle">${[["1m", "1か月"], ["3m", "3か月"], ["6m", "6か月"], ["1y", "1年"], ["3y", "3年"]].map(([value, label]) => `<button type="button" data-period="${value}" class="${state.historyPeriod === value ? "active" : ""}">${label}</button>`).join("")}</div>`;
}

function historyEventLabels(row, previous) {
  const flags = Number(row[H.FLAGS] || 0);
  const labels = [];
  if (flags & EVENTS.DOWNSIDE) labels.push(`下方向ボラティリティ急拡大（${Number(row[H.DOWNSIDE_EXPANSION]).toFixed(2)}倍）`);
  if (flags & EVENTS.RANDOM) labels.push(`短期ランダム相場入り（ER20 ${Number(row[H.ER20]).toFixed(2)}・ATR比 ${Number(row[H.ATR_EXPANSION]).toFixed(2)}倍）`);
  if (flags & EVENTS.BREAKDOWN) labels.push(`順張り崩れ上限が発動（上限 ${row[H.CAP] ?? "—"}）`);
  if (flags & EVENTS.BUBBLE) labels.push("バブル的急騰を検出");
  if (flags & EVENTS.POST_BEARISH) labels.push("急騰後の大陰線を検出");
  if (flags & EVENTS.MONTHLY) labels.push(`月足ボーナスが変化（${previous ? signed(Number(row[H.MONTHLY]) - Number(previous[H.MONTHLY])) : "—"}）`);
  if (flags & EVENTS.ATH) labels.push("その日までのATHを更新");
  return labels;
}

function historyTooltipHtml(item, row) {
  const all = item.scoreHistory || [];
  const index = all.findIndex((target) => target[H.DATE] === row[H.DATE]);
  const previous = index > 0 ? all[index - 1] : null;
  const change = previous ? Number(row[H.FINAL]) - Number(previous[H.FINAL]) : 0;
  const events = historyEventLabels(row, previous);
  const cap = row[H.CAP] == null ? "なし" : row[H.CAP];
  return `<div class="history-tooltip-head"><div><span>${shortDate(row[H.DATE])}</span><strong class="${scoreClass(row[H.FINAL])}">${row[H.FINAL]}<small>/110</small></strong></div><div><span>前回比</span><strong class="${change >= 0 ? "positive" : "negative"}">${signed(change)}</strong></div><div><span>状態</span><strong>${esc(scoreBand(row[H.FINAL]))}</strong></div></div>
    <div class="history-tooltip-grid">
      <div><span>日足</span><strong>${row[H.DAILY]}/100</strong></div><div><span>週足</span><strong>${row[H.WEEKLY]}/100</strong></div><div><span>月足ボーナス</span><strong>+${row[H.MONTHLY]}</strong></div><div><span>適用上限</span><strong>${cap}</strong></div>
      <div><span>直線上昇</span><strong>${row[H.LINEAR]}/35</strong></div><div><span>出来高</span><strong>${row[H.VOLUME]}/30</strong></div><div><span>下方向ボラ</span><strong>${row[H.DOWNSIDE]}/25</strong></div><div><span>ATH構造</span><strong>${row[H.ATH]}/10</strong></div>
      <div><span>急騰後補正</span><strong>日 ${signed(row[H.POST_DAY])} / 週 ${signed(row[H.POST_WEEK])}</strong></div><div><span>減点・上限前</span><strong>${row[H.BASE]}</strong></div><div><span>減点後計算値</span><strong>${row[H.AFTER]}</strong></div><div><span>最終値</span><strong>${row[H.FINAL]}</strong></div>
    </div>${events.length ? `<div class="history-events"><strong>この日の重要な変化</strong>${events.map((event) => `<span>● ${esc(event)}</span>`).join("")}</div>` : `<div class="history-events quiet"><span>この日は新しい状態変化マーカーなし</span></div>`}`;
}

function historySection(item) {
  const rows = historyRowsForPeriod(item);
  const priceRows = priceRowsForPeriod(item);
  if (!rows.length) return `<div class="section"><div class="empty-state compact-empty">スコア履歴は次回のデータ更新で表示されます。</div></div>`;
  if (!state.selectedHistoryDate || !rows.some((row) => row[H.DATE] === state.selectedHistoryDate)) state.selectedHistoryDate = rows.at(-1)[H.DATE];
  const selected = rows.find((row) => row[H.DATE] === state.selectedHistoryDate) || rows.at(-1);
  return `<div class="section history-section"><div class="section-head history-head"><div><p class="eyebrow">SCORE HISTORY</p><h3>株価と順張りスコアの推移</h3></div>${historyPeriodButtons()}</div>
    <div class="history-chart-title"><span>株価チャート（ローソク足）</span><small>同じ期間・3年表示は週足／下段は出来高</small></div><div class="history-chart price-history">${priceHistorySvg(priceRows)}</div>
    <div class="history-chart-title"><span>順張りスコア（縦軸0〜110固定）</span><button type="button" id="smoothing-button" class="smoothing-button ${state.smoothing ? "active" : ""}">5日平均 ${state.smoothing ? "表示中" : "非表示"}</button></div>
    <div class="history-chart score-history">${scoreHistorySvg(rows)}</div>
    <div class="history-legend"><span><i class="line-solid"></i>最終スコア</span><span><i class="line-dashed"></i>減点・上限適用前</span>${state.smoothing ? `<span><i class="line-smooth"></i>5日平均</span>` : ""}<span><i class="marker-dot"></i>重要な状態変化</span></div>
    <div class="history-tooltip" id="history-tooltip">${historyTooltipHtml(item, selected)}</div></div>`;
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

function postSurgeDiagnostics(frame) {
  const metrics = frame.metrics || {};
  if (!("postSurgeAdjustment" in metrics)) return "";
  const adjustment = Number(metrics.postSurgeAdjustment || 0);
  const adjustmentText = signed(adjustment);
  const adjustmentClass = adjustment > 0 ? "positive" : adjustment < 0 ? "negative" : "neutral";
  const detected = Boolean(metrics.surgeDetected);
  const rise = detected ? `${metricNumber(Number(metrics.surgeRisePercent || 0) * 100, 1)}% / ${metricNumber(metrics.surgeRiseAtr, 2)} ATR` : "該当なし";
  const drawdown = detected ? `${metricNumber(Number(metrics.postSurgeDrawdown || 0) * 100, 1)}% / ${metricNumber(metrics.postSurgeDrawdownAtr, 2)} ATR` : "—";
  const confirmation = detected ? `${Number(metrics.reboundConfirmationCount || 0)}/5${metrics.reboundConfirmed ? "（確認済み）" : ""}` : "—";
  return `<div class="downside-audit surge-audit"><div class="downside-audit-head"><span>35点内の急騰後補正</span><strong class="${adjustmentClass}">${adjustmentText}点</strong></div>
    <div class="downside-row"><span>判定状態</span><b>${esc(metrics.postSurgeState || "方向判定なし")}</b><strong class="${adjustmentClass}">${adjustmentText}</strong></div><div class="downside-row"><span>直近20期間の急騰条件</span><b>${detected ? "該当" : "非該当"}</b><strong class="neutral">${esc(rise)}</strong></div><div class="downside-row"><span>急騰高値からの下落</span><b>${esc(drawdown)}</b><strong class="neutral">—</strong></div><div class="downside-row"><span>反発確認条件</span><b>${esc(confirmation)}（2つ以上・2期間以上）</b><strong class="neutral">—</strong></div><div class="downside-row"><span>35点の計算</span><b>基本 ${Number(metrics.linearBaseScore || 0)} ＋ 補正 ${adjustmentText}</b><strong class="neutral">${Number(metrics.linearFinalScore || 0)}/35</strong></div></div>`;
}

function componentSection(item, timeframe) {
  const frame = item.timeframes[timeframe];
  const cards = Object.entries(frame.components).map(([name, score]) => {
    const maximum = maxima[name] || Math.max(score, 1);
    return `<div class="component"><span>${esc(name)}</span><strong>${score}<small>/${maximum}</small></strong><div class="component-bar"><b style="width:${Math.max(0, Math.min(100, score / maximum * 100))}%"></b></div></div>`;
  }).join("");
  const details = Object.entries(frame.details).map(([name, score]) => `<tr><td>${esc(name)}</td><td>${name === "急騰後補正" && Number(score) > 0 ? "+" : ""}${score}</td></tr>`).join("");
  return `<div class="component-grid">${cards}</div><details class="score-audit-disclosure"><summary>細かい配点・補正を表示</summary><div class="audit-disclosure-body">${postSurgeDiagnostics(frame)}${downsideDiagnostics(frame)}<table class="fine-table"><thead><tr><th>判定項目</th><th>点数</th></tr></thead><tbody>${details}</tbody></table></div></details>`;
}

function auditSection(item) {
  const penalties = item.penalties;
  const coreScore = (item.timeframes.daily.score * 0.5 + item.timeframes.weekly.score * 0.5).toFixed(1);
  const caps = Object.entries(item.caps).filter(([, value]) => value != null);
  const capRows = caps.length ? caps.map(([name, value]) => `<div class="audit-row"><span>${esc(trendText(name))}</span><strong>${value}</strong></div>`).join("") : `<div class="audit-row"><span>適用上限</span><strong>なし（110）</strong></div>`;
  const breakdownRows = Object.entries(penalties.breakdown.parts || {}).map(([name, value]) => `<tr><td>${esc(name)}</td><td>${esc(trendText(value.state))}</td><td>${value.points}</td></tr>`).join("");
  return `<div class="audit-grid"><div class="audit-card"><h4>加点・減点</h4><div class="audit-row"><span>日足50％＋週足50％</span><strong>${coreScore}</strong></div><div class="audit-row"><span>月足構造ボーナス</span><strong>+${item.monthlyBonus.score}</strong></div><div class="audit-row"><span>3年下落減点</span><strong>−${penalties.threeYear.points}</strong></div><div class="audit-row"><span>順張り崩れ減点</span><strong>−${penalties.breakdown.points}</strong></div><div class="audit-row"><span>上限適用前</span><strong>${item.afterPenalties}</strong></div></div><div class="audit-card"><h4>上限</h4>${capRows}<div class="audit-row"><span>最終適用上限</span><strong>${item.appliedCap === 110 ? "なし" : item.appliedCap}</strong></div><div class="audit-row"><span>健全な押し目保護</span><strong>${item.diagnostics.healthyPullback ? "適用" : "なし"}</strong></div></div></div><table class="fine-table"><thead><tr><th>順張り崩れ項目</th><th>状態</th><th>点</th></tr></thead><tbody>${breakdownRows}</tbody></table>`;
}

function renderDetail() {
  const item = findItem(state.selectedId) || itemsForMode()[0];
  if (!item) { $("#detail-panel").innerHTML = `<div class="empty-state">表示できるデータがありません。</div>`; return; }
  if (item.pending) {
    const rows = item.charts?.[state.timeframe] || [];
    $("#detail-panel").innerHTML = `${detailHeader(item)}<div class="pending-card"><strong>採点保留</strong><p>${esc(item.reason)}</p></div>${rows.length ? `<div class="section"><div class="section-head"><h3>価格推移</h3>${timeframeButtons()}</div><div class="chart-wrap">${chartSvg(rows)}</div></div>` : ""}`;
    bindDetailInteractions(item);
    return;
  }
  const frame = item.timeframes[state.timeframe];
  const warning = item.warning ? `<div class="metric-card warning-card"><span>警告・状態</span><strong>${esc(trendText(item.warning))}</strong></div>` : "";
  $("#detail-panel").innerHTML = `${detailHeader(item)}<div class="score-hero"><div class="score-main"><span>TREND-FOLLOWING SCORE</span><strong class="${scoreClass(item.score)}">${item.score}</strong><small>/110</small></div><div class="score-context"><div class="metric-card"><span>日足</span><strong>${item.timeframes.daily.score}/100</strong></div><div class="metric-card"><span>週足</span><strong>${item.timeframes.weekly.score}/100</strong></div><div class="metric-card"><span>月足構造ボーナス</span><strong>+${item.monthlyBonus.score}/10</strong></div><div class="metric-card"><span>適用上限</span><strong>${item.appliedCap === 110 ? "なし" : item.appliedCap}</strong></div><div class="metric-card verdict-card"><span>判定</span><strong>${esc(trendText(item.verdict))}</strong></div>${warning}</div></div>
    ${historySection(item)}
    <div class="section"><div class="section-head"><h3>${state.timeframe === "daily" ? "日足" : "週足"}の100点内訳</h3><div class="section-tools"><span class="count-pill">${frame.score}/100</span>${timeframeButtons()}</div></div>${componentSection(item, state.timeframe)}</div>
    <div class="section"><details class="score-audit-disclosure"><summary>減点・上限の監査を表示（崩れ点 ${item.penalties.breakdown.breakdownPoints}/20）</summary><div class="audit-disclosure-body">${auditSection(item)}</div></details></div>`;
  bindDetailInteractions(item);
}

function timeframeButtons() {
  return `<div class="timeframe-toggle"><button type="button" data-tf="daily" class="${state.timeframe === "daily" ? "active" : ""}">日足</button><button type="button" data-tf="weekly" class="${state.timeframe === "weekly" ? "active" : ""}">週足</button></div>`;
}

function updateHistoryTooltip(item, date, target) {
  const row = (item.scoreHistory || []).find((target) => target[H.DATE] === date);
  const root = $("#history-tooltip");
  if (!row || !root) return;
  state.selectedHistoryDate = date;
  root.innerHTML = historyTooltipHtml(item, row);
  if (target) {
    const x = target.dataset.historyX;
    const y = target.dataset.historyY;
    const line = $(".history-selection-line");
    const dot = $(".history-selection-dot");
    if (line && x) { line.setAttribute("x1", x); line.setAttribute("x2", x); }
    if (dot && x && y) { dot.setAttribute("cx", x); dot.setAttribute("cy", y); }
  }
}

function bindDetailInteractions(item) {
  $("#detail-panel").querySelectorAll("[data-tf]").forEach((button) => button.addEventListener("click", () => { state.timeframe = button.dataset.tf; renderDetail(); }));
  $("#detail-panel").querySelectorAll("[data-period]").forEach((button) => button.addEventListener("click", () => { state.historyPeriod = button.dataset.period; state.selectedHistoryDate = null; renderDetail(); }));
  $("#smoothing-button")?.addEventListener("click", () => { state.smoothing = !state.smoothing; renderDetail(); });
  $("#detail-panel").querySelectorAll(".history-hit").forEach((target) => {
    const activate = () => updateHistoryTooltip(item, target.dataset.historyDate, target);
    target.addEventListener("pointerenter", activate);
    target.addEventListener("click", activate);
    target.addEventListener("focus", activate);
  });
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
  state.mode = tab.dataset.mode; state.selectedId = null; state.timeframe = "daily"; state.historyPeriod = "6m"; state.selectedHistoryDate = null;
  document.querySelectorAll(".tab").forEach((other) => other.classList.toggle("active", other === tab));
  renderList(); renderDetail();
}));
$("#search-input").addEventListener("input", (event) => renderSearch(event.target.value));
$("#search-input").addEventListener("keydown", (event) => { if (event.key === "Escape") $("#search-results").hidden = true; });
document.addEventListener("click", (event) => { if (!event.target.closest(".search-wrap")) $("#search-results").hidden = true; });
$("#method-button").addEventListener("click", () => $("#method-dialog").showModal());
$("#share-button").addEventListener("click", () => {
  window.open(`https://social-plugins.line.me/lineit/share?url=${encodeURIComponent(location.href)}`, "_blank", "noopener,noreferrer");
});

function unlockEntryGate() {
  const gate = $("#entry-gate");
  const shell = $("#app-shell");
  gate.hidden = true;
  shell.removeAttribute("inert");
  shell.removeAttribute("aria-hidden");
  document.body.classList.remove("gate-locked");
  $("#search-input")?.focus();
}

$("#entry-confirm").addEventListener("click", unlockEntryGate);
$("#entry-confirm").focus();
load();
