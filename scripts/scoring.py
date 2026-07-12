#!/usr/bin/env python3
"""US Equity Trend Score model (methodology revision 6)."""

from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import mean, median

from market_data import resample


def avg(values, default=0.0):
    values = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return mean(values) if values else default


def moving_average(values, period):
    result = []
    window_sum = 0.0
    valid_count = 0
    normalized = []
    for index, value in enumerate(values):
        number = float(value) if value is not None and math.isfinite(float(value)) else None
        normalized.append(number)
        if number is not None:
            window_sum += number
            valid_count += 1
        if index >= period:
            expired = normalized[index - period]
            if expired is not None:
                window_sum -= expired
                valid_count -= 1
        result.append(window_sum / valid_count if index >= period - 1 and valid_count else (0.0 if index >= period - 1 else None))
    return result


def true_ranges(bars):
    ranges = []
    for index, bar in enumerate(bars):
        previous = bars[index - 1]["close"] if index else bar["open"]
        ranges.append(max(bar["high"] - bar["low"], abs(bar["high"] - previous), abs(bar["low"] - previous)))
    return ranges


def atr(bars, period=20):
    ranges = true_ranges(bars)
    return avg(ranges[-period:]) if len(ranges) >= period else avg(ranges)


def pct_change(current, previous):
    return (current / previous - 1.0) if previous else 0.0


def price_return(bars, periods):
    if len(bars) <= periods:
        return 0.0
    return pct_change(bars[-1]["close"], bars[-periods - 1]["close"])


def slope(values, lookback):
    clean = [value for value in values[-lookback:] if value is not None]
    if len(clean) < 2:
        return 0.0
    return (clean[-1] - clean[0]) / max(1, len(clean) - 1)


def regression_metrics(values):
    """Return the least-squares slope and R-squared for a numeric series."""
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if len(clean) < 2:
        return 0.0, 0.0
    x_average = (len(clean) - 1) / 2
    y_average = avg(clean)
    denominator = sum((index - x_average) ** 2 for index in range(len(clean)))
    if not denominator:
        return 0.0, 0.0
    coefficient = sum((index - x_average) * (value - y_average) for index, value in enumerate(clean)) / denominator
    intercept = y_average - coefficient * x_average
    fitted = [intercept + coefficient * index for index in range(len(clean))]
    total = sum((value - y_average) ** 2 for value in clean)
    residual = sum((value - estimate) ** 2 for value, estimate in zip(clean, fitted))
    r_squared = 1.0 if total <= 1e-18 else max(0.0, min(1.0, 1 - residual / total))
    return coefficient, r_squared


def positive_log_slope(bars, periods):
    window = bars[-min(len(bars), periods) :]
    values = [math.log(max(row["close"], 1e-9)) for row in window]
    coefficient, _ = regression_metrics(values)
    return coefficient


def linear_fit_points(bars, periods, maximum):
    window = bars[-min(len(bars), periods) :]
    values = [math.log(max(row["close"], 1e-9)) for row in window]
    coefficient, r_squared = regression_metrics(values)
    rising = len(window) >= 2 and window[-1]["close"] > window[0]["close"] and coefficient > 0
    if not rising:
        points = 0
    else:
        thresholds = {
            9: ((0.92, 9), (0.85, 8), (0.75, 7), (0.65, 6), (0.55, 5), (0.45, 4), (0.35, 3), (0.25, 2), (0.15, 1)),
            6: ((0.88, 6), (0.76, 5), (0.62, 4), (0.48, 3), (0.34, 2), (0.20, 1)),
        }[maximum]
        points = next((score for threshold, score in thresholds if r_squared >= threshold), 0)
    return points, {"rSquared": r_squared, "slope": coefficient, "periods": len(window)}


def directional_efficiency_points(bars, periods=20):
    window = bars[-min(len(bars), periods + 1) :]
    closes = [row["close"] for row in window]
    if len(closes) < 2:
        return 0, 0.0
    path = sum(abs(closes[index] - closes[index - 1]) for index in range(1, len(closes)))
    efficiency = max(0.0, closes[-1] - closes[0]) / max(path, 1e-9)
    thresholds = ((0.80, 6), (0.65, 5), (0.50, 4), (0.35, 3), (0.22, 2), (0.10, 1))
    return next((score for threshold, score in thresholds if efficiency >= threshold), 0), efficiency


