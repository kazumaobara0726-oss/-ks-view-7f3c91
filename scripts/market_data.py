#!/usr/bin/env python3
"""Small, dependency-free Yahoo Finance client used by the daily updater."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import datetime, timezone


USER_AGENT = "Mozilla/5.0 (compatible; KiritsuScore/2.0; +https://github.com/)"
ALIASES = {"BRK.B": "BRK-B"}


def yahoo_symbol(symbol: str) -> str:
    return ALIASES.get(symbol, symbol)


def _get_json(urls: list[str], attempts: int = 5) -> dict:
    last_error: Exception | None = None
    for attempt in range(attempts):
        url = urls[attempt % len(urls)]
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                break
            time.sleep(min(12, (2**attempt) + random.random()))
    raise RuntimeError(str(last_error or "market data request failed"))


def fetch_chart(symbol: str, chart_range: str = "10y") -> dict:
    source_symbol = yahoo_symbol(symbol)
    encoded = urllib.parse.quote(source_symbol, safe="")
    query = urllib.parse.urlencode(
        {
            "range": chart_range,
            "interval": "1d",
            "events": "div,splits",
            "includePrePost": "false",
            "includeAdjustedClose": "true",
        }
    )
    urls = [
        f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}?{query}",
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?{query}",
    ]
    payload = _get_json(urls)
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(str(chart["error"]))
    results = chart.get("result") or []
    if not results:
        raise RuntimeError("no chart result")
    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0]
    adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    bars = []
    for index, stamp in enumerate(timestamps):
        try:
            close = closes[index]
            high = highs[index]
            low = lows[index]
            open_ = opens[index]
        except IndexError:
            continue
        if any(value is None for value in (open_, high, low, close)):
            continue
        if close <= 0 or high <= 0 or low <= 0:
            continue
        date = datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat()
        volume = volumes[index] if index < len(volumes) and volumes[index] is not None else 0
        adj = adjusted[index] if index < len(adjusted) and adjusted[index] is not None else close
        bars.append(
            {
                "date": date,
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume or 0),
                "adj": float(adj),
            }
        )
    if not bars:
        raise RuntimeError("no usable bars")
    meta = result.get("meta") or {}
    return {"requestedSymbol": symbol, "sourceSymbol": source_symbol, "meta": meta, "bars": bars}


def fetch_chart_with_prehistory(symbol: str) -> dict:
    """Return ten years of daily bars plus an older ATH baseline.

    Yahoo automatically makes ``range=max`` data sparse for long-lived symbols,
    even when ``interval=1d`` is requested.  Those sparse bars must never be fed
    into moving-average calculations.  We therefore score with exact 10-year
    daily bars and use max-range bars only to preserve the all-time high that
    existed before the daily window began.
    """
    record = fetch_chart(symbol, "10y")
    cutoff = record["bars"][0]["date"]
    prehistory_high = 0.0
    try:
        maximum = fetch_chart(symbol, "max")
        prehistory_high = max(
            (bar["high"] for bar in maximum["bars"] if bar["date"] < cutoff),
            default=0.0,
        )
    except RuntimeError:
        # Ten years of exact daily data is sufficient for every score formula.
        # A temporary max-range failure should not make the whole item pending.
        prehistory_high = 0.0
    record["historicalHighBeforeRange"] = float(prehistory_high)
    return record


def fetch_profile(symbol: str) -> dict:
    source_symbol = yahoo_symbol(symbol)
    query = urllib.parse.urlencode({"q": source_symbol, "quotesCount": 8, "newsCount": 0})
    urls = [
        f"https://query2.finance.yahoo.com/v1/finance/search?{query}",
        f"https://query1.finance.yahoo.com/v1/finance/search?{query}",
    ]
    payload = _get_json(urls, attempts=4)
    normalized = source_symbol.replace(".", "-").upper()
    for quote in payload.get("quotes") or []:
        candidate = str(quote.get("symbol") or "").replace(".", "-").upper()
        if candidate == normalized:
            return {
                "name": quote.get("longname") or quote.get("shortname") or symbol,
                "sector": quote.get("sector") or "",
                "industry": quote.get("industry") or "",
                "exchange": quote.get("exchDisp") or quote.get("exchange") or "",
                "quoteType": quote.get("quoteType") or "",
            }
    return {"name": symbol, "sector": "", "industry": "", "exchange": "", "quoteType": ""}


def resample(bars: list[dict], frequency: str) -> list[dict]:
    """Convert daily bars to completed calendar weeks or months."""
    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for bar in bars:
        day = datetime.strptime(bar["date"], "%Y-%m-%d").date()
        if frequency == "week":
            iso = day.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
        elif frequency == "month":
            key = f"{day.year}-{day.month:02d}"
        else:
            raise ValueError(f"unsupported frequency: {frequency}")
        grouped.setdefault(key, []).append(bar)
    output = []
    for rows in grouped.values():
        output.append(
            {
                "date": rows[-1]["date"],
                "open": rows[0]["open"],
                "high": max(row["high"] for row in rows),
                "low": min(row["low"] for row in rows),
                "close": rows[-1]["close"],
                "volume": sum(row["volume"] for row in rows),
                "adj": rows[-1]["adj"],
            }
        )
    return output


def merge_volume(price_bars: list[dict], proxy_bars: list[dict] | None) -> list[dict]:
    if not proxy_bars or any(bar["volume"] > 0 for bar in price_bars[-60:]):
        return price_bars
    proxy = {bar["date"]: bar["volume"] for bar in proxy_bars}
    merged = []
    for bar in price_bars:
        copy = dict(bar)
        copy["volume"] = float(proxy.get(bar["date"], 0))
        merged.append(copy)
    return merged
