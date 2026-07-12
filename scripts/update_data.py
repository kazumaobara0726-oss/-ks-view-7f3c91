#!/usr/bin/env python3
"""Fetch completed daily bars, calculate all scores, and write data.json."""

from __future__ import annotations

import json
import os
import sys
from bisect import bisect_right
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from market_data import fetch_chart_with_prehistory, fetch_profile, merge_volume, resample  # noqa: E402
from scoring import compact_chart, moving_average, score_asset  # noqa: E402


HISTORY_LOOKBACK_BARS = 1000
HISTORY_DAILY_DAYS = 365
HISTORY_TOTAL_DAYS = 365 * 3 + 14
HISTORY_SCHEMA = [
    "date", "finalScore", "baseScore", "afterPenalties", "dailyScore", "weeklyScore", "monthlyBonus",
    "linearScore", "volumeScore", "downsideScore", "athMaScore", "dailyPostSurge", "weeklyPostSurge",
    "appliedCap", "eventFlags", "er20", "atrExpansion", "close", "downsideExpansion", "breakdownPoints",
    "open", "high", "low", "priceVolume",
]

EVENT_DOWNSIDE_EXPANSION = 1
EVENT_RANDOM_ENTRY = 2
EVENT_BREAKDOWN_CAP = 4
EVENT_BUBBLE = 8
EVENT_POST_SURGE_BEARISH = 16
EVENT_MONTHLY_BONUS_CHANGE = 32
EVENT_ATH = 64


SECTOR_MAP = {
    "Technology": ("XLK", "情報技術"),
    "Communication Services": ("XLC", "コミュニケーション・サービス"),
    "Consumer Cyclical": ("XLY", "一般消費財"),
    "Financial Services": ("XLF", "金融"),
    "Financial": ("XLF", "金融"),
    "Industrials": ("XLI", "資本財・産業"),
    "Energy": ("XLE", "エネルギー"),
    "Healthcare": ("XLV", "ヘルスケア"),
    "Utilities": ("XLU", "公益事業"),
    "Consumer Defensive": ("XLP", "生活必需品"),
    "Basic Materials": ("XLB", "素材"),
    "Real Estate": ("XLRE", "不動産"),
}

SEMICONDUCTORS = {
    "MU", "NVDA", "SNDK", "AMD", "INTC", "AVGO", "AMAT", "STX", "MRVL", "LITE", "WDC", "LRCX",
    "ASML", "KLAC", "QCOM", "COHR", "TXN", "ADI", "ARM", "ALAB", "AAOI", "MPWR", "MCHP", "ON", "NTAP",
    "TER", "NXPI", "STM", "CRDO",
}
SPACE = {"SPCX", "RKLB", "ASTS", "ONDS"}
BIOTECH = {"ALNY", "IONS", "MRNA", "AMGN", "BBIO"}
METALS = {"FCX"}


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path, value, pretty=False):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2 if pretty else None, separators=None if pretty else (",", ":"))
        handle.write("\n")


def enrich_profiles(stocks, cache):
    missing = [symbol for symbol in stocks if symbol not in cache]
    if not missing:
        print(f"Profiles: cached {len(stocks)}/{len(stocks)}", flush=True)
        return cache
    workers = int(os.getenv("PROFILE_WORKERS", "6"))
    print(f"Profiles: fetching {len(missing)} missing records", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_profile, symbol): symbol for symbol in missing}
        completed = 0
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                cache[symbol] = future.result()
            except Exception as exc:
                cache[symbol] = {"name": symbol, "sector": "", "industry": "", "exchange": "", "quoteType": "", "profileError": str(exc)}
            completed += 1
            if completed % 25 == 0 or completed == len(missing):
                print(f"Profiles: {completed}/{len(missing)}", flush=True)
    return cache