def ascent_stability_points(bars, periods=20):
    window = bars[-min(len(bars), periods + 1) :]
    if len(window) < 7 or window[-1]["close"] <= window[0]["close"]:
        return 0, {"positiveRatio": 0.0, "variation": None}
    step = max(2, min(5, (len(window) - 1) // 4))
    speeds = []
    for end in range(step, len(window), step):
        start = end - step
        speeds.append(math.log(max(window[end]["close"], 1e-9) / max(window[start]["close"], 1e-9)) / step)
    if not speeds:
        return 0, {"positiveRatio": 0.0, "variation": None}
    positive_ratio = sum(value > 0 for value in speeds) / len(speeds)
    average_speed = avg(speeds)
    deviation = math.sqrt(avg([(value - average_speed) ** 2 for value in speeds]))
    variation = deviation / max(abs(average_speed), 1e-9)
    if average_speed <= 0:
        points = 0
    elif positive_ratio == 1 and variation <= 0.60:
        points = 5
    elif positive_ratio >= 0.75 and variation <= 1.00:
        points = 4
    elif positive_ratio >= 0.75:
        points = 3
    elif positive_ratio >= 0.50:
        points = 2
    else:
        points = 1
    return points, {"positiveRatio": positive_ratio, "variation": variation}


def recent_high_zone_points(bars, historical_high=None):
    current = bars[-1]["close"]
    history_high = historical_high or max(row["high"] for row in bars)
    distance = max(0.0, 1 - current / max(history_high, 1e-9))
    slope5 = positive_log_slope(bars, 5)
    slope10 = positive_log_slope(bars, 10)
    current_atr = max(atr(bars, min(20, len(bars))), 1e-9)
    ranges = true_ranges(bars)
    recent_range = avg(ranges[-5:])
    prior_range = avg(ranges[-20:-5], recent_range)
    calm = recent_range <= prior_range * 0.90
    recent_lows = [row["low"] for row in bars[-5:]]
    prior_lows = [row["low"] for row in bars[-10:-5]]
    rising_lows = len(prior_lows) >= 3 and min(recent_lows) > min(prior_lows)
    large_bearish = any(
        max(0.0, bars[index - 1]["close"] - bars[index]["close"]) >= current_atr * 1.5
        or max(0.0, bars[index]["open"] - bars[index]["close"]) >= current_atr * 1.25
        for index in range(max(1, len(bars) - 5), len(bars))
    )
    proximity = 2 if distance <= 0.03 else 1 if distance <= 0.08 else 0
    direction = 1 if slope5 > 0.0005 and slope10 > 0.0005 else 0
    low_score = 1 if rising_lows else 0
    calm_score = 1 if calm and not large_bearish else 0
    return proximity + direction + low_score + calm_score, {
        "athDistance": distance,
        "slope5": slope5,
        "slope10": slope10,
        "volatilityContracting": calm,
        "risingLows": rising_lows,
        "largeBearish": large_bearish,
    }


def shallow_pullback_recovery_points(bars):
    window = bars[-min(len(bars), 20) :]
    high_index = max(range(len(window)), key=lambda index: window[index]["high"])
    high = window[high_index]["high"]
    current = window[-1]["close"]
    post = window[high_index:]
    low = min(row["low"] for row in post)
    drawdown = max(0.0, 1 - current / max(high, 1e-9))
    recovery = max(0.0, current - low) / max(high - low, 1e-9)
    ma5 = avg([row["close"] for row in bars[-5:]])
    if drawdown <= 0.03 and current >= ma5:
        points = 4
    elif drawdown <= 0.06 and (recovery >= 0.50 or current >= ma5):
        points = 3
    elif drawdown <= 0.10 and recovery >= 0.25:
        points = 2
    elif drawdown <= 0.15 and current > low:
        points = 1
    else:
        points = 0
    return points, {"drawdown": drawdown, "recovery": recovery}


def ath_ma_structure_score(bars, ma, historical_high=None):
    current = bars[-1]["close"]
    history_high = historical_high or max(row["high"] for row in bars)
    distance = max(0.0, 1 - current / max(history_high, 1e-9))
    ath_position = 4 if distance <= 0.03 else 3 if distance <= 0.05 else 2 if distance <= 0.10 else 1 if distance <= 0.20 else 0
    short, mid, long = ma["short"][-1], ma["mid"][-1], ma["long"][-1]
    if any(value is None for value in (short, mid, long)):
        price_position = ordering = ma_direction = 0
    else:
        price_position = 2 if current > max(short, mid, long) else 1 if current > mid and current > long else 0
        ordering = 2 if short > mid > long else 1 if short > mid or abs(mid - long) <= current * 0.012 else 0
        lookback = min(5, len(bars) - 1)
        previous = (ma["short"][-1 - lookback], ma["mid"][-1 - lookback], ma["long"][-1 - lookback])
        if any(value is None for value in previous):
            ma_direction = 0
        else:
            rising = [now > before for now, before in zip((short, mid, long), previous)]
            ma_direction = 2 if all(rising) else 1 if rising[0] and rising[1] else 0
    parts = {
        "ATHからの位置": ath_position,
        "株価と移動平均線の位置": price_position,
        "移動平均線の並び": ordering,
        "移動平均線の方向": ma_direction,
    }
    return sum(parts.values()), parts, {"athDistance": distance}


def detect_recent_surge(bars, lookback=20):
    start = max(0, len(bars) - lookback)
    window = bars[start:]
    current_atr = max(atr(bars, min(20, len(bars))), 1e-9)
    running_low = window[0]["low"]
    running_low_index = start
    trigger = None
    for local_index, row in enumerate(window[1:], 1):
        global_index = start + local_index
        if row["low"] < running_low:
            running_low = row["low"]
            running_low_index = global_index
        rise_atr = max(0.0, row["high"] - running_low) / current_atr
        rise_percent = max(0.0, row["high"] / max(running_low, 1e-9) - 1)
        period_return = pct_change(row["close"], window[0]["close"])
        if rise_atr >= 4.0 or rise_percent >= 0.12 or period_return >= 0.12:
            trigger = (running_low_index, global_index)
            break
    if trigger is None:
        return {"detected": False, "atr": current_atr}
    low_index, trigger_index = trigger
    peak_index = max(range(trigger_index, len(bars)), key=lambda index: bars[index]["high"])
    low = bars[low_index]["low"]
    peak = bars[peak_index]["high"]
    return {
        "detected": True,
        "atr": current_atr,
        "lowIndex": low_index,
        "triggerIndex": trigger_index,
        "peakIndex": peak_index,
        "low": low,
        "peak": peak,
        "riseAtr": (peak - low) / current_atr,
        "risePercent": peak / max(low, 1e-9) - 1,
    }


def post_surge_adjustment(bars, historical_high=None):
    surge = detect_recent_surge(bars)
    if not surge["detected"]:
        return 0, {
            "surgeDetected": False,
            "postSurgeState": "急騰条件なし",
            "postSurgeAdjustment": 0,
            "reboundConfirmationCount": 0,
        }

    peak_index = surge["peakIndex"]
    peak = surge["peak"]
    post_peak = bars[peak_index:]
    current = bars[-1]["close"]
    low_index = min(range(peak_index, len(bars)), key=lambda index: bars[index]["low"])
    post_low = bars[low_index]["low"]
    drawdown = max(0.0, 1 - current / max(peak, 1e-9))
    drawdown_atr = max(0.0, peak - current) / surge["atr"]
    bounce_fraction = max(0.0, current - post_low) / max(peak - post_low, 1e-9)
    slope5 = positive_log_slope(bars, 5)
    slope10 = positive_log_slope(bars, 10)

    after_peak = post_peak[1:]
    if len(after_peak) >= 3:
        split = max(1, len(after_peak) // 2)
        older, newer = after_peak[:split], after_peak[split:]
        lower_highs = bool(newer) and max(row["high"] for row in newer) < max(row["high"] for row in older)
        lower_lows = bool(newer) and min(row["low"] for row in newer) < min(row["low"] for row in older)
    else:
        lower_highs = lower_lows = False
    running_low = bars[peak_index]["low"]
    new_low_count = 0
    for row in after_peak:
        if row["low"] < running_low:
            new_low_count += 1
            running_low = row["low"]
    decline_structure = (lower_highs and lower_lows) or new_low_count >= 2

    ma5 = avg([row["close"] for row in bars[-5:]])
    closes_after_low = [row["close"] for row in bars[low_index + 1 : -1]]
    rebound_high_break = bool(closes_after_low) and current > max(closes_after_low)
    average_volume = avg([row["volume"] for row in bars[-20:] if row["volume"] > 0])
    volume_rebound = any(
        index > 0
        and bars[index]["close"] > bars[index]["open"]
        and bars[index]["close"] > bars[index - 1]["close"]
        and average_volume > 0
        and bars[index]["volume"] >= average_volume * 1.05
        for index in range(max(1, len(bars) - 3), len(bars))
    )
    confirmations = {
        "直近5期間の傾きがプラス": slope5 > 0.0005,
        "下落幅の50％超を回復": bounce_fraction > 0.50,
        "直近の戻り高値を上抜く": rebound_high_break,
        "短期移動平均線を終値で回復": current >= ma5,
        "出来高を伴う陽線": volume_rebound,
    }
    confirmation_count = sum(confirmations.values())
    periods_since_low = len(bars) - 1 - low_index
    rebound_confirmed = confirmation_count >= 2 and periods_since_low >= 2
    after_peak_periods = len(bars) - 1 - peak_index
    unrecovered = bounce_fraction <= 0.50

    adjustment = 0
    state = "方向判定なし"
    if after_peak_periods >= 2 and decline_structure and unrecovered and not rebound_confirmed:
        if (drawdown > 0.10 or drawdown_atr > 2.5) and new_low_count >= 2:
            adjustment, state = -5, "急騰後調整・安値更新中"
        elif drawdown >= 0.06 and slope10 < 0:
            adjustment, state = -4, "急騰後調整・反発未確認"
        elif 0.03 <= drawdown < 0.06 and slope5 < 0:
            adjustment, state = -3, "急騰後調整・反発未確認"
        elif 0 < drawdown < 0.03 and slope5 < 0:
            adjustment, state = -2, "急騰後小幅下落・反発未確認"
    elif rebound_confirmed and drawdown >= 0.03:
        state = "反発確認・減点解除"

    high_zone_metrics = recent_high_zone_points(bars, historical_high)[1]
    history_high = historical_high or max(row["high"] for row in bars)
    ath_distance = max(0.0, 1 - current / max(history_high, 1e-9))
    after_trigger_periods = len(bars) - 1 - surge["triggerIndex"]
    gentle_up = slope5 > 0.0005 and slope10 > 0.0005
    no_large_bearish = not high_zone_metrics["largeBearish"]
    if adjustment == 0 and after_trigger_periods >= 2:
        if (
            ath_distance <= 0.03
            and gentle_up
            and high_zone_metrics["volatilityContracting"]
            and high_zone_metrics["risingLows"]
            and no_large_bearish
        ):
            adjustment, state = 4, "ATH近辺で低ボラ上昇継続"
        elif ath_distance <= 0.05 and gentle_up and high_zone_metrics["risingLows"] and no_large_bearish:
            adjustment, state = 3, "高値圏で安値切り上げ"
        elif drawdown <= 0.07 and gentle_up and no_large_bearish and (
            high_zone_metrics["volatilityContracting"] or high_zone_metrics["risingLows"]
        ):
            adjustment, state = 2, "高値圏で緩やかな上昇継続"

    return adjustment, {
        "surgeDetected": True,
        "surgeRiseAtr": surge["riseAtr"],
        "surgeRisePercent": surge["risePercent"],
        "surgePeakDate": bars[peak_index]["date"],
        "postSurgeDrawdown": drawdown,
        "postSurgeDrawdownAtr": drawdown_atr,
        "postSurgeState": state,
        "postSurgeAdjustment": adjustment,
        "reboundConfirmationCount": confirmation_count,
        "reboundConfirmed": rebound_confirmed,
        "reboundConfirmations": confirmations,
        "bounceFraction": bounce_fraction,
        "newLowCount": new_low_count,
        "lowerHighs": lower_highs,
        "lowerLows": lower_lows,
    }


def linear_high_zone_score(bars, short_period, mid_period, long_period, historical_high=None):
    closes = [row["close"] for row in bars]
    ma = {
        "short": moving_average(closes, short_period),
        "mid": moving_average(closes, mid_period),
        "long": moving_average(closes, long_period),
    }
    fit20, fit20_metrics = linear_fit_points(bars, 20, 9)
    fit60, fit60_metrics = linear_fit_points(bars, 60, 6)
    direction, direction_efficiency = directional_efficiency_points(bars, 20)
    stability, stability_metrics = ascent_stability_points(bars, 20)
    high_zone, high_zone_metrics = recent_high_zone_points(bars, historical_high)
    pullback, pullback_metrics = shallow_pullback_recovery_points(bars)
    base_parts = {
        "直近20期間の直線適合度": fit20,
        "直近60期間の直線適合度": fit60,
        "方向効率": direction,
        "上昇速度の安定性": stability,
        "高値圏での緩やかな上昇": high_zone,
        "押し目の浅さ・回復力": pullback,
    }
    base_score = sum(base_parts.values())
    adjustment, adjustment_metrics = post_surge_adjustment(bars, historical_high)
    final_score = max(0, min(35, base_score + adjustment))
    details = {**base_parts, "急騰後補正": adjustment}
    return final_score, details, ma, {
        "linearBaseScore": base_score,
        "linearFinalScore": final_score,
        "fit20": fit20_metrics,
        "fit60": fit60_metrics,
        "directionalEfficiency": direction_efficiency,
        "ascentStability": stability_metrics,
        "highZone": high_zone_metrics,
        "pullbackRecovery": pullback_metrics,
        **adjustment_metrics,
    }


def score_band(score):
    if score >= 100:
        return "歴史的な高順張り"
    if score >= 90:
        return "極めて高い"
    if score >= 80:
        return "高い"
    if score >= 70:
        return "やや高い"
    if score >= 55:
        return "変調・監視"
    if score >= 40:
        return "低い"
    return "順張り不成立・ランダム性が高い"


def trend_score(bars, short_period, mid_period, long_period, structure_window):
    closes = [bar["close"] for bar in bars]
    short = moving_average(closes, short_period)
    mid = moving_average(closes, mid_period)
    long = moving_average(closes, long_period)
    latest = closes[-1]
    current_atr = max(atr(bars, min(20, len(bars))), 1e-9)
    ma_values = (short[-1], mid[-1], long[-1])
    if any(value is None for value in ma_values):
        price_position = ordering = ma_slope = 0
    else:
        short_value, mid_value, long_value = ma_values
        near_mid = max(abs(mid_value) * 0.015, current_atr * 0.35)
        if latest > max(ma_values):
            price_position = 6
        elif latest < short_value and latest > mid_value and latest > long_value:
            price_position = 4
        elif abs(latest - mid_value) <= near_mid or (
            latest >= mid_value * 0.98 and latest >= long_value * 0.98
        ):
            price_position = 2
        else:
            price_position = 0

        near_order = max(abs(avg(ma_values)) * 0.012, current_atr * 0.25)
        if short_value > mid_value > long_value:
            ordering = 5
        elif short_value > mid_value and abs(mid_value - long_value) <= near_order:
            ordering = 3
        elif max(ma_values) - min(ma_values) <= near_order * 1.5:
            ordering = 1
        else:
            ordering = 0

        slope_lookback = max(2, min(6, structure_window // 2))
        previous_values = (short[-1 - slope_lookback], mid[-1 - slope_lookback], long[-1 - slope_lookback])
        if any(value is None for value in previous_values):
            ma_slope = 0
        else:
            short_up = short_value > previous_values[0]
            mid_up = mid_value > previous_values[1]
            long_up = long_value > previous_values[2]
            flat_tolerance = max(abs(avg(ma_values)) * 0.001, current_atr * 0.08)
            nearly_flat = all(abs(current - previous) <= flat_tolerance for current, previous in zip(ma_values, previous_values))
            if short_up and mid_up and long_up:
                ma_slope = 4
            elif short_up and mid_up:
                ma_slope = 3
            elif mid_up:
                ma_slope = 2
            elif nearly_flat:
                ma_slope = 1
            else:
                ma_slope = 0

    width = min(structure_window, max(3, len(bars) // 4))
    recent = bars[-width:]
    previous = bars[-2 * width : -width]
    if previous:
        recent_high = max(row["high"] for row in recent)
        previous_high = max(row["high"] for row in previous)
        recent_low = min(row["low"] for row in recent)
        previous_low = min(row["low"] for row in previous)
        higher_high = 4 if recent_high > previous_high + current_atr * 0.15 else 2 if recent_high >= previous_high - current_atr * 0.25 else 0
        higher_low = 4 if recent_low > previous_low + current_atr * 0.15 else 2 if recent_low >= previous_low - current_atr * 0.25 else 0
    else:
        higher_high = higher_low = 0

    smooth_window = min(max(10, structure_window * 2), len(bars) - 1)
    smooth_return = price_return(bars, smooth_window)
    smooth_er = efficiency_ratio(bars, smooth_window)
    if smooth_return > 0 and smooth_er >= 0.45:
        smoothness = 2
    elif smooth_return >= 0 and smooth_er >= 0.20:
        smoothness = 1
    else:
        smoothness = 0

    components = {
        "株価と移動平均線の位置関係": price_position,
        "移動平均線の並び": ordering,
        "移動平均線の傾き": ma_slope,
        "高値切り上げ": higher_high,
        "安値切り上げ": higher_low,
        "上昇の継続性・滑らかさ": smoothness,
    }
    return sum(components.values()), components, {"short": short, "mid": mid, "long": long}


def volume_quality(bars, breakout_window):
    recent = bars[-max(60, breakout_window * 3) :]
    volumes = [bar["volume"] for bar in recent if bar["volume"] > 0]
    if len(volumes) < 12:
        return 15, {"上昇足と下落足の出来高比": 4, "出来高の継続性": 4, "ブレイク時の出来高": 3, "下落時の出来高": 4}, True
    up = []
    down = []
    for index in range(1, len(recent)):
        target = up if recent[index]["close"] > recent[index - 1]["close"] else down
        if recent[index]["volume"] > 0:
            target.append(recent[index]["volume"])
    ratio = avg(up) / max(avg(down), 1)
    if ratio >= 1.50:
        ratio_score = 9
    elif ratio >= 1.30:
        ratio_score = 7
    elif ratio >= 1.10:
        ratio_score = 5
    elif ratio >= 0.90:
        ratio_score = 2
    else:
        ratio_score = 0

    sample = recent[-min(len(recent), 40) :]
    bucket_size = max(3, len(sample) // 4)
    buckets = [sample[index : index + bucket_size] for index in range(0, len(sample), bucket_size)][-4:]
    bucket_volumes = [avg([row["volume"] for row in bucket]) for bucket in buckets if bucket]
    bucket_closes = [bucket[-1]["close"] for bucket in buckets if bucket]
    price_rising = len(bucket_closes) >= 3 and bucket_closes[-1] > bucket_closes[0]
    continuously_rising = len(bucket_volumes) >= 3 and all(
        bucket_volumes[index] >= bucket_volumes[index - 1] * 0.97 for index in range(1, len(bucket_volumes))
    ) and bucket_volumes[-1] >= bucket_volumes[0] * 1.10
    high_level = len(bucket_volumes) >= 3 and min(bucket_volumes[-3:]) >= bucket_volumes[0] * 1.05
    stable = bucket_volumes and max(bucket_volumes) <= max(min(bucket_volumes), 1) * 1.35
    if price_rising and continuously_rising:
        persistence = 8
    elif price_rising and high_level:
        persistence = 7
    elif price_rising and stable:
        persistence = 5
    elif max(volumes[-20:]) >= max(median(volumes[-20:]), 1) * 1.8:
        persistence = 2
    else:
        persistence = 0

    breakout_ratios = []
    for index in range(breakout_window, len(recent)):
        prior_high = max(row["high"] for row in recent[index - breakout_window : index])
        if recent[index]["close"] > prior_high:
            base_volume = avg([row["volume"] for row in recent[index - breakout_window : index]])
            breakout_ratios.append(recent[index]["volume"] / max(base_volume, 1))
    breakout_ratio = max(breakout_ratios[-5:], default=0)
    if breakout_ratio >= 2.0:
        breakout = 6
    elif breakout_ratio >= 1.5:
        breakout = 5
    elif breakout_ratio >= 1.2:
        breakout = 3
    elif breakout_ratio >= 1.0:
        breakout = 1
    else:
        breakout = 0

    down_average = avg(down)
    up_average = avg(up)
    all_average = avg(volumes)
    down_ratio = down_average / max(up_average, 1)
    if down_ratio <= 0.70:
        down_score = 7
    elif down_average <= all_average:
        down_score = 5
    elif down_ratio <= 1.15:
        down_score = 3
    else:
        down_score = 0
    parts = {
        "上昇足と下落足の出来高比": ratio_score,
        "出来高の継続性": persistence,
        "ブレイク時の出来高": breakout,
        "下落時の出来高": down_score,
    }
    return sum(parts.values()), parts, False


def relative_series(asset, benchmark):
    benchmark_by_date = {bar["date"]: bar["close"] for bar in benchmark}
    series = []
    for bar in asset:
        base = benchmark_by_date.get(bar["date"])
        if base:
            series.append((bar["date"], bar["close"] / base))
    return series


def relative_change(series, periods):
    if len(series) <= periods:
        return 0.0
    return pct_change(series[-1][1], series[-periods - 1][1])


def relative_direction(series, short_period, mid_period):
    short_change = relative_change(series, short_period)
    mid_change = relative_change(series, mid_period)
    return short_change, mid_change


def pullback_quality(bars, ma, lookback):
    current = bars[-1]["close"]
    window = bars[-min(len(bars), lookback) :]
    high_local = max(range(len(window)), key=lambda index: window[index]["high"])
    high_bar = window[high_local]
    preceding = window[: high_local + 1]
    start_bar = min(preceding, key=lambda row: row["low"]) if preceding else window[0]
    rise = max(high_bar["high"] - start_bar["low"], 1e-9)
    retracement = max(0.0, (high_bar["high"] - current) / rise)
    if retracement <= 0.236:
        depth = 6
    elif retracement <= 0.382:
        depth = 5
    elif retracement <= 0.50:
        depth = 3
    elif retracement <= 0.618:
        depth = 1
    else:
        depth = 0

    short_value = ma["short"][-1]
    mid_value = ma["mid"][-1]
    current_atr = max(atr(bars, min(20, len(bars))), 1e-9)
    prior_low = min(row["low"] for row in window[:-1]) if len(window) > 1 else current
    if short_value is not None and current >= short_value:
        support = 5
    elif mid_value is not None and current > mid_value:
        support = 4
    elif mid_value is not None and (
        abs(current - mid_value) <= max(abs(mid_value) * 0.02, current_atr * 0.40)
        or abs(current - prior_low) <= current_atr * 0.35
    ):
        support = 2
    elif mid_value is not None and (current < mid_value or current < prior_low):
        support = 0
    else:
        support = 2

    pullback_rows = window[high_local + 1 :] or window[-min(5, len(window)) :]
    rise_rows = preceding[-max(5, min(len(preceding), len(pullback_rows) * 2)) :]
    pullback_volume = avg([row["volume"] for row in pullback_rows if row["volume"] > 0])
    rise_volume = avg([row["volume"] for row in rise_rows if row["volume"] > 0])
    overall_volume = avg([row["volume"] for row in window if row["volume"] > 0])
    if pullback_volume and rise_volume and pullback_volume <= rise_volume * 0.70:
        pullback_volume_score = 5
    elif pullback_volume and pullback_volume <= max(overall_volume, 1):
        pullback_volume_score = 4
    elif pullback_volume and rise_volume and pullback_volume <= rise_volume * 1.15:
        pullback_volume_score = 2
    else:
        pullback_volume_score = 0

    structure_width = max(3, min(10, len(bars) // 6))
    recent_low = min(row["low"] for row in bars[-structure_width:])
    previous_low = min(row["low"] for row in bars[-2 * structure_width : -structure_width])
    low_tolerance = current_atr * 0.25
    if previous_low and recent_low > previous_low + low_tolerance and current >= recent_low + current_atr * 0.40:
        stopping = 4
    elif previous_low and recent_low > previous_low and current >= recent_low:
        stopping = 3
    elif previous_low and recent_low >= previous_low - low_tolerance:
        stopping = 1
    else:
        stopping = 0
    parts = {
        "直前上昇に対する押しの深さ": depth,
        "支持線・移動平均線の維持": support,
        "押し目中の出来高": pullback_volume_score,
        "安値切り上げ・停止力": stopping,
    }
    return sum(parts.values()), parts, retracement


def downside_volatility(bars):
    if len(bars) < 2:
        return 0.0
    values = []
    for index in range(1, len(bars)):
        previous = bars[index - 1]["close"]
        downside = max(0.0, previous - bars[index]["low"]) / max(previous, 1e-9)
        values.append(downside)
    return math.sqrt(avg([value * value for value in values]))


def downside_expansion_penalty(expansion):
    if expansion < 0.8:
        return 0
    if expansion < 1.0:
        return 1
    if expansion < 1.2:
        return 3
    if expansion < 1.5:
        return 5
    return 7


def five_day_speed_penalty(drop_atr):
    if drop_atr < 1.5:
        return 0
    if drop_atr < 2.5:
        return 2
    if drop_atr < 3.5:
        return 4
    if drop_atr < 5.0:
        return 6
    return 7


def large_down_frequency_penalty(count):
    if count <= 0:
        return 0
    if count == 1:
        return 2
    if count == 2:
        return 3
    if count == 3:
        return 4
    return 5


def downside_quality(bars):
    current_atr = max(atr(bars, min(20, len(bars))), 1e-9)
    recent_slice = bars[-11:]
    baseline_slice = bars[-71:-10] if len(bars) >= 71 else bars[:-10]
    recent_downside = downside_volatility(recent_slice)
    baseline_downside = downside_volatility(baseline_slice)
    expansion = recent_downside / max(baseline_downside, 1e-9)
    volatility_penalty = downside_expansion_penalty(expansion)

    recent_five = bars[-6:]
    five_day_drop = max(0.0, max(row["high"] for row in recent_five[:-1]) - bars[-1]["close"]) / current_atr
    speed_penalty = five_day_speed_penalty(five_day_drop)

    large_down_days = 0
    recent_twenty_start = max(1, len(bars) - 20)
    for index in range(recent_twenty_start, len(bars)):
        down_move = max(0.0, bars[index - 1]["close"] - bars[index]["close"])
        if down_move >= current_atr:
            large_down_days += 1
    frequency_penalty = large_down_frequency_penalty(large_down_days)

    crash_index = None
    for index in range(max(1, len(bars) - 20), len(bars)):
        close_drop = max(0.0, bars[index - 1]["close"] - bars[index]["close"])
        bearish_body = max(0.0, bars[index]["open"] - bars[index]["close"])
        if close_drop >= current_atr * 1.5 or bearish_body >= current_atr * 1.25:
            crash_index = index
    continuation_penalty = 0
    continuation_state = "安値更新なし・反発"
    if crash_index is not None and crash_index < len(bars) - 1:
        crash_low = bars[crash_index]["low"]
        running_low = crash_low
        new_lows = 0
        reacceleration = False
        for index in range(crash_index + 1, len(bars)):
            if bars[index]["low"] < running_low:
                new_lows += 1
                running_low = bars[index]["low"]
            close_drop = max(0.0, bars[index - 1]["close"] - bars[index]["close"])
            bearish_body = max(0.0, bars[index]["open"] - bars[index]["close"])
            if close_drop >= current_atr * 1.5 or bearish_body >= current_atr * 1.5:
                reacceleration = True
        if reacceleration and new_lows:
            continuation_penalty, continuation_state = 3, "大陰線再発・下落加速"
        elif new_lows >= 2:
            continuation_penalty, continuation_state = 2, "複数回安値更新"
        elif new_lows == 1:
            continuation_penalty, continuation_state = 1, "一度だけ安値更新"

    recent_intraday = bars[-5:]
    weak_closes = 0
    lower_breaks = 0
    gap_downs = 0
    for index in range(len(bars) - len(recent_intraday), len(bars)):
        row = bars[index]
        close_position = (row["close"] - row["low"]) / max(row["high"] - row["low"], 1e-9)
        if close_position <= 0.25:
            weak_closes += 1
        if index > 0 and row["low"] < bars[index - 1]["low"] and close_position < 0.50:
            lower_breaks += 1
        if index > 0 and row["open"] < bars[index - 1]["close"] - current_atr * 0.50:
            gap_downs += 1
    latest_position = (bars[-1]["close"] - bars[-1]["low"]) / max(bars[-1]["high"] - bars[-1]["low"], 1e-9)
    if gap_downs and weak_closes >= 2:
        intraday_penalty, intraday_state = 3, "窓下げ＋安値圏引けが反復"
    elif weak_closes >= 2 or lower_breaks >= 2:
        intraday_penalty, intraday_state = 2, "安値圏引けが複数回"
    elif weak_closes == 1 or lower_breaks == 1:
        intraday_penalty, intraday_state = 1, "安値圏引けが1回"
    elif latest_position >= 0.50:
        intraday_penalty, intraday_state = 0, "安値から戻して終了"
    else:
        intraday_penalty, intraday_state = 0, "明確な弱さなし"

    penalties = {
        "下方向ボラティリティの拡大": volatility_penalty,
        "5日以内の下落速度": speed_penalty,
        "大幅下落日の頻度": frequency_penalty,
        "急落後の売り継続": continuation_penalty,
        "日中値幅・終値位置": intraday_penalty,
    }
    total_penalty = min(25, sum(penalties.values()))
    parts = {name: -points for name, points in penalties.items()}
    return 25 - total_penalty, parts, {
        "downsideExpansion": expansion,
        "fiveDayDropAtr": five_day_drop,
        "largeDownDayCount": large_down_days,
        "continuationState": continuation_state,
        "intradayState": intraday_state,
        "downsidePenalty": total_penalty,
        "atr": current_atr,
    }


def sector_relative_score(sector_series, short_period, mid_period):
    short, mid = relative_direction(sector_series, short_period, mid_period)
    if short > 0.05 and mid > 0.05:
        excess = 5
    elif short > 0 and mid > 0:
        excess = 4
    elif short > 0 or mid > 0:
        excess = 3
    elif max(short, mid) > -0.03:
        excess = 1
    else:
        excess = 0
    values = [value for _, value in sector_series]
    line = 3 if len(values) > short_period and slope(values, short_period) > 0 else (1 if short > -0.01 else 0)
    high = 2 if values and values[-1] >= max(values[-mid_period:]) * 0.995 else 0
    return excess + line + high, {"セクターの超過リターン": excess, "セクターRSラインの方向": line, "RSラインの高値更新": high}, (short, mid)


def stock_relative_score(stock_series, sector_series, short_period, mid_period):
    short, mid = relative_direction(stock_series, short_period, mid_period)
    if short > 0.05 and mid > 0.05:
        excess = 7
    elif short > 0 and mid > 0:
        excess = 6
    elif short > 0 or mid > 0:
        excess = 4
    elif max(short, mid) > -0.03:
        excess = 2
    else:
        excess = 0
    values = [value for _, value in stock_series]
    direction = 5 if len(values) > short_period and slope(values, short_period) > 0 else (2 if short > -0.01 else 0)
    sector_short = relative_change(sector_series, short_period)
    if short > 0 and sector_short <= 0:
        leadership = 3
    elif short > sector_short:
        leadership = 2
    elif short > 0:
        leadership = 1
    else:
        leadership = 0
    return excess + direction + leadership, {"セクターに対する超過リターン": excess, "個別株RSラインの方向": direction, "セクターに対する先行性": leadership}, (short, mid)


def timeframe_score(asset_bars, sector_bars, market_bars, timeframe, single_level=False, historical_high=None):
    if timeframe == "daily":
        short_period, mid_period, long_period = 20, 50, 200
        breakout = 20
    else:
        short_period, mid_period, long_period = 10, 20, 40
        breakout = 10
    linear, linear_parts, ma, linear_metrics = linear_high_zone_score(asset_bars, short_period, mid_period, long_period, historical_high)
    ath_ma, ath_ma_parts, ath_ma_metrics = ath_ma_structure_score(asset_bars, ma, historical_high)
    volume, volume_parts, volume_proxy_missing = volume_quality(asset_bars, breakout)
    sector_rs_series = relative_series(sector_bars, market_bars)
    if single_level:
        stock_rs_series = relative_series(asset_bars, market_bars)
    else:
        stock_rs_series = relative_series(asset_bars, sector_bars)
    downside, downside_parts, downside_metrics = downside_quality(asset_bars)
    components = {
        "直線上昇・高値圏上昇": linear,
        "出来高の質": volume,
        "下方向ボラティリティ・下落速度": downside,
        "ATH位置・移動平均線構造": ath_ma,
    }
    details = {**linear_parts, **volume_parts, **downside_parts, **ath_ma_parts}
    return {
        "score": int(round(sum(components.values()))),
        "components": components,
        "details": details,
        "metrics": {
            **linear_metrics,
            **downside_metrics,
            **ath_ma_metrics,
            "volumeProxyMissing": volume_proxy_missing,
        },
        "ma": ma,
        "stockRsSeries": stock_rs_series,
        "sectorRsSeries": sector_rs_series,
    }


def monthly_bonus(monthly, daily_score, weekly_score, weekly_bars):
    if len(monthly) < 12:
        return 0, {"高値・安値の切り上げ": 0, "移動平均線の並びと傾き": 0, "月足の下方向ボラティリティ": 0, "出来高と価格上昇の整合性": 0}, 0
    width = min(6, len(monthly) // 2)
    recent = monthly[-width:]
    previous = monthly[-2 * width : -width]
    high_low = 3 if max(x["high"] for x in recent) > max(x["high"] for x in previous) and min(x["low"] for x in recent) > min(x["low"] for x in previous) else 0
    closes = [x["close"] for x in monthly]
    ma10 = moving_average(closes, 10)
    ma20 = moving_average(closes, 20)
    if (
        ma20[-1] is not None
        and ma20[-4] is not None
        and ma10[-1] is not None
        and ma10[-4] is not None
        and closes[-1] > ma10[-1] > ma20[-1]
        and ma10[-1] > ma10[-4]
        and ma20[-1] >= ma20[-4]
    ):
        ma_score = 3
    elif ma10[-1] is not None and ma10[-4] is not None and closes[-1] > ma10[-1] and ma10[-1] > ma10[-4]:
        ma_score = 2
    elif closes[-1] > avg(closes[-6:]):
        ma_score = 1
    else:
        ma_score = 0
    monthly_atr = max(atr(monthly, min(20, len(monthly))), 1e-9)
    shock = max(0, monthly[-2]["close"] - monthly[-1]["close"]) / monthly_atr
    vol_score = 2 if shock < 0.75 else (1 if shock < 1.25 else 0)
    up_volume = [monthly[i]["volume"] for i in range(1, len(monthly)) if monthly[i]["close"] > monthly[i - 1]["close"] and monthly[i]["volume"] > 0]
    down_volume = [monthly[i]["volume"] for i in range(1, len(monthly)) if monthly[i]["close"] <= monthly[i - 1]["close"] and monthly[i]["volume"] > 0]
    if up_volume and down_volume and avg(up_volume) > avg(down_volume) * 1.1:
        volume_score = 2
    elif up_volume and down_volume and avg(up_volume) >= avg(down_volume) * 0.9:
        volume_score = 1
    else:
        volume_score = 0
    raw = high_low + ma_score + vol_score + volume_score
    state_bonus = 10 if raw >= 9 else 8 if raw >= 7 else 6 if raw >= 5 else 3 if raw >= 3 else 1 if raw >= 1 else 0
    weekly_width = min(8, len(weekly_bars) // 3)
    weekly_lower_low = weekly_width >= 3 and min(x["low"] for x in weekly_bars[-weekly_width:]) < min(x["low"] for x in weekly_bars[-2 * weekly_width : -weekly_width])
    if daily_score >= 55 and weekly_score >= 55:
        cap = 10
    elif weekly_score >= 55:
        cap = 8
    elif weekly_score >= 45:
        cap = 5
    elif weekly_lower_low:
        cap = 3
    else:
        cap = 0
    parts = {"高値・安値の切り上げ": high_low, "移動平均線の並びと傾き": ma_score, "月足の下方向ボラティリティ": vol_score, "出来高と価格上昇の整合性": volume_score}
    return min(state_bonus, cap), parts, cap


def three_year_penalty(bars):
    current_date = date.fromisoformat(bars[-1]["date"])
    target = current_date - timedelta(days=365 * 3)
    candidates = [bar for bar in bars if date.fromisoformat(bar["date"]) <= target]
    if not candidates:
        return 0, None
    old = candidates[-1]["adj"]
    change = pct_change(bars[-1]["adj"], old)
    if change >= 0:
        penalty = 0
    elif change > -0.10:
        penalty = 2
    elif change > -0.20:
        penalty = 4
    elif change > -0.35:
        penalty = 7
    elif change > -0.50:
        penalty = 10
    else:
        penalty = 15
    return penalty, change


def long_decline_cap(daily, weekly, three_year_change):
    weekly_closes = [bar["close"] for bar in weekly]
    ma40 = moving_average(weekly_closes, 40)
    checks = [
        three_year_change is not None and three_year_change < 0,
        ma40[-1] is not None and weekly_closes[-1] < ma40[-1],
        ma40[-1] is not None and len(ma40) >= 5 and ma40[-5] is not None and ma40[-1] < ma40[-5],
        price_return(weekly, 52) < 0,
    ]
    count = sum(checks)
    cap = None if count <= 1 else 84 if count == 2 else 74 if count == 3 else 64
    return cap, checks


def acute_downside_penalty(daily):
    ranges = true_ranges(daily)
    selected = None
    for index in range(max(20, len(daily) - 10), len(daily)):
        local_atr = avg(ranges[max(0, index - 20) : index])
        if local_atr <= 0:
            continue
        shock = max(0, daily[index - 1]["close"] - daily[index]["close"]) / local_atr
        if shock >= 1.0:
            selected = (index, shock, local_atr)
    if not selected:
        return 0, {"shock": 0, "age": None, "original": 0, "recovery": "該当なし"}
    index, shock, local_atr = selected
    base = 2 if shock < 1.5 else 5 if shock < 2.0 else 8 if shock < 2.5 else 10
    target = daily[index]
    position = (target["close"] - target["low"]) / max(target["high"] - target["low"], 1e-9)
    additional = 0
    if position <= 0.25:
        additional += 2
    prior_volume = avg([row["volume"] for row in daily[max(0, index - 20) : index] if row["volume"] > 0])
    if prior_volume and target["volume"] >= prior_volume * 1.5:
        additional += 2
    prior_support = min(row["low"] for row in daily[max(0, index - 20) : index])
    ma20 = avg([row["close"] for row in daily[max(0, index - 20) : index]])
    if target["close"] < min(prior_support, ma20):
        additional += 2
    original = min(12, base + additional)
    age = len(daily) - 1 - index
    current = daily[-1]["close"]
    current_ma20 = avg([row["close"] for row in daily[-20:]])
    pre_crash_high = max(row["high"] for row in daily[max(0, index - 5) : index])
    if current >= pre_crash_high:
        penalty, recovery = 0, "急落前高値を回復・完全解除"
    elif current >= current_ma20:
        penalty, recovery = min(2, original), "20日線を回復・ほぼ解除"
    elif age >= 3 and min(row["low"] for row in daily[index + 1 :]) >= target["low"]:
        penalty, recovery = int(math.ceil(original / 2)), "3営業日安値更新なし・半減"
    else:
        penalty, recovery = original, "未解除"
    return penalty, {"shock": shock, "age": age, "original": original, "recovery": recovery}


def efficiency_ratio(bars, periods=20):
    if len(bars) <= periods:
        return 0.0
    closes = [bar["close"] for bar in bars[-periods - 1 :]]
    distance = abs(closes[-1] - closes[0])
    path = sum(abs(closes[index] - closes[index - 1]) for index in range(1, len(closes)))
    return distance / path if path else 1.0


def random_market(bars, weekly=False):
    er = efficiency_ratio(bars, 20)
    recent = bars[-10:]
    directions = []
    for index in range(1, len(recent)):
        change = recent[index]["close"] - recent[index - 1]["close"]
        directions.append(1 if change > 0 else -1 if change < 0 else 0)
    reversals = sum(1 for index in range(1, len(directions)) if directions[index] and directions[index - 1] and directions[index] != directions[index - 1])
    # The confirmed rule divides the number of direction changes by 9
    # (the nine day-to-day moves contained in the latest ten sessions).
    reversal_rate = reversals / 9
    ranges = true_ranges(bars)
    atr10 = avg(ranges[-10:])
    atr60 = avg(ranges[-60:])
    expansion = atr10 / max(atr60, 1e-9)
    threshold = median(ranges[-60:]) * 1.5 if len(ranges) >= 20 else avg(ranges) * 1.5
    big_days = sum(1 for value in ranges[-10:] if value > threshold)
    checks = [er < 0.20, reversal_rate >= 0.60, expansion >= 1.40, big_days >= 4]
    count = sum(checks)
    cap = None if count <= 2 else 79 if count == 3 else 69
    if er < 0.15 and expansion >= 1.8:
        cap = 69
    if weekly and count >= 3:
        cap = 59
    return cap, {"er20": er, "reversalRate": reversal_rate, "atrExpansion": expansion, "bigRangeCount": big_days, "checks": checks, "count": count}


def structure_points(bars, window):
    width = min(window, len(bars) // 3)
    if width < 3:
        return 0, "判定期間不足"
    recent = bars[-width:]
    previous = bars[-2 * width : -width]
    higher_high = max(x["high"] for x in recent) > max(x["high"] for x in previous)
    higher_low = min(x["low"] for x in recent) > min(x["low"] for x in previous)
    lower_high = max(x["high"] for x in recent) < max(x["high"] for x in previous)
    lower_low = min(x["low"] for x in recent) < min(x["low"] for x in previous)
    if higher_high and higher_low:
        return 0, "高値・安値切り上げ"
    if not higher_high and higher_low:
        return 1, "高値更新失敗"
    if lower_high and not lower_low:
        return 2, "戻り高値切り下げ"
    if lower_low and not lower_high:
        return 3, "安値切り下げ"
    if lower_high and lower_low:
        return 4, "戻り高値切り下げ後に再度安値更新"
    return 1, "高値更新失敗"


def breakdown_points(daily, weekly, stock_rs_daily, sector_rs_daily, stock_rs_weekly, sector_rs_weekly):
    structure, structure_label = structure_points(weekly, 8)
    daily_closes = [bar["close"] for bar in daily]
    weekly_closes = [bar["close"] for bar in weekly]
    day20 = avg(daily_closes[-20:])
    day50 = avg(daily_closes[-50:])
    week20 = avg(weekly_closes[-20:])
    week40 = avg(weekly_closes[-40:])
    prior_week_low = min(bar["low"] for bar in weekly[-16:-8]) if len(weekly) >= 16 else weekly[-1]["low"]
    if weekly_closes[-1] < week40 and week40 < avg(weekly_closes[-44:-4]):
        support, support_label = 4, "週足長期線割れが継続"
    elif weekly_closes[-1] < min(week20, prior_week_low):
        support, support_label = 3, "週足中期線・前回安値割れ"
    elif daily_closes[-1] < day50:
        support, support_label = 2, "日足中期線割れ"
    elif daily_closes[-1] < day20:
        support, support_label = 1, "日足短期線割れ"
    else:
        support, support_label = 0, "主要支持線維持"

    recent_down = [daily[i]["volume"] for i in range(max(1, len(daily) - 20), len(daily)) if daily[i]["close"] < daily[i - 1]["close"] and daily[i]["volume"] > 0]
    average_volume = avg([bar["volume"] for bar in daily[-20:] if bar["volume"] > 0])
    ratio = avg(recent_down) / max(average_volume, 1)
    large_down_count = sum(1 for value in recent_down if value >= average_volume * 1.5)
    if large_down_count >= 2:
        volume, volume_label = 4, "大出来高下落が複数回"
    elif ratio >= 1.5:
        volume, volume_label = 3, "平均の1.5倍以上"
    elif ratio >= 1.2:
        volume, volume_label = 2, "平均の1.2倍以上"
    elif ratio >= 0.9:
        volume, volume_label = 1, "平均程度"
    else:
        volume, volume_label = 0, "下落時出来高が減少"

    current_atr = max(atr(daily, 20), 1e-9)
    shock = max(0, daily[-2]["close"] - daily[-1]["close"]) / current_atr
    high = max(bar["high"] for bar in daily[-60:])
    drawdown = max(0, 1 - daily[-1]["close"] / high)
    _, daily_structure_label = structure_points(daily, 10)
    if drawdown >= 0.20 and "安値更新" in daily_structure_label:
        speed, speed_label = 4, "急落後も乱高下・安値更新"
    elif drawdown >= 0.20:
        speed, speed_label = 3, "高値から20％以上急落"
    elif shock >= 2:
        speed, speed_label = 2, "2ATRを超える下落"
    elif avg(true_ranges(daily)[-10:]) >= avg(true_ranges(daily)[-60:]) * 1.2:
        speed, speed_label = 1, "値幅がやや拡大"
    else:
        speed, speed_label = 0, "通常範囲"

    stock_day_down = relative_change(stock_rs_daily, 20) < 0
    sector_day_down = relative_change(sector_rs_daily, 20) < 0
    stock_week_down = relative_change(stock_rs_weekly, 13) < 0
    sector_week_down = relative_change(sector_rs_weekly, 13) < 0
    if stock_week_down and sector_week_down:
        rs, rs_label = 4, "両方とも週足で下降"
    elif stock_day_down and sector_day_down:
        rs, rs_label = 3, "両方とも低下"
    elif stock_day_down or sector_day_down:
        rs, rs_label = 2, "片方が明確に低下"
    elif abs(relative_change(stock_rs_daily, 20)) < 0.01 or abs(relative_change(sector_rs_daily, 20)) < 0.01:
        rs, rs_label = 1, "片方が横ばい"
    else:
        rs, rs_label = 0, "セクターRS・個別株RSとも上昇"
    parts = {
        "高値・安値構造の崩壊": {"points": structure, "state": structure_label},
        "支持線・移動平均線割れ": {"points": support, "state": support_label},
        "下落出来高の増大": {"points": volume, "state": volume_label},
        "下落速度・値幅の急拡大": {"points": speed, "state": speed_label},
        "RSの悪化": {"points": rs, "state": rs_label},
    }
    return sum(value["points"] for value in parts.values()), parts


def breakdown_adjustment(points):
    if points <= 3:
        return 0, None, "順張り維持"
    if points <= 6:
        return 5, 89, "軽度警戒"
    if points <= 9:
        return 12, 79, "明確な変調"
    if points <= 12:
        return 22, 69, "上昇構造が崩れ始めた"
    if points <= 15:
        return 32, 59, "順張り上昇が終了"
    return 42, 49, "構造的下降・乱高下"


def crash_cap(daily, weekly):
    daily_atr = max(atr(daily, 20), 1e-9)
    weekly_atr = max(atr(weekly, 20), 1e-9)
    one_day = max(0, daily[-2]["close"] - daily[-1]["close"]) / daily_atr
    one_week = max(0, weekly[-2]["close"] - weekly[-1]["close"]) / weekly_atr
    big_bearish = sum(1 for index in range(-10, 0) if daily[index]["close"] < daily[index]["open"] and daily[index]["open"] - daily[index]["close"] >= daily_atr * 1.25)
    five_day = max(0, daily[-6]["close"] - daily[-1]["close"]) / daily_atr
    avg_volume = avg([bar["volume"] for bar in daily[-20:] if bar["volume"] > 0])
    high_volume = any(bar["volume"] >= avg_volume * 1.5 for bar in daily[-5:]) if avg_volume else False
    support_break = daily[-1]["close"] < min(avg([bar["close"] for bar in daily[-20:]]), min(bar["low"] for bar in daily[-21:-1]))
    weekly_position = (weekly[-1]["close"] - weekly[-1]["low"]) / max(weekly[-1]["high"] - weekly[-1]["low"], 1e-9)
    week_support_break = weekly[-1]["close"] < min(avg([bar["close"] for bar in weekly[-20:]]), min(bar["low"] for bar in weekly[-12:-1]))
    structure, _ = structure_points(daily, 10)
    caps = []
    reasons = []
    if one_day >= 2 or one_week >= 3 or big_bearish >= 2:
        caps.append(79); reasons.append("1日2ATR以上・1週3ATR以上・大陰線複数のいずれか")
    if five_day >= 4 and high_volume and support_break:
        caps.append(69); reasons.append("5日以内に4ATR以上下落＋大出来高＋支持線割れ")
    if weekly[-1]["close"] < weekly[-1]["open"] and weekly_position <= 0.25 and week_support_break:
        caps.append(59); reasons.append("週足大陰線＋安値圏引け＋週足支持割れ")
    if structure >= 4 and high_volume:
        caps.append(49); reasons.append("安値更新＋戻り高値切り下げ＋大出来高売り継続")
    return (min(caps) if caps else None), reasons


def linear_projection(values):
    if len(values) < 2:
        return values[-1] if values else 0
    x_avg = (len(values) - 1) / 2
    y_avg = avg(values)
    numerator = sum((index - x_avg) * (value - y_avg) for index, value in enumerate(values))
    denominator = sum((index - x_avg) ** 2 for index in range(len(values))) or 1
    coefficient = numerator / denominator
    return y_avg + coefficient * ((len(values) - 1 + 5) - x_avg)


def bubble_state(daily, weekly):
    closes = [bar["close"] for bar in daily]
    current_atr = max(atr(daily, 20), 1e-9)
    recent_speed = max(0, closes[-1] - closes[-6]) / 5
    prior_speed = max(0, closes[-6] - closes[-26]) / 20
    condition1 = prior_speed > 0 and recent_speed >= prior_speed * 2.5
    five_returns = [pct_change(closes[-1 - offset], closes[-6 - offset]) for offset in (10, 5, 0)]
    condition2 = five_returns[0] > 0 and five_returns[0] < five_returns[1] < five_returns[2]
    path = closes[-65:-5] if len(closes) >= 65 else closes[:-5]
    projected = linear_projection(path)
    condition3 = closes[-1] >= projected + current_atr * 3
    weekly_closes = [bar["close"] for bar in weekly]
    recent_week_slope = slope(weekly_closes, 4)
    prior_week_slope = slope(weekly_closes[-16:-4], 12)
    condition4 = prior_week_slope > 0 and recent_week_slope >= prior_week_slope * 2
    checks = [condition1, condition2, condition3, condition4]
    bubble = sum(checks) >= 3
    return bubble, {"checks": checks, "count": sum(checks), "recent5SpeedVsPrior20": recent_speed / max(prior_speed, 1e-9), "trackDeviationAtr": (closes[-1] - projected) / current_atr}


def bubble_collapse_cap(daily, bubble):
    if not bubble:
        return None, None
    current_atr = max(atr(daily, 20), 1e-9)
    shock = max(0, daily[-2]["close"] - daily[-1]["close"]) / current_atr
    bearish = daily[-1]["close"] < daily[-1]["open"] and shock >= 1.25
    if not bearish:
        return None, None
    avg_volume = avg([bar["volume"] for bar in daily[-20:-1] if bar["volume"] > 0])
    high_volume = avg_volume and daily[-1]["volume"] >= avg_volume * 1.5
    support_break = daily[-1]["close"] < min(avg([bar["close"] for bar in daily[-20:]]), min(bar["low"] for bar in daily[-10:-1]))
    structure, _ = structure_points(daily, 10)
    if structure >= 4:
        return 49, "安値更新＋戻り高値切り下げ"
    if support_break:
        return 59, "支持線割れ・反発不足"
    if high_volume:
        return 69, "大陰線＋大出来高"
    return 79, "初回の大陰線"


def healthy_pullback(weekly, weekly_rs):
    width = min(8, len(weekly) // 3)
    if width < 3:
        return False
    higher_low = min(bar["low"] for bar in weekly[-width:]) > min(bar["low"] for bar in weekly[-2 * width : -width])
    ma20 = avg([bar["close"] for bar in weekly[-20:]])
    support = weekly[-1]["close"] >= ma20 * 0.98
    up_volume = [weekly[i]["volume"] for i in range(max(1, len(weekly) - 20), len(weekly)) if weekly[i]["close"] > weekly[i - 1]["close"] and weekly[i]["volume"] > 0]
    down_volume = [weekly[i]["volume"] for i in range(max(1, len(weekly) - 20), len(weekly)) if weekly[i]["close"] <= weekly[i - 1]["close"] and weekly[i]["volume"] > 0]
    quiet_volume = not up_volume or not down_volume or avg(down_volume) < avg(up_volume)
    rs_ok = relative_change(weekly_rs, 13) >= -0.03
    recent_high = max(bar["high"] for bar in weekly[-12:])
    high_index = max(index for index, bar in enumerate(weekly[-12:]) if bar["high"] == recent_high)
    decline_bars = max(1, 11 - high_index)
    decline_speed = max(0, recent_high - weekly[-1]["close"]) / decline_bars
    prior_low = min(bar["low"] for bar in weekly[-24:-12]) if len(weekly) >= 24 else weekly[-12]["low"]
    rise_speed = max(0, recent_high - prior_low) / 12
    slower = decline_speed < max(rise_speed, 1e-9)
    return all([higher_low, support, quiet_volume, rs_ok, slower])


def compact_chart(bars, ma, limit):
    start = max(0, len(bars) - limit)
    output = []
    for index in range(start, len(bars)):
        bar = bars[index]
        output.append([
            bar["date"],
            round(bar["open"], 4),
            round(bar["high"], 4),
            round(bar["low"], 4),
            round(bar["close"], 4),
            int(bar["volume"]),
            round(ma["short"][index], 4) if ma["short"][index] is not None else None,
            round(ma["mid"][index], 4) if ma["mid"][index] is not None else None,
        ])
    return output


def score_asset(
    daily,
    sector_daily,
    market_daily,
    single_level=False,
    historical_high=None,
    include_charts=True,
    monthly_daily=None,
):
    weekly = resample(daily, "week")
    monthly = resample(monthly_daily if monthly_daily is not None else daily, "month")
    sector_weekly = resample(sector_daily, "week")
    market_weekly = resample(market_daily, "week")
    if len(daily) < 200 or len(weekly) < 40:
        return {"pending": True, "reason": f"採点に必要な履歴が不足しています（日足{len(daily)}本・週足{len(weekly)}本）。", "dailyBars": len(daily), "weeklyBars": len(weekly)}
    historical_high = historical_high or max(row["high"] for row in daily)
    day = timeframe_score(daily, sector_daily, market_daily, "daily", single_level=single_level, historical_high=historical_high)
    week = timeframe_score(weekly, sector_weekly, market_weekly, "weekly", single_level=single_level, historical_high=historical_high)
    month_bonus, month_parts, month_cap = monthly_bonus(monthly, day["score"], week["score"], weekly)
    three_penalty, three_change = three_year_penalty(daily)
    long_cap, long_checks = long_decline_cap(daily, weekly, three_change)
    random_cap_day, random_day = random_market(daily)
    random_cap_week, random_week = random_market(weekly, weekly=True)
    random_cap_value = min([cap for cap in (random_cap_day, random_cap_week) if cap is not None], default=None)
    breakdown, breakdown_parts = breakdown_points(daily, weekly, day["stockRsSeries"], day["sectorRsSeries"], week["stockRsSeries"], week["sectorRsSeries"])
    breakdown_penalty, breakdown_cap, breakdown_state = breakdown_adjustment(breakdown)
    bubble, bubble_metrics = bubble_state(daily, weekly)
    bubble_cap, bubble_collapse = bubble_collapse_cap(daily, bubble)
    healthy = healthy_pullback(weekly, week["stockRsSeries"])
    base = day["score"] * 0.5 + week["score"] * 0.5 + month_bonus
    after_penalties = base - three_penalty - breakdown_penalty
    caps = {
        "順張り崩れ上限": breakdown_cap,
        "短期ランダム相場上限": random_cap_value,
        "長期下降上限": long_cap,
        "バブル崩壊上限": bubble_cap,
    }
    applicable = [value for value in caps.values() if value is not None]
    applied_cap = min(applicable) if applicable else 110
    final_score = int(round(max(0, min(after_penalties, applied_cap))))
    if bubble and bubble_cap is None:
        verdict = "強い順張り・ハイリスク"
        warning = "バブル的急騰"
    elif random_cap_value is not None:
        verdict = "中長期順張り・短期乱高下" if week["score"] >= 55 else "日足・週足とも乱高下"
        warning = "短期ランダム相場"
    else:
        verdict = score_band(final_score)
        warning = bubble_collapse
    return {
        "pending": False,
        "score": final_score,
        "verdict": verdict,
        "warning": warning,
        "baseScore": round(base, 1),
        "afterPenalties": round(after_penalties, 1),
        "appliedCap": applied_cap,
        "timeframes": {
            "daily": {"score": day["score"], "components": day["components"], "details": day["details"], "metrics": day["metrics"], **({"chart": compact_chart(daily, day["ma"], 90)} if include_charts else {})},
            "weekly": {"score": week["score"], "components": week["components"], "details": week["details"], "metrics": week["metrics"], **({"chart": compact_chart(weekly, week["ma"], 70)} if include_charts else {})},
        },
        "monthlyBonus": {"score": month_bonus, "rawParts": month_parts, "cap": month_cap},
        "penalties": {
            "threeYear": {"points": three_penalty, "return": three_change},
            "breakdown": {"points": breakdown_penalty, "breakdownPoints": breakdown, "state": breakdown_state, "parts": breakdown_parts},
        },
        "caps": caps,
        "diagnostics": {
            "healthyPullback": healthy,
            "randomDaily": random_day,
            "randomWeekly": random_week,
            "bubble": bubble_metrics,
            "bubbleCollapse": bubble_collapse,
            "longDeclineChecks": long_checks,
        },
    }
