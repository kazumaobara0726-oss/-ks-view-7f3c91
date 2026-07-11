import json
import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from market_data import resample  # noqa: E402
from scoring import monthly_bonus, random_market, score_asset  # noqa: E402


def synthetic_daily(count=1100, *, growth=0.0007, volatility=0.012, volume=1_000_000):
    rows = []
    day = date(2021, 1, 4)
    close = 100.0
    index = 0
    while len(rows) < count:
        if day.weekday() < 5:
            wave = math.sin(index / 8) * volatility
            previous = close
            close = max(1.0, close * (1 + growth + wave * 0.08))
            open_ = previous * (1 + wave * 0.02)
            high = max(open_, close) * (1 + volatility * 0.35)
            low = min(open_, close) * (1 - volatility * 0.35)
            rows.append({
                "date": day.isoformat(),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "adj": close,
                "volume": volume * (1 + 0.08 * math.sin(index / 13)),
            })
            index += 1
        day += timedelta(days=1)
    return rows


class UniverseTests(unittest.TestCase):
    def test_exactly_two_hundred_unique_stocks(self):
        universe = json.loads((ROOT / "universe.json").read_text(encoding="utf-8"))
        self.assertEqual(len(universe["stocks"]), 200)
        self.assertEqual(len(set(universe["stocks"])), 200)
        self.assertEqual(len(universe["indices"]), 7)
        self.assertEqual(len(universe["sectors"]), 15)


class ScoringTests(unittest.TestCase):
    def test_monthly_bonus_handles_early_twenty_month_history(self):
        daily = synthetic_daily(460)
        monthly = resample(daily, "month")
        weekly = resample(daily, "week")
        bonus, parts, cap = monthly_bonus(monthly, 70, 70, weekly)
        self.assertGreaterEqual(bonus, 0)
        self.assertLessEqual(bonus, 10)
        self.assertEqual(sum(parts.values()) >= 0, True)
        self.assertLessEqual(cap, 10)

    def test_direction_reversal_rate_uses_nine_moves(self):
        daily = synthetic_daily(80, growth=0)
        for index, row in enumerate(daily[-10:]):
            row["close"] = 100 + (1 if index % 2 else 0)
            row["open"] = row["close"]
            row["high"] = row["close"] + 1
            row["low"] = row["close"] - 1
        _, diagnostics = random_market(daily)
        self.assertAlmostEqual(diagnostics["reversalRate"], 8 / 9)

    def test_full_score_schema_and_bounds(self):
        asset = synthetic_daily(1100, growth=0.0009)
        sector = synthetic_daily(1100, growth=0.0006)
        market = synthetic_daily(1100, growth=0.0004)
        result = score_asset(asset, sector, market)
        self.assertFalse(result["pending"])
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 110)
        self.assertEqual(set(result["timeframes"]), {"daily", "weekly"})
        self.assertLessEqual(len(result["timeframes"]["daily"]["chart"]), 90)
        self.assertLessEqual(len(result["timeframes"]["weekly"]["chart"]), 70)
        self.assertNotIn("monthly", result["timeframes"])


if __name__ == "__main__":
    unittest.main()
