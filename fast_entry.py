"""Fast-entry quality model for PRIMA-15.

Uses closed 1-minute OHLCV bars, live order-book depth and recent taker flow.
Historical labels are strictly forward-looking only for training: at each past
minute, they ask whether a TP level was reached before an adverse path barrier.
The barrier is an evaluation label, NOT an automatically executed stop-loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import exp, log, sqrt, tanh
from statistics import median
from typing import Any

FEATURE_NAMES = (
    "roc1_norm", "roc3_norm", "roc5_norm", "roc10_norm",
    "ema_gap_norm", "ema_slope_norm", "vwap_gap_norm", "rsi_centered",
    "volume_log_ratio", "efficiency_signed", "range_location_signed",
    "volatility_ratio_norm",
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if value == value and abs(value) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    output = [result]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
        output.append(result)
    return output


def _rsi(values: list[float], period: int = 7) -> float:
    if len(values) < period + 1:
        return 50.0
    changes = [values[i] - values[i - 1] for i in range(len(values) - period, len(values))]
    gains = sum(max(0.0, item) for item in changes) / period
    losses = sum(max(0.0, -item) for item in changes) / period
    if losses <= 1e-15:
        return 100.0 if gains > 1e-15 else 50.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


def _atr_pct(candles: list[dict[str, Any]], period: int = 14, price: float | None = None) -> float:
    if len(candles) < 2:
        return 0.0
    true_ranges: list[float] = []
    for index in range(max(1, len(candles) - period), len(candles)):
        item = candles[index]
        previous_close = _f(candles[index - 1].get("close"))
        high, low = _f(item.get("high")), _f(item.get("low"))
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    mark = price or _f(candles[-1].get("close"))
    return (sum(true_ranges) / len(true_ranges) / mark * 100.0) if mark > 0 and true_ranges else 0.0


def _features(candles: list[dict[str, Any]], price: float | None = None) -> dict[str, float] | None:
    if len(candles) < 35:
        return None
    closes = [_f(c.get("close")) for c in candles if _f(c.get("close")) > 0]
    if len(closes) < 35:
        return None
    mark = price if price and price > 0 else closes[-1]
    series = closes + ([mark] if abs(mark - closes[-1]) / mark > 1e-10 else [])
    returns: dict[str, float] = {}
    for horizon in (1, 3, 5, 10):
        reference = closes[-(horizon + 1)] if len(closes) > horizon else closes[0]
        returns[f"roc_{horizon}m_pct"] = (mark / reference - 1.0) * 100.0 if reference > 0 else 0.0

    ema9_values = _ema_series(series[-60:], 9)
    ema21_values = _ema_series(series[-80:], 21)
    ema9 = ema9_values[-1]
    ema21 = ema21_values[-1]
    ema9_previous = ema9_values[-4] if len(ema9_values) >= 4 else ema9_values[0]
    ema_gap_pct = (ema9 / ema21 - 1.0) * 100.0 if ema21 > 0 else 0.0
    ema_slope_pct = (ema9 / ema9_previous - 1.0) * 100.0 if ema9_previous > 0 else 0.0

    recent = candles[-20:]
    total_volume = sum(max(0.0, _f(c.get("volume"))) for c in recent)
    typical_volume = [max(0.0, _f(c.get("volume"))) for c in candles[-30:-5]]
    vol_median = median(typical_volume) if typical_volume else 0.0
    recent_volume = sum(max(0.0, _f(c.get("volume"))) for c in candles[-3:]) / 3.0
    volume_ratio = recent_volume / vol_median if vol_median > 1e-15 else 1.0

    vwap_num = 0.0
    vwap_den = 0.0
    for c in recent:
        typical = (_f(c.get("high")) + _f(c.get("low")) + _f(c.get("close"))) / 3.0
        volume = max(0.0, _f(c.get("volume")))
        vwap_num += typical * volume
        vwap_den += volume
    vwap = vwap_num / vwap_den if vwap_den > 0 else mark
    vwap_gap_pct = (mark / vwap - 1.0) * 100.0 if vwap > 0 else 0.0

    last10 = closes[-11:]
    path_length = sum(abs(last10[i] - last10[i - 1]) for i in range(1, len(last10)))
    efficiency = abs(mark - last10[0]) / path_length if path_length > 1e-15 else 0.0
    range_rows = candles[-30:]
    range_low = min(_f(c.get("low")) for c in range_rows)
    range_high = max(_f(c.get("high")) for c in range_rows)
    range_location = (mark - range_low) / (range_high - range_low) if range_high > range_low else 0.5
    atr7 = _atr_pct(candles, 7, mark)
    atr21 = _atr_pct(candles, 21, mark)
    volatility_ratio = atr7 / atr21 if atr21 > 1e-12 else 1.0
    prior_roc3 = (closes[-1] / closes[-4] - 1.0) * 100.0 if len(closes) >= 4 else 0.0
    acceleration = returns["roc_3m_pct"] - prior_roc3

    result = {
        **returns,
        "ema9": ema9, "ema21": ema21,
        "ema_gap_pct": ema_gap_pct,
        "ema_slope_pct": ema_slope_pct,
        "rsi7": _rsi(series[-20:], 7),
        "vwap20": vwap,
        "vwap_gap_pct": vwap_gap_pct,
        "volume_ratio": volume_ratio,
        "volume_20_total": total_volume,
        "efficiency_ratio": _clamp(efficiency, 0.0, 1.0),
        "range_location": _clamp(range_location, 0.0, 1.0),
        "atr1m_pct": atr7,
        "volatility_ratio": volatility_ratio,
        "acceleration_3m_pct": acceleration,
        "mark_price": mark,
    }
    return result


def _directional_vector(features: dict[str, float], side: str) -> list[float]:
    sign = 1.0 if side == "LONG" else -1.0
    atr = max(features.get("atr1m_pct", 0.0), 0.025)
    values = [
        _clamp(features.get("roc_1m_pct", 0.0) / atr, -3.0, 3.0) / 1.5,
        _clamp(features.get("roc_3m_pct", 0.0) / atr, -4.0, 4.0) / 2.0,
        _clamp(features.get("roc_5m_pct", 0.0) / atr, -5.0, 5.0) / 2.5,
        _clamp(features.get("roc_10m_pct", 0.0) / atr, -6.0, 6.0) / 3.0,
        _clamp(features.get("ema_gap_pct", 0.0) / atr, -4.0, 4.0) / 2.0,
        _clamp(features.get("ema_slope_pct", 0.0) / atr, -3.0, 3.0) / 1.5,
        _clamp(features.get("vwap_gap_pct", 0.0) / atr, -4.0, 4.0) / 2.0,
        _clamp((features.get("rsi7", 50.0) - 50.0) / 25.0, -2.0, 2.0) / 2.0,
        _clamp(log(max(0.10, features.get("volume_ratio", 1.0))), -2.0, 2.0) / 2.0,
        _clamp(features.get("efficiency_ratio", 0.0) * 2.0 - 1.0, -1.0, 1.0),
        _clamp(features.get("range_location", 0.5) * 2.0 - 1.0, -1.0, 1.0) * sign,
        _clamp(features.get("volatility_ratio", 1.0) - 1.0, -1.0, 2.0) / 1.5,
    ]
    # Directional price/oscillator features point in the intended side's direction.
    for idx in range(8):
        values[idx] *= sign
    return values


def _distance(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 999.0
    weights = [1.1, 1.2, 1.2, 1.0, 1.2, 0.9, 1.0, 0.7, 0.45, 0.55, 0.65, 0.5]
    total_weight = sum(weights)
    return sqrt(sum(w * (x - y) ** 2 for x, y, w in zip(a, b, weights)) / total_weight)


def _label_future(
    entry: float, future: list[dict[str, Any]], side: str,
    target_pct: float, adverse_pct: float,
) -> tuple[bool, int | None, str]:
    target = target_pct / 100.0
    adverse = adverse_pct / 100.0
    for offset, candle in enumerate(future, start=1):
        high = _f(candle.get("high"))
        low = _f(candle.get("low"))
        if side == "LONG":
            target_hit = high >= entry * (1.0 + target)
            adverse_hit = low <= entry * (1.0 - adverse)
        else:
            target_hit = low <= entry * (1.0 - target)
            adverse_hit = high >= entry * (1.0 + adverse)
        # Conservative ordering: if both barriers are crossed in the same 1m bar,
        # count it as adverse-first because OHLC does not reveal intrabar ordering.
        if target_hit and adverse_hit:
            return False, None, "BOTH_SAME_BAR_CONSERVATIVE_LOSS"
        if adverse_hit:
            return False, None, "ADVERSE_FIRST"
        if target_hit:
            return True, offset, "TP_FIRST"
    return False, None, "NO_TOUCH_WITHIN_HORIZON"


def _build_reference_samples(
    candles: list[dict[str, Any]], target_pct: float, adverse_pct: float,
    horizon_minutes: int, max_samples: int = 2400,
) -> list[dict[str, Any]]:
    first = max(40, len(candles) - max_samples - horizon_minutes)
    last = len(candles) - horizon_minutes
    rows: list[dict[str, Any]] = []
    for idx in range(first, last):
        # Every feature only needs the last 80 candles. Keeping a 100-bar
        # lookback avoids rebuilding an ever-growing prefix for each sample.
        window = candles[max(0, idx - 100):idx + 1]
        entry = _f(window[-1].get("close"))
        if entry <= 0:
            continue
        features = _features(window, entry)
        if not features:
            continue
        vector_long = _directional_vector(features, "LONG")
        vector_short = _directional_vector(features, "SHORT")
        future = candles[idx + 1:idx + 1 + horizon_minutes]
        long_hit, long_bars, long_label = _label_future(entry, future, "LONG", target_pct, adverse_pct)
        short_hit, short_bars, short_label = _label_future(entry, future, "SHORT", target_pct, adverse_pct)
        rows.append({
            "time_ms": int(_f(window[-1].get("open_time"))),
            "long_vector": vector_long, "short_vector": vector_short,
            "long_hit": long_hit, "long_bars": long_bars, "long_label": long_label,
            "short_hit": short_hit, "short_bars": short_bars, "short_label": short_label,
        })
    return rows


def _wilson_lower(wins: int, n: int, z: float = 1.28) -> float:
    if n <= 0:
        return 0.0
    p = wins / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * sqrt((p * (1.0 - p) / n) + z2 / (4.0 * n * n))
    return max(0.0, (centre - margin) / denominator)


def _neighbor_stats(
    vector: list[float], reference: list[dict[str, Any]], side: str, k: int = 50,
    min_separation_minutes: int = 20,
) -> dict[str, Any]:
    vkey = "long_vector" if side == "LONG" else "short_vector"
    hit_key = "long_hit" if side == "LONG" else "short_hit"
    bars_key = "long_bars" if side == "LONG" else "short_bars"
    distances = sorted(
        ((_distance(vector, item[vkey]), item) for item in reference),
        key=lambda pair: pair[0],
    )
    # Avoid counting adjacent minutes from one single swing as independent
    # historical examples. Selected analogue timestamps must be separated by
    # at least the full outcome horizon (default 20 minutes).
    nearest: list[tuple[float, dict[str, Any]]] = []
    selected_times: list[int] = []
    separation_ms = max(1, int(min_separation_minutes)) * 60_000
    for distance, item in distances:
        if distance > 2.25:
            continue
        stamp = int(_f(item.get("time_ms")))
        if any(abs(stamp - prior) < separation_ms for prior in selected_times):
            continue
        nearest.append((distance, item))
        selected_times.append(stamp)
        if len(nearest) >= k:
            break
    n = len(nearest)
    wins = sum(1 for _, item in nearest if item[hit_key])
    weighted_wins = sum(exp(-distance * 1.15) for distance, item in nearest if item[hit_key])
    weight_total = sum(exp(-distance * 1.15) for distance, _ in nearest)
    # A small neutral prior prevents tiny neighborhoods from producing 0%/100% claims.
    smoothed = (weighted_wins + 2.5) / (weight_total + 5.0) if weight_total > 0 else 0.5
    bars = [int(item[bars_key]) for _, item in nearest if item[hit_key] and item[bars_key] is not None]
    avg_distance = sum(distance for distance, _ in nearest) / n if n else None
    return {
        "sample_count": n,
        "tp_first_count": wins,
        "tp_first_rate_pct": round(smoothed * 100.0, 2),
        "wilson_lower_80_pct": round(_wilson_lower(wins, n) * 100.0, 2),
        "median_minutes_to_tp": round(float(median(bars)), 1) if bars else None,
        "mean_similarity_distance": round(avg_distance, 4) if avg_distance is not None else None,
        "nearest_labels": {
            label: sum(1 for _, item in nearest if item["long_label" if side == "LONG" else "short_label"] == label)
            for label in ("TP_FIRST", "ADVERSE_FIRST", "BOTH_SAME_BAR_CONSERVATIVE_LOSS", "NO_TOUCH_WITHIN_HORIZON")
        },
    }


def _book_metrics(orderbook: dict[str, Any] | None, now_ms: int) -> dict[str, Any]:
    if not isinstance(orderbook, dict):
        return {"available": False, "age_seconds": None, "imbalance": None, "spread_bps": None}
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    timestamp = int(_f(orderbook.get("timestamp_ms")))
    age = max(0.0, (now_ms - timestamp) / 1000.0) if timestamp else None
    try:
        bid_prices = [_f(row[0]) for row in bids if len(row) >= 2 and _f(row[0]) > 0]
        ask_prices = [_f(row[0]) for row in asks if len(row) >= 2 and _f(row[0]) > 0]
        if not bid_prices or not ask_prices:
            raise ValueError("empty")
        best_bid, best_ask = max(bid_prices), min(ask_prices)
        mid = (best_bid + best_ask) / 2.0
        spread = (best_ask - best_bid) / mid * 10000.0 if mid > 0 else None
        bid_notional = sum(_f(row[0]) * _f(row[1]) / sqrt(i + 1) for i, row in enumerate(bids[:10]) if len(row) >= 2)
        ask_notional = sum(_f(row[0]) * _f(row[1]) / sqrt(i + 1) for i, row in enumerate(asks[:10]) if len(row) >= 2)
        total = bid_notional + ask_notional
        imbalance = (bid_notional - ask_notional) / total if total > 0 else 0.0
        return {
            "available": True, "age_seconds": round(age, 2) if age is not None else None,
            "best_bid": best_bid, "best_ask": best_ask, "mid": mid,
            "spread_bps": round(spread, 3) if spread is not None else None,
            "bid_notional_weighted": round(bid_notional, 6),
            "ask_notional_weighted": round(ask_notional, 6),
            "imbalance": round(_clamp(imbalance, -1.0, 1.0), 4),
        }
    except (TypeError, ValueError, IndexError):
        return {"available": False, "age_seconds": round(age, 2) if age is not None else None,
                "imbalance": None, "spread_bps": None}


def _trade_metrics(trades: dict[str, Any] | None, now_ms: int) -> dict[str, Any]:
    if not isinstance(trades, dict):
        return {"available": False, "age_seconds": None, "taker_imbalance": None, "trade_count": 0}
    rows = trades.get("trades") or []
    fallback_timestamp = int(_f(trades.get("timestamp_ms")))
    buy = sell = 0.0
    count = 0
    newest_trade_ms = 0
    stale_or_invalid = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        trade_ms = int(_f(item.get("time_ms"), fallback_timestamp))
        trade_age = (now_ms - trade_ms) / 1000.0 if trade_ms else float("inf")
        # Do not let a large recent-trades response dilute current taker pressure
        # with prints that happened several minutes ago.
        if trade_age < -5 or trade_age > 45:
            stale_or_invalid += 1
            continue
        notional = _f(item.get("price")) * _f(item.get("qty"))
        side = str(item.get("side", "")).upper()
        if notional <= 0:
            continue
        if side == "BUY":
            buy += notional
        elif side == "SELL":
            sell += notional
        else:
            continue
        count += 1
        newest_trade_ms = max(newest_trade_ms, trade_ms)
    total = buy + sell
    age = max(0.0, (now_ms - newest_trade_ms) / 1000.0) if newest_trade_ms else None
    return {
        "available": total > 0 and age is not None and age <= 45,
        "age_seconds": round(age, 2) if age is not None else None,
        "buy_notional": round(buy, 6), "sell_notional": round(sell, 6),
        "taker_imbalance": round((buy - sell) / total, 4) if total else None,
        "trade_count": count, "stale_or_invalid_trades_ignored": stale_or_invalid,
        "lookback_seconds": 45,
    }


def _pressure(
    features: dict[str, float], book: dict[str, Any], trades: dict[str, Any],
    cross_market: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Inputs are transformed into bounded directional votes. Missing micro-data is
    # omitted and weights are renormalized rather than fabricated as neutral evidence.
    volume_pulse = _clamp((features.get("volume_ratio", 1.0) - 0.60) / 1.0, 0.0, 1.0)
    components: list[tuple[str, float, float]] = [
        ("roc3", tanh(features.get("roc_3m_pct", 0.0) / 0.12), 0.13),
        ("roc5", tanh(features.get("roc_5m_pct", 0.0) / 0.20), 0.12),
        ("roc10", tanh(features.get("roc_10m_pct", 0.0) / 0.30), 0.07),
        ("ema_cross", tanh(features.get("ema_gap_pct", 0.0) / 0.15), 0.14),
        ("ema_acceleration", tanh(features.get("ema_slope_pct", 0.0) / 0.10), 0.08),
        ("vwap", tanh(features.get("vwap_gap_pct", 0.0) / 0.20), 0.10),
        ("rsi", _clamp((features.get("rsi7", 50.0) - 50.0) / 28.0, -1.0, 1.0), 0.05),
        ("acceleration", tanh(features.get("acceleration_3m_pct", 0.0) / 0.08), 0.08),
        ("directional_volume_pulse", tanh(features.get("roc_3m_pct", 0.0) / 0.12) * volume_pulse, 0.06),
    ]
    if book.get("available") and (book.get("age_seconds") is None or book.get("age_seconds") <= 45):
        components.append(("orderbook_imbalance", _clamp(_f(book.get("imbalance")), -1.0, 1.0), 0.12))
    if trades.get("available") and (trades.get("age_seconds") is None or trades.get("age_seconds") <= 45):
        components.append(("taker_flow", _clamp(_f(trades.get("taker_imbalance")), -1.0, 1.0), 0.11))
    # BTC and ETH are contextual leaders, not substitute signals for RENDER.
    # Their 5m returns get a small, renormalized weight when fresh.
    cross_assets = (cross_market or {}).get("assets", {}) if isinstance(cross_market, dict) else {}
    fresh_leader_returns = []
    for asset in ("BTCUSDT", "ETHUSDT"):
        row = cross_assets.get(asset, {}) if isinstance(cross_assets, dict) else {}
        age = _f(row.get("age_seconds"), 999.0)
        roc5 = row.get("roc_5m_pct")
        if row.get("available") and roc5 is not None and age <= 90:
            leader_return = _f(roc5)
            fresh_leader_returns.append(leader_return)
            components.append((f"cross_{asset.lower()}_5m", tanh(leader_return / 0.18), 0.045))
    if len(fresh_leader_returns) == 2:
        leader_average = sum(fresh_leader_returns) / 2.0
        relative_strength = features.get("roc_5m_pct", 0.0) - leader_average
        components.append(("render_relative_strength_vs_btc_eth_5m", tanh(relative_strength / 0.18), 0.07))
    total_weight = sum(weight for _, _, weight in components)
    signed = sum(value * weight for _, value, weight in components) / total_weight if total_weight else 0.0
    volume_quality = _clamp((features.get("volume_ratio", 1.0) - 0.65) / 1.15, 0.0, 1.0)
    # Pressure is deliberately a score, not a future-success probability.
    long_score = _clamp(50.0 + signed * 50.0, 0.0, 100.0)
    short_score = 100.0 - long_score
    return {
        "long_score": round(long_score, 2), "short_score": round(short_score, 2),
        "signed_pressure": round(signed, 4),
        "volume_quality_pct": round(volume_quality * 100.0, 1),
        "components": {name: round(value, 4) for name, value, _ in components},
        "component_count": len(components),
    }


def _runway(
    candles: list[dict[str, Any]], price: float, side: str, target_pct: float,
    higher_timeframe_levels: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check near-term 1m obstacles and the 15m structural level from the main engine."""
    rows = candles[-60:]
    candidates: list[tuple[float, str]] = []
    levels = higher_timeframe_levels if isinstance(higher_timeframe_levels, dict) else {}
    if side == "LONG":
        candidates.extend(
            (_f(c.get("high")), "1m_recent_high")
            for c in rows if _f(c.get("high")) > price
        )
        resistance = _f(levels.get("resistance"))
        if resistance > price:
            candidates.append((resistance, "15m_resistance_31"))
        nearest, source = min(candidates, key=lambda item: item[0]) if candidates else (None, None)
        distance = ((nearest / price) - 1.0) * 100.0 if nearest and price > 0 else None
    else:
        candidates.extend(
            (_f(c.get("low")), "1m_recent_low")
            for c in rows if 0 < _f(c.get("low")) < price
        )
        support = _f(levels.get("support"))
        if 0 < support < price:
            candidates.append((support, "15m_support_31"))
        nearest, source = max(candidates, key=lambda item: item[0]) if candidates else (None, None)
        distance = ((price / nearest) - 1.0) * 100.0 if nearest and price > 0 else None
    blocked = distance is not None and distance < target_pct * 1.12
    return {
        "nearest_obstacle_price": round(nearest, 10) if nearest else None,
        "obstacle_source": source,
        "distance_to_obstacle_pct": round(distance, 5) if distance is not None else None,
        "required_clearance_pct": round(target_pct * 1.12, 5),
        "blocked": blocked,
        "higher_timeframe_levels": {
            "support": levels.get("support"), "resistance": levels.get("resistance"),
            "source_interval": levels.get("source_interval", "15m"),
        },
    }


