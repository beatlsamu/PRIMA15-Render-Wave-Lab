"""Rule-aware, multi-degree Elliott-wave count for PRIMA-15.

This is a transparent heuristic counter, not a human Elliott analyst. It uses
closed OHLCV candles, an ATR-adaptive ZigZag pivot extractor, hard impulse-rule
checks, and soft Fibonacci/alternation guidelines. Counts remain provisional
when the price structure does not support a clear interpretation.
"""
from __future__ import annotations

from math import isfinite
from statistics import median
from typing import Any

FIB_LEVELS = (0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618, 2.0, 2.618)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _nearest_prime(value: int) -> tuple[int | None, int | None]:
    if value < 2:
        return None, None

    def prime(n: int) -> bool:
        if n < 2:
            return False
        if n % 2 == 0:
            return n == 2
        d = 3
        while d * d <= n:
            if n % d == 0:
                return False
            d += 2
        return True

    low = value
    while low >= 2 and not prime(low):
        low -= 1
    high = value
    while not prime(high):
        high += 1
    candidates = [p for p in (low, high) if p >= 2]
    p = min(candidates, key=lambda x: (abs(x - value), x))
    return p, abs(p - value)


def _median_atr_pct(candles: list[dict[str, Any]], period: int = 14, tail: int = 120) -> float:
    rows = candles[-max(period + 1, tail):]
    values: list[float] = []
    previous = _f(rows[0].get("close")) if rows else 0.0
    for row in rows:
        high, low, close = _f(row.get("high")), _f(row.get("low")), _f(row.get("close"))
        if previous > 0:
            tr = max(high - low, abs(high - previous), abs(low - previous))
            values.append(tr / previous * 100.0)
        previous = close
    return median(values[-tail:]) if values else 0.0


def zigzag_pivots(
    candles: list[dict[str, Any]], *, floor_pct: float, atr_multiplier: float = 1.5,
    max_pivots: int = 80,
) -> dict[str, Any]:
    """Extract alternating pivots with an ATR-adaptive reversal threshold.

    A pivot is timestamped at its extreme but is not considered confirmed until
    a later candle reverses by the required percentage. The current extreme is
    returned separately as provisional and is never used for a completed count.
    """
    if len(candles) < 30:
        return {"pivots": [], "provisional_pivot": None, "threshold_pct": None,
                "status": "INSUFFICIENT_HISTORY", "atr_pct": None}
    atr_pct = _median_atr_pct(candles)
    threshold = max(float(floor_pct), atr_pct * float(atr_multiplier))
    threshold = min(max(threshold, floor_pct), max(floor_pct * 5.0, 5.0))

    first = candles[0]
    start_low = {"index": 0, "time": int(_f(first.get("open_time"))), "price": _f(first.get("low")), "type": "LOW"}
    start_high = {"index": 0, "time": int(_f(first.get("open_time"))), "price": _f(first.get("high")), "type": "HIGH"}
    direction = 0  # +1 rising, -1 falling, 0 not yet established
    extreme = None
    pivots: list[dict[str, Any]] = []

    for i in range(1, len(candles)):
        row = candles[i]
        high, low = _f(row.get("high")), _f(row.get("low"))
        stamp = int(_f(row.get("open_time")))
        if high <= 0 or low <= 0 or high < low:
            continue

        if direction == 0:
            if low < start_low["price"]:
                start_low = {"index": i, "time": stamp, "price": low, "type": "LOW"}
            if high > start_high["price"]:
                start_high = {"index": i, "time": stamp, "price": high, "type": "HIGH"}
            base_low = max(start_low["price"], 1e-12)
            base_high = max(start_high["price"], 1e-12)
            rise = (high / base_low - 1.0) * 100.0
            fall = (1.0 - low / base_high) * 100.0
            if rise >= threshold and rise > fall:
                pivots.append({**start_low, "confirmed": True, "confirmed_at": stamp})
                direction = 1
                extreme = {"index": i, "time": stamp, "price": high, "type": "HIGH"}
            elif fall >= threshold and fall > rise:
                pivots.append({**start_high, "confirmed": True, "confirmed_at": stamp})
                direction = -1
                extreme = {"index": i, "time": stamp, "price": low, "type": "LOW"}
            continue

        if direction == 1:
            new_extreme = extreme is None or high > extreme["price"]
            if new_extreme:
                extreme = {"index": i, "time": stamp, "price": high, "type": "HIGH"}
                continue  # Unknown intrabar ordering: do not confirm on the same candle.
            reversal = (extreme["price"] - low) / max(extreme["price"], 1e-12) * 100.0
            if reversal >= threshold:
                pivots.append({**extreme, "confirmed": True, "confirmed_at": stamp})
                direction = -1
                extreme = {"index": i, "time": stamp, "price": low, "type": "LOW"}
        else:
            new_extreme = extreme is None or low < extreme["price"]
            if new_extreme:
                extreme = {"index": i, "time": stamp, "price": low, "type": "LOW"}
                continue
            reversal = (high - extreme["price"]) / max(extreme["price"], 1e-12) * 100.0
            if reversal >= threshold:
                pivots.append({**extreme, "confirmed": True, "confirmed_at": stamp})
                direction = 1
                extreme = {"index": i, "time": stamp, "price": high, "type": "HIGH"}

    provisional = None
    if extreme and (not pivots or extreme["index"] > pivots[-1]["index"]):
        provisional = {**extreme, "confirmed": False, "status": "PROVISIONAL_NOT_COUNTED"}
    return {
        "pivots": pivots[-max_pivots:],
        "provisional_pivot": provisional,
        "threshold_pct": round(threshold, 5),
        "atr_pct": round(atr_pct, 5),
        "status": "OK" if len(pivots) >= 6 else "INSUFFICIENT_SWINGS",
        "pivot_count_total": len(pivots),
    }