def sector_for(symbol, profile):
    industry = str(profile.get("industry") or "").lower()
    if symbol in SPACE:
        return "UFO", "宇宙"
    if symbol in SEMICONDUCTORS or "semiconductor" in industry:
        return "^SOX", "半導体"
    if symbol in BIOTECH or "biotech" in industry:
        return "XBI", "バイオテクノロジー"
    if symbol in METALS or any(word in industry for word in ("copper", "metal", "mining", "steel")):
        return "XME", "金属・鉱業"
    return SECTOR_MAP.get(profile.get("sector"), ("SPY", "市場全体"))


def fetch_all(symbols):
    workers = int(os.getenv("CHART_WORKERS", "6"))
    output, failures = {}, {}
    ordered = sorted(set(symbols))
    print(f"Charts: fetching {len(ordered)} unique symbols", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_chart_with_prehistory, symbol): symbol for symbol in ordered}
        completed = 0
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                output[symbol] = future.result()
            except Exception as exc:
                failures[symbol] = str(exc)
            completed += 1
            if completed % 20 == 0 or completed == len(ordered):
                print(f"Charts: {completed}/{len(ordered)} (ok={len(output)}, failed={len(failures)})", flush=True)
    return output, failures


def pending_chart(bars):
    if not bars:
        return {"daily": [], "weekly": []}
    closes = [bar["close"] for bar in bars]
    daily_ma = {"short": moving_average(closes, 20), "mid": moving_average(closes, 50)}
    weekly = resample(bars, "week")
    weekly_closes = [bar["close"] for bar in weekly]
    weekly_ma = {"short": moving_average(weekly_closes, 10), "mid": moving_average(weekly_closes, 20)}
    return {"daily": compact_chart(bars, daily_ma, 90), "weekly": compact_chart(weekly, weekly_ma, 70)}


def quote_fields(record, display_name=None):
    bars = record["bars"]
    meta = record.get("meta") or {}
    change = (bars[-1]["close"] / bars[-2]["close"] - 1) * 100 if len(bars) > 1 else 0
    return {
        "name": display_name or meta.get("longName") or meta.get("shortName") or record["requestedSymbol"],
        "price": round(bars[-1]["close"], 6),
        "changePercent": round(change, 4),
        "currency": meta.get("currency") or "",
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
        "asOf": bars[-1]["date"],
    }


def history_target_indices(bars):
    """Daily points for one year, then each week's final trading day out to three years."""
    if not bars:
        return []
    latest = date.fromisoformat(bars[-1]["date"])
    daily_cutoff = latest - timedelta(days=HISTORY_DAILY_DAYS)
    total_cutoff = latest - timedelta(days=HISTORY_TOTAL_DAYS)
    daily_indices = []
    weekly_last = {}
    for index, bar in enumerate(bars):
        day = date.fromisoformat(bar["date"])
        if day < total_cutoff:
            continue
        if day >= daily_cutoff:
            daily_indices.append(index)
        else:
            iso = day.isocalendar()
            weekly_last[(iso.year, iso.week)] = index
    return sorted(set(weekly_last.values()) | set(daily_indices))


def bars_through(bars, dates, target_date, limit=HISTORY_LOOKBACK_BARS):
    end = bisect_right(dates, target_date)
    return bars[max(0, end - limit) : end]


def history_state(score):
    daily_metrics = score["timeframes"]["daily"]["metrics"]
    random_daily = score["diagnostics"]["randomDaily"]
    return {
        "downside": NumberLike(daily_metrics.get("downsideExpansion")) >= 1.5,
        "random": int(random_daily.get("count") or 0) >= 3,
        "breakdownCap": score["caps"].get("順張り崩れ上限"),
        "bubble": int(score["diagnostics"]["bubble"].get("count") or 0) >= 3,
        "postBearish": bool(daily_metrics.get("surgeDetected")) and bool((daily_metrics.get("highZone") or {}).get("largeBearish")),
        "monthly": int(score["monthlyBonus"]["score"]),
    }


