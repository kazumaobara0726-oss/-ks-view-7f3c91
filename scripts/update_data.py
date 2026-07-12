#!/usr/bin/env python3
"""Fetch completed daily bars, calculate all scores, and write data.json."""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from market_data import fetch_chart, fetch_profile, merge_volume, resample  # noqa: E402
from scoring import compact_chart, moving_average, score_asset  # noqa: E402


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
        futures = {executor.submit(fetch_chart, symbol): symbol for symbol in ordered}
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


def build_scored_item(identifier, symbol, category, record, sector_record, market_record, *, name=None, sector_name=None, hide_symbol=False, volume_record=None, single_level=False):
    bars = merge_volume(record["bars"], volume_record["bars"] if volume_record else None)
    score = score_asset(bars, sector_record["bars"], market_record["bars"], single_level=single_level)
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

    results = []
    unavailable = []
    spy = records.get("SPY")
    if not spy:
        raise RuntimeError(f"SPY benchmark could not be fetched: {failures.get('SPY', 'unknown error')}")

    for symbol in stocks:
        profile = sector_cache.get(symbol, {})
        proxy, sector_name = stock_sectors[symbol]
        record = records.get(symbol)
        sector_record = records.get(proxy) or spy
        if not record:
            unavailable.append({
                "id": f"stock-{symbol}", "symbol": symbol, "displaySymbol": symbol, "name": profile.get("name") or symbol,
                "category": "stock", "sectorName": sector_name, "pending": True,
                "reason": f"価格履歴を取得できませんでした。母集団には含まれています。{failures.get(symbol, '')}".strip(),
            })
            continue
        results.append(build_scored_item(
            f"stock-{symbol}", symbol, "stock", record, sector_record, spy,
            name=profile.get("name") or None, sector_name=sector_name,
        ))

    index_items = []
    for config in universe["indices"]:
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

    sector_items = []
    for config in universe["sectors"]:
        record = records.get(config["symbol"])
        if not record:
            sector_items.append({"id": config["id"], "symbol": config["symbol"], "displaySymbol": "", "name": config["name"], "category": "sector", "pending": True, "reason": failures.get(config["symbol"], "価格履歴を取得できませんでした。")})
            continue
        volume_record = records.get(config.get("volumeProxy")) if config.get("volumeProxy") else None
        sector_items.append(build_scored_item(
            config["id"], config["symbol"], "sector", record, record, spy,
            name=config["name"], hide_symbol=True, volume_record=volume_record, single_level=True,
        ))

    stock_items = results + unavailable
    ranked = sorted((item for item in stock_items if not item.get("pending")), key=lambda item: item["score"], reverse=True)
    top20 = [item["id"] for item in ranked[:20]]
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "methodologyVersion": "us-equity-discipline-v4-post-surge",
        "dataSource": "Yahoo Finance chart API（最新完成日足）",
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