def _fib_fit(value: float, preferred: tuple[float, ...], tolerance: float = 0.45) -> tuple[float, float | None]:
    if value <= 0 or not preferred:
        return 0.0, None
    nearest = min(preferred, key=lambda x: abs(value - x) / x)
    error = abs(value - nearest) / nearest
    return max(0.0, 1.0 - error / tolerance), nearest


def _duration_info(pivots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legs = []
    for index in range(1, len(pivots)):
        left, right = pivots[index - 1], pivots[index]
        duration = max(1, int(right["index"]) - int(left["index"]))
        prime, delta = _nearest_prime(duration)
        start_price, end_price = _f(left["price"]), _f(right["price"])
        change = (end_price / max(start_price, 1e-12) - 1.0) * 100.0
        legs.append({
            "leg": index,
            "from": left["type"], "to": right["type"],
            "start_price": round(start_price, 10), "end_price": round(end_price, 10),
            "change_pct": round(change, 4), "amplitude_pct": round(abs(change), 4),
            "duration_bars": duration,
            "duration_minutes": None,
            "nearest_prime": prime, "prime_delta": delta,
            "prime_match": bool(delta is not None and delta <= 1),
        })
    return legs


def evaluate_impulse_pivots(pivots: list[dict[str, Any]], direction: str) -> dict[str, Any] | None:
    """Evaluate a six-pivot standard impulse sequence using Elliott hard rules."""
    if len(pivots) != 6 or direction not in {"UP", "DOWN"}:
        return None
    expected = ["LOW", "HIGH", "LOW", "HIGH", "LOW", "HIGH"] if direction == "UP" else ["HIGH", "LOW", "HIGH", "LOW", "HIGH", "LOW"]
    if [p.get("type") for p in pivots] != expected:
        return None
    prices = [_f(p.get("price")) for p in pivots]
    if any(value <= 0 for value in prices):
        return None

    if direction == "UP":
        origin, end1, end2, end3, end4, end5 = prices
        w1, w2, w3, w4, w5 = end1 - origin, end1 - end2, end3 - end2, end3 - end4, end5 - end4
        rule_2 = end2 >= origin
        rule_3 = end3 > end1
        rule_4 = end4 > end1
    else:
        origin, end1, end2, end3, end4, end5 = prices
        w1, w2, w3, w4, w5 = origin - end1, end2 - end1, end2 - end3, end4 - end3, end4 - end5
        rule_2 = end2 <= origin
        rule_3 = end3 < end1
        rule_4 = end4 < end1
    positive_legs = all(v > 0 for v in (w1, w2, w3, w4, w5))
    rule_3_not_shortest = w3 >= min(w1, w5) if positive_legs else False
    checks = {
        "wave_2_does_not_exceed_wave_1_origin": bool(rule_2),
        "wave_3_exceeds_wave_1_end": bool(rule_3),
        "wave_3_is_not_shortest": bool(rule_3_not_shortest),
        "wave_4_does_not_overlap_wave_1": bool(rule_4),
        "all_five_legs_have_directional_progress": bool(positive_legs),
    }
    violations = [name for name, passed in checks.items() if not passed]
    valid = not violations
    ratios = {
        "wave2_retracement_of_wave1": w2 / w1 if w1 > 0 else None,
        "wave3_extension_of_wave1": w3 / w1 if w1 > 0 else None,
        "wave4_retracement_of_wave3": w4 / w3 if w3 > 0 else None,
        "wave5_length_vs_wave1": w5 / w1 if w1 > 0 else None,
    }
    fit2, fib2 = _fib_fit(ratios["wave2_retracement_of_wave1"] or 0, (0.382, 0.5, 0.618, 0.786))
    fit3, fib3 = _fib_fit(ratios["wave3_extension_of_wave1"] or 0, (1.0, 1.618, 2.0, 2.618))
    fit4, fib4 = _fib_fit(ratios["wave4_retracement_of_wave3"] or 0, (0.236, 0.382, 0.5))
    fit5, fib5 = _fib_fit(ratios["wave5_length_vs_wave1"] or 0, (0.618, 1.0, 1.618))
    guideline_score = 25.0 * (fit2 + fit3 + fit4 + fit5)
    score = (70.0 + guideline_score * 0.30) if valid else min(49.0, 49.0 - 3.0 * len(violations))
    legs = _duration_info(pivots)
    if valid:
        for i, leg in enumerate(legs):
            leg["label"] = f"WAVE_{i + 1}"
    return {
        "pattern": "STANDARD_IMPULSE_5_WAVES" if valid else "INVALID_STANDARD_IMPULSE_CANDIDATE",
        "direction": direction,
        "trend_direction_signal": direction if valid else None,
        "direction_role": "COMPLETED_IMPULSE_DIRECTION" if valid else "INVALID_CANDIDATE_NO_DIRECTION",
        "valid": valid,
        "provisional": False,
        "score": round(score, 1),
        "hard_rules": checks,
        "violations": violations,
        "guidelines": {
            "wave2_retracement": {"observed": round(ratios["wave2_retracement_of_wave1"] or 0, 4), "nearest_fib": fib2, "fit": round(fit2, 3)},
            "wave3_extension": {"observed": round(ratios["wave3_extension_of_wave1"] or 0, 4), "nearest_fib": fib3, "fit": round(fit3, 3)},
            "wave4_retracement": {"observed": round(ratios["wave4_retracement_of_wave3"] or 0, 4), "nearest_fib": fib4, "fit": round(fit4, 3)},
            "wave5_vs_wave1": {"observed": round(ratios["wave5_length_vs_wave1"] or 0, 4), "nearest_fib": fib5, "fit": round(fit5, 3)},
            "wave2_wave4_depth_difference": round(abs((ratios["wave2_retracement_of_wave1"] or 0) - (ratios["wave4_retracement_of_wave3"] or 0)), 4),
            "alternation": "DEPTH_DIFFERENCE_ONLY; SHARP_VS_SIDEWAYS_FORM_NEEDS_LOWER_DEGREE_SUBWAVES",
        },
        "ratios": {key: round(value, 4) if value is not None else None for key, value in ratios.items()},
        "legs": legs,
        "pivots": [dict(p) for p in pivots],
        "start_time": int(pivots[0].get("time", 0)),
        "end_time": int(pivots[-1].get("time", 0)),
        "end_index": int(pivots[-1].get("index", 0)),
        "invalidation_price": round(prices[0], 10),
        "next_expected": "ABC_CORRECTION" if valid else "RECOUNT_REQUIRED",
        "interpretation": (
            "Las cinco ondas cumplen las reglas estructurales del impulso estándar. Las relaciones Fibonacci son guías, no reglas."
            if valid else "El candidato viola reglas duras del impulso estándar; no debe etiquetarse como impulso válido."
        ),
    }


def _partial_impulse(pivots: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(pivots) >= 5:
        seq = pivots[-5:]
        types = [p["type"] for p in seq]
        if types == ["LOW", "HIGH", "LOW", "HIGH", "LOW"]:
            origin, one, two, three, four = [_f(p["price"]) for p in seq]
            if two > origin and three > one and four > one:
                retrace2 = (one - two) / max(one - origin, 1e-12)
                retrace4 = (three - four) / max(three - two, 1e-12)
                return {"pattern": "BULLISH_IMPULSE_WAVE_4_COMPLETE", "direction": "UP", "trend_direction_signal": "UP", "direction_role": "PROVISIONAL_IMPULSE_DIRECTION", "valid": True,
                        "provisional": True, "score": 65.0, "stage": "WAVE_4_COMPLETE_WAITING_WAVE_5",
                        "hard_rules": {"wave2_origin": True, "wave3_extension": True, "wave4_no_overlap": True},
                        "ratios": {"wave2_retracement": round(retrace2, 4), "wave4_retracement": round(retrace4, 4)},
                        "next_expected": "WAVE_5", "invalidation_price": origin,
                        "pivots": [dict(p) for p in seq], "legs": _duration_info(seq),
                        "interpretation": "Conteo provisional 1-2-3-4; falta confirmar el quinto tramo."}
        if types == ["HIGH", "LOW", "HIGH", "LOW", "HIGH"]:
            origin, one, two, three, four = [_f(p["price"]) for p in seq]
            if two < origin and three < one and four < one:
                retrace2 = (two - one) / max(origin - one, 1e-12)
                retrace4 = (four - three) / max(two - three, 1e-12)
                return {"pattern": "BEARISH_IMPULSE_WAVE_4_COMPLETE", "direction": "DOWN", "trend_direction_signal": "DOWN", "direction_role": "PROVISIONAL_IMPULSE_DIRECTION", "valid": True,
                        "provisional": True, "score": 65.0, "stage": "WAVE_4_COMPLETE_WAITING_WAVE_5",
                        "hard_rules": {"wave2_origin": True, "wave3_extension": True, "wave4_no_overlap": True},
                        "ratios": {"wave2_retracement": round(retrace2, 4), "wave4_retracement": round(retrace4, 4)},
                        "next_expected": "WAVE_5", "invalidation_price": origin,
                        "pivots": [dict(p) for p in seq], "legs": _duration_info(seq),
                        "interpretation": "Conteo provisional 1-2-3-4; falta confirmar el quinto tramo."}
    if len(pivots) >= 4:
        seq = pivots[-4:]
        types = [p["type"] for p in seq]
        if types == ["LOW", "HIGH", "LOW", "HIGH"]:
            origin, one, two, three = [_f(p["price"]) for p in seq]
            if two > origin and three > one:
                return {"pattern": "BULLISH_IMPULSE_WAVE_3_COMPLETE", "direction": "UP", "trend_direction_signal": "UP", "direction_role": "PROVISIONAL_IMPULSE_DIRECTION", "valid": True,
                        "provisional": True, "score": 58.0, "stage": "WAVE_3_COMPLETE_WAITING_WAVE_4",
                        "hard_rules": {"wave2_origin": True, "wave3_extension": True}, "next_expected": "WAVE_4", 
                        "invalidation_price": origin, "pivots": [dict(p) for p in seq], "legs": _duration_info(seq),
                        "interpretation": "Conteo provisional 1-2-3; falta una corrección 4 y posterior validación de 5."}
        if types == ["HIGH", "LOW", "HIGH", "LOW"]:
            origin, one, two, three = [_f(p["price"]) for p in seq]
            if two < origin and three < one:
                return {"pattern": "BEARISH_IMPULSE_WAVE_3_COMPLETE", "direction": "DOWN", "trend_direction_signal": "DOWN", "direction_role": "PROVISIONAL_IMPULSE_DIRECTION", "valid": True,
                        "provisional": True, "score": 58.0, "stage": "WAVE_3_COMPLETE_WAITING_WAVE_4",
                        "hard_rules": {"wave2_origin": True, "wave3_extension": True}, "next_expected": "WAVE_4",
                        "invalidation_price": origin, "pivots": [dict(p) for p in seq], "legs": _duration_info(seq),
                        "interpretation": "Conteo provisional 1-2-3; falta una corrección 4 y posterior validación de 5."}
    return None


def _abc_candidate(pivots: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(pivots) < 4:
        return None
    seq = pivots[-4:]
    types = [p["type"] for p in seq]
    prices = [_f(p["price"]) for p in seq]
    if types == ["HIGH", "LOW", "HIGH", "LOW"]:
        a, b, c = prices[0] - prices[1], prices[2] - prices[1], prices[2] - prices[3]
        parent_direction = "UP"
        terminal_rule = prices[3] < prices[1]
        retrace_b = b / max(a, 1e-12)
    elif types == ["LOW", "HIGH", "LOW", "HIGH"]:
        a, b, c = prices[1] - prices[0], prices[1] - prices[2], prices[3] - prices[2]
        parent_direction = "DOWN"
        terminal_rule = prices[3] > prices[1]
        retrace_b = b / max(a, 1e-12)
    else:
        return None
    if min(a, b, c) <= 0:
        return None
    c_vs_a = c / a
    # The 0.9 boundary is a practical morphology heuristic to distinguish zigzag-like from flat-like.
    form = "ABC_ZIGZAG_LIKE" if retrace_b < 0.9 and terminal_rule else "ABC_FLAT_LIKE" if retrace_b >= 0.9 else "ABC_UNCLASSIFIED"
    score = 55.0
    fib_fit, fib = _fib_fit(c_vs_a, (0.618, 1.0, 1.618))
    score += fib_fit * 20.0
    score += 15.0 if terminal_rule else 0.0
    score += 10.0 if 0.2 <= retrace_b <= 1.4 else 0.0
    return {
        "pattern": form, "direction": parent_direction, "correction_direction": "DOWN" if parent_direction == "UP" else "UP",
        "trend_direction_signal": parent_direction if terminal_rule else None,
        "direction_role": "POSSIBLE_PARENT_TREND_RESUMPTION" if terminal_rule else "CORRECTION_NOT_TERMINAL",
        "valid": bool(terminal_rule), "provisional": True, "score": round(min(95.0, score), 1),
        "stage": "ABC_OUTER_SWINGS_CANDIDATE", "next_expected": "POSSIBLE_PARENT_TREND_RESUMPTION" if terminal_rule else "CORRECTION_MAY_CONTINUE",
        "ratios": {"B_retracement_of_A": round(retrace_b, 4), "C_length_vs_A": round(c_vs_a, 4), "nearest_fib_C_A": fib},
        "subdivision_status": "NOT_VERIFIABLE_FROM_OUTER_SWING_PIVOTS; 5-3-5 OR 3-3-5 NEEDS LOWER-DEGREE CONFIRMATION",
        "pivots": [dict(p) for p in seq], "legs": _duration_info(seq),
        "interpretation": "ABC es una hipótesis de tres tramos; la clasificación zigzag/plano es morfológica y provisional, no una validación completa de subondas.",
    }


def _triangle_candidate(pivots: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(pivots) < 5:
        return None
    seq = pivots[-5:]
    types = [p["type"] for p in seq]
    prices = [_f(p["price"]) for p in seq]
    if types == ["HIGH", "LOW", "HIGH", "LOW", "HIGH"]:
        highs, lows = [prices[0], prices[2], prices[4]], [prices[1], prices[3]]
    elif types == ["LOW", "HIGH", "LOW", "HIGH", "LOW"]:
        highs, lows = [prices[1], prices[3]], [prices[0], prices[2], prices[4]]
    else:
        return None
    contracting = all(highs[i + 1] < highs[i] for i in range(len(highs) - 1)) and all(lows[i + 1] > lows[i] for i in range(len(lows) - 1))
    widths = [max(0.0, highs[i] - lows[i]) for i in range(min(len(highs), len(lows)))]
    narrowing = len(widths) >= 2 and widths[-1] < widths[0]
    if not (contracting and narrowing):
        return None
    return {
        "pattern": "POSSIBLE_CONTRACTING_TRIANGLE", "direction": "RANGE", "trend_direction_signal": None, "direction_role": "NEUTRAL_PENDING_BREAKOUT", "valid": False,
        "provisional": True, "score": 62.0, "stage": "A_B_C_D_E_OUTER_SWINGS_CANDIDATE",
        "subdivision_status": "3-3-3-3-3_NOT_VERIFIABLE_FROM_OUTER_SWINGS",
        "pivots": [dict(p) for p in seq], "legs": _duration_info(seq),
        "next_expected": "BREAKOUT_DIRECTION_NOT_YET_CONFIRMED",
        "interpretation": "Los extremos convergen; es una posible contracción triangular, no una señal direccional antes de ruptura confirmada.",
    }


def _diagonal_candidate(pivots: list[dict[str, Any]], direction: str) -> dict[str, Any] | None:
    if len(pivots) < 6:
        return None
    seq = pivots[-6:]
    types = [p["type"] for p in seq]
    expected = ["LOW", "HIGH", "LOW", "HIGH", "LOW", "HIGH"] if direction == "UP" else ["HIGH", "LOW", "HIGH", "LOW", "HIGH", "LOW"]
    if types != expected:
        return None
    p = [_f(row["price"]) for row in seq]
    if direction == "UP":
        highs, lows = [p[1], p[3], p[5]], [p[0], p[2], p[4]]
        action = [highs[0] - lows[0], highs[1] - lows[1], highs[2] - lows[2]]
        overlap = lows[2] <= highs[0]
    else:
        highs, lows = [p[0], p[2], p[4]], [p[1], p[3], p[5]]
        action = [highs[0] - lows[0], highs[1] - lows[1], highs[2] - lows[2]]
        overlap = highs[2] >= lows[0]
    ordered = (highs[0] < highs[1] < highs[2] and lows[0] < lows[1] < lows[2]) if direction == "UP" else (highs[0] > highs[1] > highs[2] and lows[0] > lows[1] > lows[2])
    widths = [h - l for h, l in zip(highs, lows)]
    wedge = ordered and widths[1] < widths[0] and widths[2] < widths[1]
    wave3_not_shortest = action[1] >= min(action[0], action[2])
    if not (overlap and wedge and wave3_not_shortest):
        return None
    return {
        "pattern": "POSSIBLE_DIAGONAL", "direction": direction, "trend_direction_signal": None,
        "direction_role": "POSSIBLE_TERMINAL_DIAGONAL_REVERSAL_RISK", "valid": False,
        "provisional": True, "score": 60.0, "stage": "FIVE_SWING_OVERLAPPING_WEDGE",
        "hard_rules": {"wave4_overlap_present": True, "wave3_not_shortest": wave3_not_shortest, "wedge_converging": wedge},
        "pivots": [dict(pv) for pv in seq], "legs": _duration_info(seq),
        "next_expected": "REVERSAL_WATCH_IF_ENDING_DIAGONAL_CONTEXT",
        "interpretation": "El solapamiento impide llamarlo impulso estándar; la cuña puede ser diagonal, pero la estructura interna 3-3-3-3-3 no se valida aquí.",
    }


def _select_count(pivots: list[dict[str, Any]]) -> dict[str, Any]:
    valid_impulses: list[dict[str, Any]] = []
    for end in range(6, len(pivots) + 1):
        seq = pivots[end - 6:end]
        for direction in ("UP", "DOWN"):
            candidate = evaluate_impulse_pivots(seq, direction)
            if candidate and candidate["valid"]:
                valid_impulses.append(candidate)
    latest_impulse = None
    if valid_impulses:
        valid_impulses.sort(key=lambda c: (c["end_time"], c["score"]), reverse=True)
        latest_impulse = valid_impulses[0]

    alternatives = [
        candidate for candidate in (
            _abc_candidate(pivots), _triangle_candidate(pivots), _partial_impulse(pivots),
            _diagonal_candidate(pivots, "UP"), _diagonal_candidate(pivots, "DOWN"),
        ) if candidate
    ]
    # Choose the candidate whose last confirmed swing is newest.  The old
    # implementation returned alternatives[0] when there was no completed
    # impulse, which could display a stale ABC candidate over a newer 1-2-3.
    latest_alternative = max(
        alternatives,
        key=lambda c: (
            int((c.get("pivots") or [{}])[-1].get("index", 0)),
            float(c.get("score", 0.0)),
        ),
    ) if alternatives else None
    if latest_impulse and latest_alternative:
        alternative_end = int((latest_alternative.get("pivots") or [{}])[-1].get("index", 0))
        if alternative_end > int(latest_impulse.get("end_index", 0)):
            if latest_alternative.get("pattern", "").startswith("ABC_"):
                latest_alternative["parent_impulse"] = {
                    "direction": latest_impulse.get("direction"),
                    "score": latest_impulse.get("score"),
                    "end_time": latest_impulse.get("end_time"),
                }
            latest_alternative["alternative_count"] = {
                "pattern": latest_impulse.get("pattern"),
                "direction": latest_impulse.get("direction"),
                "score": latest_impulse.get("score"),
                "end_time": latest_impulse.get("end_time"),
            }
            return latest_alternative
        return latest_impulse
    if latest_impulse:
        return latest_impulse
    if latest_alternative:
        return latest_alternative
    return {
        "pattern": "NO_CLEAR_ELLIOTT_COUNT", "direction": "RANGE", "trend_direction_signal": None,
        "direction_role": "UNDETERMINED", "valid": False,
        "provisional": True, "score": 0.0, "stage": "COUNT_AMBIGUOUS",
        "next_expected": "WAIT_FOR_MORE_CONFIRMED_SWINGS",
        "pivots": [dict(p) for p in pivots[-8:]], "legs": _duration_info(pivots[-8:]),
        "interpretation": "Los pivotes disponibles no forman una estructura suficientemente clara. No se fuerza un conteo.",
    }


def _timeframe_result(
    name: str, interval_minutes: int, candles: list[dict[str, Any]], floor_pct: float, atr_multiplier: float,
) -> dict[str, Any]:
    zigzag = zigzag_pivots(candles, floor_pct=floor_pct, atr_multiplier=atr_multiplier)
    pivots = zigzag["pivots"]
    count = _select_count(pivots) if len(pivots) >= 4 else {
        "pattern": "NO_CLEAR_ELLIOTT_COUNT", "direction": "RANGE", "trend_direction_signal": None,
        "direction_role": "UNDETERMINED", "valid": False,
        "provisional": True, "score": 0.0, "stage": "INSUFFICIENT_SWINGS", "pivots": [], "legs": [],
        "interpretation": "Faltan extremos confirmados para construir un conteo Elliott."}
    legs = count.get("legs", []) or []
    for leg in legs:
        leg["duration_minutes"] = int(leg["duration_bars"]) * interval_minutes
    prime_hits = sum(1 for leg in legs if leg.get("prime_match"))
    prime_fit = (prime_hits / len(legs) * 100.0) if legs else 0.0
    count["prime_time_overlay"] = {
        "matched_legs": prime_hits, "total_legs": len(legs), "fit_pct": round(prime_fit, 1),
        "interpretation": "La duración de los tramos se acerca a números primos de velas." if prime_fit >= 60 else "La duración no muestra una coincidencia prima dominante.",
    }
    return {
        "timeframe": name, "interval_minutes": interval_minutes,
        "candle_count": len(candles), "history_days": round(len(candles) * interval_minutes / 1440.0, 1),
        "zigzag_threshold_pct": zigzag.get("threshold_pct"), "atr_median_pct": zigzag.get("atr_pct"),
        "pivot_count_total": zigzag.get("pivot_count_total", 0),
        "confirmed_pivots": pivots[-24:],
        "provisional_pivot": zigzag.get("provisional_pivot"),
        "count": count,
    }


def aggregate_candles(candles: list[dict[str, Any]], factor: int, source_minutes: int = 15) -> list[dict[str, Any]]:
    """Aggregate only complete, time-aligned groups; omit gaps and partial groups."""
    if factor <= 1:
        return [dict(c) for c in candles]
    bucket_ms = factor * source_minutes * 60_000
    source_ms = source_minutes * 60_000
    grouped: dict[int, list[dict[str, Any]]] = {}
    for candle in candles:
        stamp = int(_f(candle.get("open_time")))
        grouped.setdefault(stamp // bucket_ms, []).append(candle)
    output = []
    for bucket, rows in sorted(grouped.items()):
        rows.sort(key=lambda row: int(_f(row.get("open_time"))))
        if len(rows) != factor:
            continue
        stamps = [int(_f(row.get("open_time"))) for row in rows]
        if stamps != [stamps[0] + i * source_ms for i in range(factor)]:
            continue
        output.append({
            "open_time": stamps[0], "close_time": int(_f(rows[-1].get("close_time"))),
            "open": _f(rows[0].get("open")), "high": max(_f(row.get("high")) for row in rows),
            "low": min(_f(row.get("low")) for row in rows), "close": _f(rows[-1].get("close")),
            "volume": sum(_f(row.get("volume")) for row in rows),
            "quote_volume": sum(_f(row.get("quote_volume")) for row in rows),
            "trades": sum(int(_f(row.get("trades"))) for row in rows),
            "aggregated_from": factor,
        })
    return output


def _prime_elliott_relation(timeframes: dict[str, Any], prime_engine_analysis: dict[str, Any] | None) -> dict[str, Any]:
    main = timeframes.get("15m", {})
    count = (main.get("count") or {})
    pattern = str(count.get("pattern", "NO_CLEAR_ELLIOTT_COUNT"))
    e_dir = count.get("direction", "RANGE")
    trend_signal = count.get("trend_direction_signal")
    directional_count = trend_signal in {"UP", "DOWN"} and bool(count.get("valid") or count.get("provisional"))
    valid_impulse = pattern == "STANDARD_IMPULSE_5_WAVES" and bool(count.get("valid"))
    fit = float((count.get("prime_time_overlay") or {}).get("fit_pct", 0.0) or 0.0)
    prime_signal = str((prime_engine_analysis or {}).get("signal", "NEUTRAL")).upper()
    p_dir = "UP" if prime_signal == "LONG" else "DOWN" if prime_signal == "SHORT" else "RANGE"
    same_direction = directional_count and p_dir != "RANGE" and trend_signal == p_dir
    if valid_impulse and same_direction and fit >= 60.0:
        state = "ELLIOTT_IMPULSE_AND_PRIME_CONFLUENCE"
    elif same_direction and fit >= 60.0:
        state = "ELLIOTT_PROVISIONAL_AND_PRIME_CONFLUENCE"
    elif directional_count and p_dir != "RANGE" and trend_signal != p_dir:
        state = "DIRECTIONAL_CONFLICT"
    elif directional_count and fit >= 60.0:
        state = "ELLIOTT_PRIME_TIME_FIT_ONLY"
    elif directional_count:
        state = "ELLIOTT_COUNT_WITHOUT_STRONG_PRIME_FIT"
    else:
        state = "NO_VALID_PRIMARY_ELLIOTT_COUNT"
    return {
        "state": state,
        "elliott_pattern": pattern,
        "elliott_direction": e_dir,
        "elliott_trend_signal": trend_signal,
        "elliott_direction_role": count.get("direction_role", "UNDETERMINED"),
        "elliott_directional_candidate": bool(directional_count),
        "elliott_valid_impulse": bool(valid_impulse),
        "elliott_provisional": bool(count.get("provisional")),
        "prime_engine_signal": prime_signal,
        "prime_engine_direction": p_dir,
        "elliott_leg_prime_fit_pct": round(fit, 1),
        "confluence_is_causal": False,
        "conclusion": {
            "ELLIOTT_IMPULSE_AND_PRIME_CONFLUENCE": "Un impulso Elliott estándar completo y válido coincide en dirección con el motor prima; al menos 60% de sus tramos se acercan a duraciones primas. Es confluencia descriptiva, no probabilidad de éxito.",
            "ELLIOTT_PROVISIONAL_AND_PRIME_CONFLUENCE": "Un conteo Elliott direccional provisional coincide con el motor prima y presenta ajuste temporal. No equivale a un impulso completo confirmado.",
            "DIRECTIONAL_CONFLICT": "El conteo Elliott y la señal direccional prima apuntan en direcciones distintas. Mantener ambas hipótesis visibles; no promediar el conflicto.",
            "ELLIOTT_PRIME_TIME_FIT_ONLY": "El conteo Elliott tiene ajuste temporal prima, pero el motor prima no aporta una dirección confirmada.",
            "ELLIOTT_COUNT_WITHOUT_STRONG_PRIME_FIT": "Hay una estructura Elliott direccional, pero sus tiempos no muestran confluencia prima suficiente.",
            "NO_VALID_PRIMARY_ELLIOTT_COUNT": "No hay un conteo Elliott direccional suficientemente definido en 15m; no forzar una etiqueta.",
        }.get(state, "Lectura mixta."),
    }


def analyze_multi_degree_elliott(
    candles_15m: list[dict[str, Any]], candles_1m: list[dict[str, Any]] | None = None,
    prime_engine_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run independent 1m/15m/1h/4h Elliott counts from closed candles."""
    m15 = [dict(c) for c in candles_15m if _f(c.get("close_time")) > 0]
    m1 = [dict(c) for c in (candles_1m or []) if _f(c.get("close_time")) > 0]
    h1 = aggregate_candles(m15, 4, 15)
    h4 = aggregate_candles(m15, 16, 15)
    frames = {
        "1m": _timeframe_result("1m", 1, m1[-3000:], 0.10, 1.75),
        "15m": _timeframe_result("15m", 15, m15[-5000:], 0.38, 1.45),
        "1h": _timeframe_result("1h", 60, h1[-1300:], 0.65, 1.45),
        "4h": _timeframe_result("4h", 240, h4[-400:], 1.00, 1.35),
    }
    directional = []
    for name, weight in (("15m", 1.0), ("1h", 1.2), ("4h", 1.25), ("1m", 0.45)):
        count = frames[name].get("count", {})
        direction = count.get("trend_direction_signal")
        if direction in {"UP", "DOWN"} and (count.get("valid") or count.get("provisional")):
            directional.append({"timeframe": name, "direction": direction, "direction_role": count.get("direction_role"),
                                "score": float(count.get("score", 0)), "weight": weight})
    up_weight = sum(row["weight"] * row["score"] / 100.0 for row in directional if row["direction"] == "UP")
    down_weight = sum(row["weight"] * row["score"] / 100.0 for row in directional if row["direction"] == "DOWN")
    if up_weight > down_weight * 1.15 and sum(r["direction"] == "UP" for r in directional) >= 2:
        multi_bias = "UP"
    elif down_weight > up_weight * 1.15 and sum(r["direction"] == "DOWN" for r in directional) >= 2:
        multi_bias = "DOWN"
    elif directional:
        multi_bias = "MIXED"
    else:
        multi_bias = "UNDETERMINED"
    prime_relation = _prime_elliott_relation(frames, prime_engine_analysis)
    total_15m = len(m15)
    return {
        "method": "ATR_ADAPTIVE_ZIGZAG_PLUS_RULE_BASED_ELLIOTT",
        "version": "elliott-1.1",
        "uses_closed_candles_only": True,
        "history": {
            "15m_candles_processed": total_15m,
            "15m_days": round(total_15m * 15 / 1440.0, 1),
            "requested_15m_history_candles": 5000,
            "history_target_days": round(5000 * 15 / 1440.0, 1),
            "1m_candles_processed": len(m1[-3000:]),
            "1h_candles_aggregated": len(h1),
            "4h_candles_aggregated": len(h4),
        },
        "timeframes": frames,
        "multi_degree_synthesis": {
            "direction": multi_bias, "up_weight": round(up_weight, 3), "down_weight": round(down_weight, 3),
            "directional_counts": directional,
            "conclusion": (
                "Hay apoyo multi-grado para una estructura alcista." if multi_bias == "UP" else
                "Hay apoyo multi-grado para una estructura bajista." if multi_bias == "DOWN" else
                "Los grados se contradicen o la evidencia es insuficiente; no imponer una dirección única."
            ),
        },
        "prime_elliott_relation": prime_relation,
        "limitations": [
            "Los conteos Elliott son hipótesis dependientes del grado y del umbral de ZigZag; se muestran estados provisionales y no se fuerza una numeración cuando hay ambigüedad.",
            "La regla de alternancia no se certifica solo con extremos exteriores: se informa la diferencia de profundidad 2/4, pero la forma sharp/sideways exige verificar subondas de menor grado.",
            "Las subestructuras 5-3-5-3-5, 5-3-5, 3-3-5 y 3-3-3-3-3 no pueden verificarse por completo con pivotes exteriores solamente.",
            "Las proporciones Fibonacci y la alternancia son guías, no reglas duras.",
            "Los pivotes quedan confirmados solo después de una reversión ATR; la marca temporal del extremo es anterior a su confirmación.",
            "El conteo no constituye una probabilidad calibrada ni una señal de trading independiente.",
        ],
    }