def NumberLike(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def score_history(asset_bars, sector_bars, market_bars, *, single_level=False, prehistory_high=0.0):
    """Recalculate historical scores using only data known on each target date."""
    targets = history_target_indices(asset_bars)
    if not targets:
        return []
    sector_dates = [row["date"] for row in sector_bars]
    market_dates = [row["date"] for row in market_bars]
    cumulative_highs = []
    running_high = NumberLike(prehistory_high)
    for row in asset_bars:
        running_high = max(running_high, row["high"])
        cumulative_highs.append(running_high)

    output = []
    previous_state = None
    previous_target_index = None
    latest = date.fromisoformat(asset_bars[-1]["date"])
    daily_cutoff = latest - timedelta(days=HISTORY_DAILY_DAYS)
    for index in targets:
        target_date = asset_bars[index]["date"]
        target_day = date.fromisoformat(target_date)
        if target_day >= daily_cutoff:
            price_bar = asset_bars[index]
        else:
            target_week = target_day.isocalendar()[:2]
            week_start = index
            while week_start > 0 and date.fromisoformat(asset_bars[week_start - 1]["date"]).isocalendar()[:2] == target_week:
                week_start -= 1
            price_bar = resample(asset_bars[week_start : index + 1], "week")[-1]
        asset_slice = asset_bars[max(0, index + 1 - HISTORY_LOOKBACK_BARS) : index + 1]
        sector_slice = bars_through(sector_bars, sector_dates, target_date)
        market_slice = bars_through(market_bars, market_dates, target_date)
        score = score_asset(
            asset_slice,
            sector_slice,
            market_slice,
            single_level=single_level,
            historical_high=cumulative_highs[index],
            include_charts=False,
            monthly_daily=asset_bars[: index + 1],
        )
        if score.get("pending"):
            continue
        daily = score["timeframes"]["daily"]
        weekly = score["timeframes"]["weekly"]
        state = history_state(score)
        flags = 0
        if previous_state is not None:
            if state["downside"] and not previous_state["downside"]:
                flags |= EVENT_DOWNSIDE_EXPANSION
            if state["random"] and not previous_state["random"]:
                flags |= EVENT_RANDOM_ENTRY
            if state["breakdownCap"] is not None and (
                previous_state["breakdownCap"] is None or state["breakdownCap"] < previous_state["breakdownCap"]
            ):
                flags |= EVENT_BREAKDOWN_CAP
            if state["bubble"] and not previous_state["bubble"]:
                flags |= EVENT_BUBBLE
            if state["postBearish"] and not previous_state["postBearish"]:
                flags |= EVENT_POST_SURGE_BEARISH
            if state["monthly"] != previous_state["monthly"]:
                flags |= EVENT_MONTHLY_BONUS_CHANGE
        if previous_target_index is not None and cumulative_highs[index] > cumulative_highs[previous_target_index]:
            flags |= EVENT_ATH

        components = []
        for name in ("直線上昇・高値圏上昇", "出来高の質", "下方向ボラティリティ・下落速度", "ATH位置・移動平均線構造"):
            components.append(round((daily["components"][name] + weekly["components"][name]) / 2, 1))
        random_daily = score["diagnostics"]["randomDaily"]
        row = [
            target_date,
            score["score"],
            score["baseScore"],
            score["afterPenalties"],
            daily["score"],
            weekly["score"],
            score["monthlyBonus"]["score"],
            *components,
            int(daily["metrics"].get("postSurgeAdjustment") or 0),
            int(weekly["metrics"].get("postSurgeAdjustment") or 0),
            None if score["appliedCap"] == 110 else score["appliedCap"],
            flags,
            round(NumberLike(random_daily.get("er20")), 4),
            round(NumberLike(random_daily.get("atrExpansion")), 4),
            round(asset_bars[index]["close"], 4),
            round(NumberLike(daily["metrics"].get("downsideExpansion")), 4),
            score["penalties"]["breakdown"]["breakdownPoints"],
            round(price_bar["open"], 4),
            round(price_bar["high"], 4),
            round(price_bar["low"], 4),
            int(price_bar.get("volume") or 0),
        ]
        output.append(row)
        previous_state = state
        previous_target_index = index
    return output


def build_scored_item(identifier, symbol, category, record, sector_record, market_record, *, name=None, sector_name=None, hide_symbol=False, volume_record=None, single_level=False):
    bars = merge_volume(record["bars"], volume_record["bars"] if volume_record else None)
    prehistory_high = NumberLike(record.get("historicalHighBeforeRange"))
    historical_high = max(prehistory_high, max(row["high"] for row in bars))
    score = score_asset(
        bars,
        sector_record["bars"],
        market_record["bars"],
        single_level=single_level,
        historical_high=historical_high,
    )
    item = {
        "id": identifier,
        "symbol": symbol,
        "displaySymbol": "" if hide_symbol else symbol,
        "category": category,
        "sectorName": sector_name,
        **quote_fields({**record, "bars": bars}, name),
        **score,
    }
    if score.get("pending"):
        item["charts"] = pending_chart(bars)
    else:
        item["scoreHistory"] = score_history(
            bars,
            sector_record["bars"],
            market_record["bars"],
            single_level=single_level,
            prehistory_high=prehistory_high,
        )
    return item


def main():
    universe = load_json(ROOT / "universe.json")
    stocks = universe["stocks"]
    if len(stocks) != 199 or len(set(stocks)) != 199:
        raise RuntimeError(f"The stock universe must contain exactly 199 unique symbols after removing CRNX; got {len(stocks)} / {len(set(stocks))}.")

    sector_cache = load_json(ROOT / "sector_cache.json")
    sector_cache = enrich_profiles(stocks, sector_cache)
    save_json(ROOT / "sector_cache.json", sector_cache, pretty=True)

    stock_sectors = {symbol: sector_for(symbol, sector_cache.get(symbol, {})) for symbol in stocks}
    symbols = set(stocks)
    symbols.update(universe.get("supportSymbols") or [])
    symbols.update(row["symbol"] for row in universe["indices"])
    symbols.update(row["symbol"] for row in universe["sectors"])
    symbols.update(row.get("benchmark", "SPY") for row in universe["indices"])
    symbols.update(row.get("volumeProxy") for row in universe["indices"] if row.get("volumeProxy"))
    symbols.update(row.get("volumeProxy") for row in universe["sectors"] if row.get("volumeProxy"))
    symbols.update(proxy for proxy, _ in stock_sectors.values())
    symbols.discard(None)
    records, failures = fetch_all(symbols)

    stock_items = [None] * len(stocks)
    spy = records.get("SPY")
    if not spy:
        raise RuntimeError(f"SPY benchmark could not be fetched: {failures.get('SPY', 'unknown error')}")

    score_workers = max(1, int(os.getenv("SCORE_WORKERS", str(min(4, os.cpu_count() or 2)))))
    print(f"Score history: using {score_workers} worker processes", flush=True)
    completed_stocks = 0
    with ProcessPoolExecutor(max_workers=score_workers) as executor:
        futures = {}
        for stock_index, symbol in enumerate(stocks):
            profile = sector_cache.get(symbol, {})
            proxy, sector_name = stock_sectors[symbol]
            record = records.get(symbol)
            sector_record = records.get(proxy) or spy
            if not record:
                stock_items[stock_index] = {
                    "id": f"stock-{symbol}", "symbol": symbol, "displaySymbol": symbol, "name": profile.get("name") or symbol,
                    "category": "stock", "sectorName": sector_name, "pending": True,
                    "reason": f"価格履歴を取得できませんでした。母集団には含まれています。{failures.get(symbol, '')}".strip(),
                }
                completed_stocks += 1
                continue
            future = executor.submit(
                build_scored_item,
                f"stock-{symbol}", symbol, "stock", record, sector_record, spy,
                name=profile.get("name") or None,
                sector_name=sector_name,
            )
            futures[future] = stock_index
        for future in as_completed(futures):
            stock_items[futures[future]] = future.result()
            completed_stocks += 1
            if completed_stocks % 5 == 0 or completed_stocks == len(stocks):
                print(f"Score history: stocks {completed_stocks}/{len(stocks)}", flush=True)

    if any(item is None for item in stock_items):
        raise RuntimeError("One or more stock score tasks did not return a result.")

    index_items = []
    for index_number, config in enumerate(universe["indices"], 1):
        record = records.get(config["symbol"])
        benchmark = records.get(config.get("benchmark")) or spy
        if not record:
            index_items.append({"id": config["id"], "symbol": config["symbol"], "displaySymbol": config["symbol"], "name": config["name"], "category": "index", "pending": True, "reason": failures.get(config["symbol"], "価格履歴を取得できませんでした。")})
            continue
        volume_record = records.get(config.get("volumeProxy")) if config.get("volumeProxy") else None
        index_items.append(build_scored_item(
            config["id"], config["symbol"], "index", record, record, benchmark,
            name=config["name"], volume_record=volume_record, single_level=True,
        ))
        print(f"Score history: indices {index_number}/{len(universe['indices'])}", flush=True)

    sector_items = []
    for sector_number, config in enumerate(universe["sectors"], 1):
        record = records.get(config["symbol"])
        if not record:
            sector_items.append({"id": config["id"], "symbol": config["symbol"], "displaySymbol": "", "name": config["name"], "category": "sector", "pending": True, "reason": failures.get(config["symbol"], "価格履歴を取得できませんでした。")})
            continue
        volume_record = records.get(config.get("volumeProxy")) if config.get("volumeProxy") else None
        sector_items.append(build_scored_item(
            config["id"], config["symbol"], "sector", record, record, spy,
            name=config["name"], hide_symbol=True, volume_record=volume_record, single_level=True,
        ))
        if sector_number % 5 == 0 or sector_number == len(universe["sectors"]):
            print(f"Score history: sectors {sector_number}/{len(universe['sectors'])}", flush=True)

    ranked = sorted((item for item in stock_items if not item.get("pending")), key=lambda item: item["score"], reverse=True)
    top20 = [item["id"] for item in ranked[:20]]
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "methodologyVersion": "us-equity-trend-score-v6-candles",
        "dataSource": "Yahoo Finance chart API（最新完成日足）",
        "historySchema": HISTORY_SCHEMA,
        "historyEvents": {
            "downsideExpansion": EVENT_DOWNSIDE_EXPANSION,
            "randomEntry": EVENT_RANDOM_ENTRY,
            "breakdownCap": EVENT_BREAKDOWN_CAP,
            "bubble": EVENT_BUBBLE,
            "postSurgeBearish": EVENT_POST_SURGE_BEARISH,
            "monthlyBonusChange": EVENT_MONTHLY_BONUS_CHANGE,
            "ath": EVENT_ATH,
        },
        "counts": {
            "requestedStocks": len(stocks),
            "availableStocks": sum(1 for item in stock_items if not item.get("pending")),
            "pendingStocks": sum(1 for item in stock_items if item.get("pending")),
            "indices": len(index_items),
            "sectors": len(sector_items),
        },
        "top20": top20,
        "stocks": stock_items,
        "indices": index_items,
        "sectors": sector_items,
        "fetchFailures": failures,
    }
    save_json(ROOT / "data.json", payload)
    print(
        f"Wrote data.json: stocks={len(stock_items)} available={payload['counts']['availableStocks']} "
        f"pending={payload['counts']['pendingStocks']} indices={len(index_items)} sectors={len(sector_items)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