@dataclass
class FastEntryEngine:
    """Cache historical labels per newly closed 1m candle and TP profile."""
    cache_key: tuple[int, float, float, int] | None = None
    reference: list[dict[str, Any]] | None = None
    reference_built_at_ms: int | None = None

    def analyze(
        self,
        candles: list[dict[str, Any]],
        price: float,
        orderbook: dict[str, Any] | None,
        trades: dict[str, Any] | None,
        *, target_pct: float = 0.20, adverse_pct: float = 0.12,
        horizon_minutes: int = 20, now_ms: int | None = None,
        min_samples: int = 25, min_hit_rate_pct: float = 62.0,
        min_wilson_pct: float = 50.0, min_live_confirmation_pct: float = 58.0,
        max_spread_bps: float = 8.0, max_minutes_to_target: float = 15.0,
        opening_fee_pct: float = 0.035, exposure_pct: float = 100.0,
        cross_market_context: dict[str, Any] | None = None,
        higher_timeframe_levels: dict[str, Any] | None = None,
        reference_candles: int = 2400,
        min_directional_edge_gap_pct: float = 4.5,
    ) -> dict[str, Any]:
        now_ms = int(now_ms or __import__("time").time() * 1000)
        closed = [c for c in candles if int(_f(c.get("close_time"))) <= now_ms and _f(c.get("close")) > 0]
        last_closed = int(_f(closed[-1].get("open_time"))) if closed else 0
        base = {
            "status": "INSUFFICIENT_DATA", "target_pct": target_pct,
            "adverse_path_barrier_pct": adverse_pct,
            "outcome_horizon_minutes": horizon_minutes,
            "historical_sample_count": 0, "candidate_side": None,
            "watch_candidate": False, "opportunities": {}, "reasons": [],
            "cross_market": cross_market_context or {"available": False, "assets": {}},
        }
        if len(closed) < max(80, horizon_minutes + 45) or price <= 0:
            base["reasons"] = [f"Se requieren al menos {max(80, horizon_minutes + 45)} velas 1m cerradas; disponibles {len(closed)}."]
            return base

        key = (last_closed, round(target_pct, 4), round(adverse_pct, 4), int(horizon_minutes))
        if self.cache_key != key or self.reference is None:
            self.reference = _build_reference_samples(
                closed[-max(600, int(reference_candles)):], target_pct, adverse_pct,
                horizon_minutes, max_samples=max(600, int(reference_candles)),
            )
            self.cache_key = key
            self.reference_built_at_ms = now_ms
        current_features = _features(closed[-120:], price)
        if not current_features:
            base["reasons"] = ["No se pudieron calcular los indicadores de 1m."]
            return base
        book = _book_metrics(orderbook, now_ms)
        trade = _trade_metrics(trades, now_ms)
        pressure = _pressure(current_features, book, trade, cross_market_context)
        market_data_fresh = all(
            item.get("available") and (item.get("age_seconds") is None or item.get("age_seconds") <= 45)
            for item in (book, trade)
        )
        cross_assets = (cross_market_context or {}).get("assets", {}) if isinstance(cross_market_context, dict) else {}
        cross_fresh = {
            symbol: row for symbol, row in (cross_assets.items() if isinstance(cross_assets, dict) else [])
            if isinstance(row, dict) and row.get("available") and _f(row.get("age_seconds"), 999.0) <= 90
        }
        cross_diagnostics: dict[str, Any] = {"available": len(cross_fresh) == 2, "assets_fresh": sorted(cross_fresh)}
        if len(cross_fresh) == 2:
            leader_average = sum(_f(row.get("roc_5m_pct")) for row in cross_fresh.values()) / 2.0
            render_roc5 = _f(current_features.get("roc_5m_pct"))
            relative_strength = render_roc5 - leader_average
            cross_diagnostics.update({
                "leaders_average_roc_5m_pct": round(leader_average, 5),
                "render_roc_5m_pct": round(render_roc5, 5),
                "render_relative_strength_5m_pct": round(relative_strength, 5),
                "relative_reading": "RENDER_OUTPERFORMING" if relative_strength >= 0.08 else (
                    "RENDER_UNDERPERFORMING" if relative_strength <= -0.08 else "RELATIVE_ALIGNMENT"
                ),
                "relative_threshold_pct": 0.08,
            })
        spread_bps = book.get("spread_bps")
        opportunities: dict[str, Any] = {}
        for side in ("LONG", "SHORT"):
            vector = _directional_vector(current_features, side)
            historical = _neighbor_stats(vector, self.reference or [], side)
            runway = _runway(closed, price, side, target_pct, higher_timeframe_levels)
            live_score = pressure["long_score"] if side == "LONG" else pressure["short_score"]
            blockers: list[str] = []
            if historical["sample_count"] < min_samples:
                blockers.append(f"Solo {historical['sample_count']} analogías comparables; mínimo {min_samples}.")
            if historical["tp_first_rate_pct"] < min_hit_rate_pct:
                blockers.append(f"TP primero estimado {historical['tp_first_rate_pct']:.1f}%, inferior a {min_hit_rate_pct:.1f}%.")
            if historical["wilson_lower_80_pct"] < min_wilson_pct:
                blockers.append(f"Límite inferior de confianza {historical['wilson_lower_80_pct']:.1f}%, inferior a {min_wilson_pct:.1f}%.")
            if live_score < min_live_confirmation_pct:
                blockers.append(f"Presión actual {live_score:.1f}/100, inferior a {min_live_confirmation_pct:.1f}.")
            if not market_data_fresh:
                blockers.append("Se requieren order book Y flujo taker recientes; falta uno o ambos.")
            if len(cross_fresh) == 2:
                leader_returns = [_f(row.get("roc_5m_pct")) for row in cross_fresh.values()]
                relative = _f(cross_diagnostics.get("render_relative_strength_5m_pct"))
                leaders_down = all(value <= -0.12 for value in leader_returns)
                leaders_up = all(value >= 0.12 for value in leader_returns)
                # A broad-market move is not an automatic veto: RENDER can show
                # meaningful relative strength/weakness and decouple from leaders.
                long_conflict = side == "LONG" and leaders_down and relative < 0.08
                short_conflict = side == "SHORT" and leaders_up and relative > -0.08
                if long_conflict or short_conflict:
                    blockers.append(
                        "BTC y ETH contradicen la dirección y RENDER no muestra desacople relativo suficiente "
                        f"(fuerza relativa 5m {relative:+.3f} puntos porcentuales)."
                    )
            if not book.get("available") or spread_bps is None:
                blockers.append("No se pudo verificar el spread del order book.")
            if spread_bps is not None and spread_bps > max_spread_bps:
                blockers.append(f"Spread {spread_bps:.2f} bps supera el máximo {max_spread_bps:.2f} bps.")
            if runway["blocked"]:
                blockers.append(f"Obstáculo cercano a {runway['distance_to_obstacle_pct']:.3f}%: no deja espacio para el objetivo.")
            median_minutes = historical.get("median_minutes_to_tp")
            if median_minutes is None or median_minutes > max_minutes_to_target:
                blockers.append(f"Tiempo mediano al objetivo no disponible o supera {max_minutes_to_target:.1f} minutos.")
            if trade.get("available") and book.get("available"):
                book_signal = _f(book.get("imbalance"))
                trade_signal = _f(trade.get("taker_imbalance"))
                conflict = (side == "LONG" and book_signal < -0.28 and trade_signal < -0.18) or (
                    side == "SHORT" and book_signal > 0.28 and trade_signal > 0.18
                )
                if conflict:
                    blockers.append("Order book y agresión de mercado contradicen simultáneamente esta dirección.")
            net_target = target_pct * exposure_pct / 100.0 - opening_fee_pct
            spread_pct = (spread_bps / 100.0) if spread_bps is not None else 0.0
            net_after_spread = net_target - spread_pct * exposure_pct / 100.0
            if net_after_spread <= 0:
                blockers.append("El objetivo no deja rendimiento neto positivo después de comisión y spread estimado.")
            eligible = not blockers
            # Watchlist can trigger a fresh LLM review before the full entry threshold is met.
            watch = (
                historical["sample_count"] >= max(12, min_samples // 2)
                and historical["tp_first_rate_pct"] >= min_hit_rate_pct - 7.0
                and live_score >= min_live_confirmation_pct - 8.0
                and not runway["blocked"]
                and market_data_fresh
            )
            opportunities[side] = {
                "eligible": eligible, "watch_candidate": watch,
                "historical": historical, "live_confirmation_pct": round(live_score, 2),
                "runway": runway, "blockers": blockers,
                "estimated_net_wallet_target_pct": round(net_after_spread, 5),
            }

        # Compare the two directional edges. If they are nearly tied, do not
        # manufacture a direction merely because one score is a fraction higher.
        for side, item in opportunities.items():
            item["directional_quality_score"] = round(
                0.68 * item["historical"]["tp_first_rate_pct"]
                + 0.32 * item["live_confirmation_pct"], 2,
            )
        if all(side in opportunities for side in ("LONG", "SHORT")):
            long_q = opportunities["LONG"]["directional_quality_score"]
            short_q = opportunities["SHORT"]["directional_quality_score"]
            edge_gap = abs(long_q - short_q)
            winning_side = "LONG" if long_q > short_q else "SHORT" if short_q > long_q else None
            for side, item in opportunities.items():
                item["directional_edge_gap_pct"] = round(edge_gap, 2)
            if edge_gap < min_directional_edge_gap_pct:
                for side, item in opportunities.items():
                    item["eligible"] = False
                    item["watch_candidate"] = False
                    item["blockers"].append(
                        f"Ventaja LONG/SHORT demasiado estrecha ({edge_gap:.2f} puntos); se requieren {min_directional_edge_gap_pct:.2f}."
                    )
            elif winning_side:
                losing_side = "SHORT" if winning_side == "LONG" else "LONG"
                if opportunities[winning_side]["eligible"]:
                    opportunities[losing_side]["eligible"] = False
                    opportunities[losing_side]["blockers"].append(
                        f"La ventaja direccional favorece {winning_side} por {edge_gap:.2f} puntos."
                    )
                if opportunities[winning_side]["watch_candidate"]:
                    opportunities[losing_side]["watch_candidate"] = False
        eligible_sides = [side for side, item in opportunities.items() if item["eligible"]]
        watch_sides = [side for side, item in opportunities.items() if item["watch_candidate"]]
        candidate = None
        if len(eligible_sides) == 1:
            candidate = eligible_sides[0]
        elif eligible_sides:
            candidate = max(eligible_sides, key=lambda side: opportunities[side]["historical"]["tp_first_rate_pct"] + opportunities[side]["live_confirmation_pct"])
        elif len(watch_sides) == 1:
            candidate = watch_sides[0]
        elif watch_sides:
            candidate = max(watch_sides, key=lambda side: opportunities[side]["historical"]["tp_first_rate_pct"] + opportunities[side]["live_confirmation_pct"])
        base.update({
            "status": "READY",
            "last_closed_candle_ms": last_closed,
            "history_bars": len(closed),
            "target_pct": target_pct,
            "adverse_path_barrier_pct": adverse_pct,
            "outcome_horizon_minutes": horizon_minutes,
            "historical_sample_count": len(self.reference or []),
            "historical_reference_built_at_ms": self.reference_built_at_ms,
            "current_features": {k: round(v, 6) for k, v in current_features.items()},
            "orderbook": book,
            "trade_flow": trade,
            "cross_market": cross_market_context or {"available": False, "assets": {}},
            "cross_market_diagnostics": cross_diagnostics,
            "directional_pressure": pressure,
            "feature_names": list(FEATURE_NAMES),
            "opportunities": opportunities,
            "eligible_sides": eligible_sides,
            "watch_sides": watch_sides,
            "candidate_side": candidate,
            "watch_candidate": bool(candidate),
            "rules": {
                "minimum_comparable_samples": min_samples,
                "analogue_minimum_time_separation_minutes": horizon_minutes,
                "cross_market_conflict_block": "BTC y ETH simultáneamente opuestos en retorno 5m",
                "minimum_tp_first_rate_pct": min_hit_rate_pct,
                "minimum_wilson_lower_80_pct": min_wilson_pct,
                "minimum_live_confirmation_pct": min_live_confirmation_pct,
                "maximum_spread_bps": max_spread_bps,
                "maximum_median_minutes_to_target": max_minutes_to_target,
                "minimum_directional_edge_gap_pct": min_directional_edge_gap_pct,
                "reference_candles": max(600, int(reference_candles)),
                "higher_timeframe_obstacle_check": "15m support/resistance from main engine plus recent 1m highs/lows",
                "adverse_path_barrier_is_not_live_stop_loss": True,
            },
            "reasons": [
                "Los porcentajes TP-first son tasas históricas de analogías cercanas, no probabilidades garantizadas.",
                "Las señales de order book y taker flow son instantáneas y pueden cambiar o cancelarse.",
                "Las analogías históricas seleccionadas se separan temporalmente por al menos el horizonte de evaluación para reducir muestras redundantes.",
                "La dirección se bloquea si las puntuaciones de calidad LONG y SHORT quedan demasiado próximas; no se fuerza una entrada por un empate.",
                "El espacio al Take Profit se comprueba frente a máximos/mínimos 1m y frente al soporte/resistencia estructural 15m.",
                "BTC y ETH son confirmación contextual; no sustituyen el análisis propio de RENDER.",
                "La etiqueta histórica cuenta la barrera adversa antes del TP como fracaso; si ambos niveles ocurren en la misma vela 1m, se cuenta conservadoramente como fracaso.",
            ],
        })
        return base
