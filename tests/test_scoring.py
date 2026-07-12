import json
import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from market_data import resample  # noqa: E402
from scoring import (  # noqa: E402
    downside_expansion_penalty,
    five_day_speed_penalty,
    large_down_frequency_penalty,
    monthly_bonus,
    post_surge_adjustment,
    random_market,
    score_asset,
)


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


def bars_from_closes(closes, *, shrinking_ranges=False, base_spread=1.2):
    rows = []
    day = date(2025, 1, 6)
    previous = closes[0]
    for index, close in enumerate(closes):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        spread = (base_spread if not shrinking_ranges else max(0.18, 1.4 - index * 0.035))
        open_ = previous
        rows.append({
            "date": day.isoformat(),
            "open": open_,
            "high": max(open_, close) + spread,
            "low": min(open_, close) - spread,
            "close": float(close),
            "adj": float(close),
            "volume": 1_000_000 + index * 1_000,
        })
        previous = close
        day += timedelta(days=1)
    return rows


class UniverseTests(unittest.TestCase):
    def test_exactly_one_hundred_ninety_nine_unique_stocks(self):
        universe = json.loads((ROOT / "universe.json").read_text(encoding="utf-8"))
        self.assertEqual(len(universe["stocks"]), 199)
        self.assertEqual(len(set(universe["stocks"])), 199)
        self.assertNotIn("CRNX", universe["stocks"])
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

    def test_revised_four_component_allocation(self):
        result = score_asset(
            synthetic_daily(1100, growth=0.0009),
            synthetic_daily(1100, growth=0.0006),
            synthetic_daily(1100, growth=0.0004),
        )
        maxima = {
            "直線上昇・高値圏上昇": 35,
            "出来高の質": 30,
            "下方向ボラティリティ・下落速度": 25,
            "ATH位置・移動平均線構造": 10,
        }
        for timeframe in ("daily", "weekly"):
            components = result["timeframes"][timeframe]["components"]
            self.assertEqual(set(components), set(maxima))
            self.assertEqual(sum(components.values()), result["timeframes"][timeframe]["score"])
            for name, score in components.items():
                self.assertGreaterEqual(score, 0)
                self.assertLessEqual(score, maxima[name])

    def test_downside_thresholds_match_the_revised_tables(self):
        self.assertEqual([downside_expansion_penalty(value) for value in (0.79, 0.8, 1.0, 1.2, 1.5)], [0, 1, 3, 5, 7])
        self.assertEqual([five_day_speed_penalty(value) for value in (1.49, 1.5, 2.5, 3.5, 5.0)], [0, 2, 4, 6, 7])
        self.assertEqual([large_down_frequency_penalty(value) for value in range(5)], [0, 2, 3, 4, 5])

    def test_revised_subcomponent_schema_matches_the_screenshots(self):
        result = score_asset(
            synthetic_daily(1100, growth=0.0009),
            synthetic_daily(1100, growth=0.0006),
            synthetic_daily(1100, growth=0.0004),
        )
        expected_details = {
            "直近20期間の直線適合度",
            "直近60期間の直線適合度",
            "方向効率",
            "上昇速度の安定性",
            "高値圏での緩やかな上昇",
            "押し目の浅さ・回復力",
            "急騰後補正",
            "上昇足と下落足の出来高比",
            "出来高の継続性",
            "ブレイク時の出来高",
            "下落時の出来高",
            "下方向ボラティリティの拡大",
            "5日以内の下落速度",
            "大幅下落日の頻度",
            "急落後の売り継続",
            "日中値幅・終値位置",
            "ATHからの位置",
            "株価と移動平均線の位置",
            "移動平均線の並び",
            "移動平均線の方向",
        }
        expected_metrics = {
            "downsideExpansion",
            "fiveDayDropAtr",
            "largeDownDayCount",
            "continuationState",
            "intradayState",
            "downsidePenalty",
            "linearBaseScore",
            "linearFinalScore",
            "surgeDetected",
            "postSurgeState",
            "postSurgeAdjustment",
            "reboundConfirmationCount",
        }
        for timeframe in ("daily", "weekly"):
            frame = result["timeframes"][timeframe]
            self.assertEqual(set(frame["details"]), expected_details)
            self.assertTrue(expected_metrics.issubset(frame["metrics"]))
            self.assertGreaterEqual(frame["components"]["直線上昇・高値圏上昇"], 0)
            self.assertLessEqual(frame["components"]["直線上昇・高値圏上昇"], 35)
            self.assertGreaterEqual(frame["components"]["下方向ボラティリティ・下落速度"], 0)
            self.assertLessEqual(frame["components"]["下方向ボラティリティ・下落速度"], 25)

    def test_post_surge_continued_decline_is_capped_at_minus_five(self):
        closes = [100] * 25 + [100, 104, 108, 112, 116, 120, 118, 116, 113, 110, 107, 104, 101, 98, 96]
        adjustment, metrics = post_surge_adjustment(bars_from_closes(closes))
        self.assertEqual(adjustment, -5)
        self.assertFalse(metrics["reboundConfirmed"])
        self.assertGreaterEqual(metrics["newLowCount"], 2)

    def test_post_surge_deduction_table_uses_minus_two_to_minus_four(self):
        base = [100] * 25 + [100, 104, 108, 112, 116, 120]
        cases = [
            (base + [119.5, 119, 118.5, 118], 1.2, -2),
            (base + [119, 117.5, 116, 115], 1.2, -3),
            (base + [119, 118, 117, 116, 115, 114, 113, 112], 3.0, -4),
        ]
        for closes, spread, expected in cases:
            with self.subTest(expected=expected):
                adjustment, _ = post_surge_adjustment(bars_from_closes(closes, base_spread=spread))
                self.assertEqual(adjustment, expected)

    def test_confirmed_multi_period_rebound_removes_the_deduction(self):
        closes = [100] * 25 + [100, 104, 108, 112, 116, 120, 116, 111, 105, 100, 103, 107, 111]
        adjustment, metrics = post_surge_adjustment(bars_from_closes(closes))
        self.assertGreaterEqual(adjustment, 0)
        self.assertTrue(metrics["reboundConfirmed"])
        self.assertGreaterEqual(metrics["reboundConfirmationCount"], 2)

    def test_low_volatility_rise_near_ath_gets_plus_four(self):
        closes = [100] * 20 + [100, 104, 108, 112, 116, 120, 120.2, 120.5, 120.9, 121.3, 121.8, 122.2, 122.7, 123.1, 123.6]
        adjustment, metrics = post_surge_adjustment(bars_from_closes(closes, shrinking_ranges=True))
        self.assertEqual(adjustment, 4)
        self.assertIn("低ボラ", metrics["postSurgeState"])

    def test_sideways_action_near_the_high_stays_neutral(self):
        closes = [100] * 25 + [100, 104, 108, 112, 116, 120, 119.8, 120.1, 119.9, 120.0]
        adjustment, metrics = post_surge_adjustment(bars_from_closes(closes))
        self.assertEqual(adjustment, 0)
        self.assertEqual(metrics["postSurgeState"], "方向判定なし")


if __name__ == "__main__":
    unittest.main()
