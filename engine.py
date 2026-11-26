"""PRIMA-15: lightweight multi-scale swing and prime-cycle analysis.

This module deliberately uses only the Python standard library. Prime numbers are
used as candidate time scales, not as price targets or claims of causality.
"""
from __future__ import annotations

from math import sqrt, exp, log
import time
from typing import Any

BASE_THRESHOLD = 68.0
LEFT_PIVOT = 3
RIGHT_PIVOT = 3

# Number of 15-minute candles. The largest window spans a little under 32 hours.
# Prime window sizes are generated dynamically by prime_windows_for_length().
PRIME_RATIOS = [
    2 / 3, 3 / 2, 3 / 5, 5 / 3, 5 / 7, 7 / 5,
    7 / 11, 11 / 7, 11 / 13, 13 / 11, 13 / 17, 17 / 13,
]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def nearest_prime(value: int) -> tuple[int | None, int | None]:
    """Return the nearest prime, generating candidates beyond the static seed list."""
    if value <= 0:
        return None, None
    lower = value
    while lower >= 2 and not _is_prime(lower):
        lower -= 1
    upper = value
    while not _is_prime(upper):
        upper += 1
    candidates = [p for p in (lower, upper) if p >= 2]
    prime = min(candidates, key=lambda p: (abs(p - value), p))
    return prime, abs(prime - value)


def _rsi_series(closes: list[float], period: int = 14) -> list[float]:
    if not closes:
        return []
    result = [50.0] * len(closes)
    if len(closes) <= period:
        return result
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    result[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        result[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return result


def _atr_series(candles: list[dict[str, Any]], period: int = 14) -> list[float]:
    if not candles:
        return []
    true_ranges: list[float] = []
    previous_close = _f(candles[0].get("close"))
    for i, candle in enumerate(candles):
        high = _f(candle.get("high"))
        low = _f(candle.get("low"))
        close = _f(candle.get("close"))
        if i == 0:
            tr = max(high - low, 0.0)
        else:
            tr = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(tr)
        previous_close = close
    result = [0.0] * len(candles)
    if len(candles) < period:
        avg = sum(true_ranges) / max(1, len(true_ranges))
        return [avg] * len(candles)
    avg = sum(true_ranges[:period]) / period
    result[period - 1] = avg
    for i in range(period, len(true_ranges)):
        avg = (avg * (period - 1) + true_ranges[i]) / period
        result[i] = avg
    for i in range(period - 1):
        result[i] = result[period - 1]
    return result


def detect_pivots(
    candles: list[dict[str, Any]],
    rsi_values: list[float] | None = None,
    left: int = LEFT_PIVOT,
    right: int = RIGHT_PIVOT,
) -> list[dict[str, Any]]:
    """Return confirmed alternating pivots. The last `right` candles are unconfirmed."""
    n = len(candles)
    if n < left + right + 5:
        return []
    atrs = _atr_series(candles)
    pivots: list[dict[str, Any]] = []
    stop = n - right
    for i in range(left, stop):
        high = _f(candles[i].get("high"))
        low = _f(candles[i].get("low"))
        left_highs = [_f(candles[j].get("high")) for j in range(i - left, i)]
        right_highs = [_f(candles[j].get("high")) for j in range(i + 1, i + right + 1)]
        left_lows = [_f(candles[j].get("low")) for j in range(i - left, i)]
        right_lows = [_f(candles[j].get("low")) for j in range(i + 1, i + right + 1)]
        is_high = high >= max(left_highs + right_highs) and high > max(left_highs)
        is_low = low <= min(left_lows + right_lows) and low < min(left_lows)
        if not is_high and not is_low:
            continue
        if is_high and is_low:
            candle_range = max(high - low, 1e-12)
            close = _f(candles[i].get("close"))
            # For an outside candle, keep the side with the stronger rejection.
            is_high = (high - close) / candle_range < (close - low) / candle_range
            is_low = not is_high
        kind = "HIGH" if is_high else "LOW"
        price = high if is_high else low
        item = {
            "index": i,
            "time": int(candles[i].get("open_time", i)),
            "type": kind,
            "price": price,
            "rsi": (rsi_values[i] if rsi_values and i < len(rsi_values) else 50.0),
        }
        if pivots and pivots[-1]["type"] == kind:
            more_extreme = price > pivots[-1]["price"] if kind == "HIGH" else price < pivots[-1]["price"]
            if more_extreme:
                previous = pivots[-2] if len(pivots) >= 2 else None
                if previous is not None:
                    duration = i - int(previous["index"])
                    p, delta = nearest_prime(duration)
                    item["duration_bars"] = duration
                    item["nearest_prime"] = p
                    item["prime_delta"] = delta
                    item["duration_minutes"] = duration * 15
                else:
                    item["duration_bars"] = None
                    item["nearest_prime"] = None
                    item["prime_delta"] = None
                    item["duration_minutes"] = None
                pivots[-1] = item
            continue
        if pivots:
            move = abs(price - pivots[-1]["price"])
            local_atr = atrs[i] if i < len(atrs) else 0.0
            if local_atr > 0 and move < local_atr * 0.55:
                continue
            duration = i - pivots[-1]["index"]
            p, delta = nearest_prime(duration)
            item["duration_bars"] = duration
            item["nearest_prime"] = p
            item["prime_delta"] = delta
            item["duration_minutes"] = duration * 15
        else:
            item["duration_bars"] = None
            item["nearest_prime"] = None
            item["prime_delta"] = None
            item["duration_minutes"] = None
        pivots.append(item)
    return pivots


def _divergence(pivots: list[dict[str, Any]], side: str) -> bool:
    kind = "LOW" if side == "LONG" else "HIGH"
    same = [p for p in pivots if p["type"] == kind]
    if len(same) < 2:
        return False
    previous, current = same[-2], same[-1]
    if side == "LONG":
        return current["price"] < previous["price"] and current["rsi"] > previous["rsi"] + 2.0
    return current["price"] > previous["price"] and current["rsi"] < previous["rsi"] - 2.0


def _temporal_prime_score(pivots: list[dict[str, Any]], side: str, current_index: int) -> tuple[float, int | None, int | None, int | None]:
    wanted = "HIGH" if side == "LONG" else "LOW"
    candidates = [p for p in pivots if p["type"] == wanted]
    if not candidates:
        return 0.0, None, None, None
    last = candidates[-1]
    elapsed = max(1, current_index - int(last["index"]))
    prime, delta = nearest_prime(elapsed)
    score = 0.0
    if delta == 0:
        score = 12.0
    elif delta == 1:
        score = 8.0
    elif delta == 2:
        score = 4.0
    elif delta == 3:
        score = 1.5

    # Compare the current leg duration with the immediately preceding completed leg.
    previous_leg = None
    for p in reversed(pivots):
        if p["index"] < last["index"] and p.get("duration_bars"):
            previous_leg = int(p["duration_bars"])
            break
    ratio_score = 0.0
    if previous_leg and previous_leg > 0:
        ratio = elapsed / previous_leg
        distance = min(abs(ratio - r) / r for r in PRIME_RATIOS)
        if distance <= 0.035:
            ratio_score = 6.0
        elif distance <= 0.07:
            ratio_score = 3.0
    return min(12.0, score) + ratio_score, elapsed, prime, delta


def _zone_scores(candles: list[dict[str, Any]], price: float) -> tuple[float, float, int, int, float | None, float | None]:
    long_acc = 0.0
    short_acc = 0.0
    weight_acc = 0.0
    long_count = 0
    short_count = 0
    support = None
    resistance = None
    for window in prime_windows_for_length(len(candles)):
        if len(candles) < window:
            continue
        segment = candles[-window:]
        lows = [_f(c.get("low")) for c in segment]
        highs = [_f(c.get("high")) for c in segment]
        low = min(lows)
        high = max(highs)
        span = high - low
        if span <= 0:
            continue
        pos = (price - low) / span
        weight = 1.0 / sqrt(window)
        weight_acc += weight
        if pos <= 0.23:
            long_acc += weight * min(1.0, (0.23 - pos) / 0.23 + 0.15)
            long_count += 1
        if pos >= 0.77:
            short_acc += weight * min(1.0, (pos - 0.77) / 0.23 + 0.15)
            short_count += 1
        if window == 31:
            support, resistance = low, high
    if weight_acc == 0:
        return 0.0, 0.0, 0, 0, support, resistance
    return 34.0 * long_acc / weight_acc, 34.0 * short_acc / weight_acc, long_count, short_count, support, resistance



def analyze_wick_context(
    candles: list[dict[str, Any]],
    pivots: list[dict[str, Any]],
    current_price: float,
    atr: float,
    rsi: float,
    momentum_long: bool,
    momentum_short: bool,
    long_divergence: bool,
    short_divergence: bool,
    prime_long_delta: int | None,
    prime_short_delta: int | None,
) -> dict[str, Any]:
    """Explain whether the latest candle is a wick at a range extreme or a confirmed rejection.

    The latest candle is evaluated against *prior* candles for support/resistance so it
    cannot create its own reference level. A live candle is always provisional.
    """
    if not candles:
        return {"status": "INSUFFICIENT_DATA", "classification": "SIN_DATOS", "reasons": [], "blockers": []}

    last = candles[-1]
    open_price = _f(last.get("open"))
    high = _f(last.get("high"))
    low = _f(last.get("low"))
    close = _f(last.get("close"), current_price)
    current = current_price if current_price > 0 else close
    candle_range = max(high - low, 1e-12)
    body = abs(close - open_price)
    body_floor = max(body, current * 1e-8, 1e-12)
    lower_wick = max(0.0, min(open_price, close) - low)
    upper_wick = max(0.0, high - max(open_price, close))
    lower_ratio = lower_wick / candle_range
    upper_ratio = upper_wick / candle_range
    body_ratio = min(1.0, body / candle_range)
    close_position = max(0.0, min(1.0, (close - low) / candle_range))
    lower_body_ratio = lower_wick / body_floor
    upper_body_ratio = upper_wick / body_floor

    now_ms = int(time.time() * 1000)
    close_time = int(_f(last.get("close_time"), 0))
    candle_closed = bool(close_time > 0 and close_time <= now_ms)
    history = candles[:-1]
    scale_readings: list[dict[str, Any]] = []
    support_levels: list[tuple[int, float]] = []
    resistance_levels: list[tuple[int, float]] = []
    bottom_scale_count = 0
    top_scale_count = 0
    p_values: list[float] = []
    low_p_values: list[float] = []
    high_p_values: list[float] = []

    for window in prime_windows_for_length(len(candles)):
        if len(history) < window:
            continue
        segment = history[-window:]
        window_low = min(_f(c.get("low")) for c in segment)
        window_high = max(_f(c.get("high")) for c in segment)
        span = window_high - window_low
        if span <= 0:
            continue
        p_price = (current - window_low) / span
        p_low = (low - window_low) / span
        p_high = (high - window_low) / span
        p_values.append(p_price)
        low_p_values.append(p_low)
        high_p_values.append(p_high)
        near_low = p_low <= 0.18
        near_high = p_high >= 0.82
        if near_low:
            bottom_scale_count += 1
        if near_high:
            top_scale_count += 1
        support_levels.append((window, window_low))
        resistance_levels.append((window, window_high))
        scale_readings.append({
            "window": window,
            "minutes": window * 15,
            "low": round(window_low, 8),
            "high": round(window_high, 8),
            "p_price": round(p_price, 4),
            "p_low": round(p_low, 4),
            "p_high": round(p_high, 4),
            "near_low": near_low,
            "near_high": near_high,
        })

    def nearest_level(levels: list[tuple[int, float]], value: float) -> tuple[int | None, float | None, float | None]:
        if not levels:
            return None, None, None
        window, level = min(levels, key=lambda item: abs(value - item[1]))
        distance_pct = abs(value - level) / max(abs(level), 1e-12) * 100.0
        distance_atr = abs(value - level) / atr if atr > 0 else None
        return window, level, distance_pct if distance_atr is None else distance_pct

    support_window, support_level, low_distance_pct = nearest_level(support_levels, low)
    resistance_window, resistance_level, high_distance_pct = nearest_level(resistance_levels, high)
    tolerance = max(atr * 0.20, current * 0.00025, 1e-12)
    support_reclaim_windows = [
        window for window, level in support_levels
        if low <= level + tolerance and close > level
    ]
    resistance_reject_windows = [
        window for window, level in resistance_levels
        if high >= level - tolerance and close < level
    ]
    breakdown_without_reclaim = bool(
        support_level is not None and low < support_level - tolerance and close <= support_level
    )
    breakout_without_rejection = bool(
        resistance_level is not None and high > resistance_level + tolerance and close >= resistance_level
    )

    lower_shape = bool(lower_ratio >= 0.28 and lower_body_ratio >= 0.85 and close_position >= 0.58)
    upper_shape = bool(upper_ratio >= 0.28 and upper_body_ratio >= 0.85 and close_position <= 0.42)
    lower_strong = bool(lower_ratio >= 0.42 and lower_body_ratio >= 1.5 and close_position >= 0.70)
    upper_strong = bool(upper_ratio >= 0.42 and upper_body_ratio >= 1.5 and close_position <= 0.30)
    lower_location = bottom_scale_count >= 2 or (bool(low_p_values) and min(low_p_values) <= 0.08)
    upper_location = top_scale_count >= 2 or (bool(high_p_values) and max(high_p_values) >= 0.92)

    volume_values = [_f(c.get("volume")) for c in history[-20:] if _f(c.get("volume")) > 0]
    volume_median = sorted(volume_values)[len(volume_values) // 2] if volume_values else 0.0
    current_volume = _f(last.get("volume"))
    volume_ratio = current_volume / volume_median if volume_median > 0 and candle_closed else None
    volume_spike = bool(volume_ratio is not None and volume_ratio >= 1.45)

    lower_context = {
        "geometry": lower_shape,
        "multi_scale_low": lower_location,
        "support_reclaim": bool(support_reclaim_windows),
        "momentum_turn": bool(momentum_long),
        "oscillator_or_divergence": bool(rsi <= 45 or long_divergence),
        "volume_spike": volume_spike,
        "prime_timing": bool(prime_long_delta is not None and prime_long_delta <= 2),
    }
    upper_context = {
        "geometry": upper_shape,
        "multi_scale_high": upper_location,
        "resistance_rejection": bool(resistance_reject_windows),
        "momentum_turn": bool(momentum_short),
        "oscillator_or_divergence": bool(rsi >= 55 or short_divergence),
        "volume_spike": volume_spike,
        "prime_timing": bool(prime_short_delta is not None and prime_short_delta <= 2),
    }
    lower_context_count = sum(bool(v) for k, v in lower_context.items() if k not in {"geometry", "multi_scale_low"})
    upper_context_count = sum(bool(v) for k, v in upper_context.items() if k not in {"geometry", "multi_scale_high"})
    lower_confirmations = sum(bool(v) for v in lower_context.values())
    upper_confirmations = sum(bool(v) for v in upper_context.values())

    recent_low_pivot = next((p for p in reversed(pivots) if p.get("type") == "LOW"), None)
    recent_high_pivot = next((p for p in reversed(pivots) if p.get("type") == "HIGH"), None)
    last_index = len(candles) - 1
    min_pivot_confirmed = bool(
        recent_low_pivot
        and int(recent_low_pivot.get("index", -999)) >= last_index - 4
        and abs(float(recent_low_pivot.get("price", 0)) - low) <= max(atr * 0.20, current * 0.0002)
    )
    max_pivot_confirmed = bool(
        recent_high_pivot
        and int(recent_high_pivot.get("index", -999)) >= last_index - 4
        and abs(float(recent_high_pivot.get("price", 0)) - high) <= max(atr * 0.20, current * 0.0002)
    )

    # A wick is not a confirmed rejection merely because its geometry and context
    # look interesting. Require an actual reclaim/rejection of a prior level plus
    # at least one independent confirmation (momentum, oscillator/divergence,
    # volume or prime timing). This keeps simple breakouts from being mislabeled.
    lower_confirmed = bool(
        candle_closed and lower_shape and lower_location
        and bool(support_reclaim_windows) and lower_context_count >= 2
    )
    upper_confirmed = bool(
        candle_closed and upper_shape and upper_location
        and bool(resistance_reject_windows) and upper_context_count >= 2
    )

    lower_reasons: list[str] = []
    lower_blockers: list[str] = []
    upper_reasons: list[str] = []
    upper_blockers: list[str] = []

    if lower_shape:
        lower_reasons.append(f"Geometría inferior: mecha {lower_ratio * 100:.1f}% del rango y {lower_body_ratio:.2f}× el cuerpo; cierre en P15={close_position:.3f}.")
    else:
        if lower_ratio < 0.28:
            lower_blockers.append(f"La mecha inferior ocupa {lower_ratio * 100:.1f}% del rango; se exige al menos 28%.")
        if lower_body_ratio < 0.85:
            lower_blockers.append(f"La mecha inferior equivale a {lower_body_ratio:.2f}× el cuerpo; se exige al menos 0.85×.")
        if close_position < 0.58:
            lower_blockers.append(f"El cierre quedó en P15={close_position:.3f}; se exige cerrar en la mitad superior (≥0.58).")
    if lower_location:
        lower_reasons.append(f"Ubicación extrema: la mecha baja entra en zona inferior de {bottom_scale_count} escalas primas.")
    else:
        lower_blockers.append(f"La mecha baja solo coincide con {bottom_scale_count} escalas inferiores; se requieren 2 o una ruptura muy extendida (P≤0.08).")
    if support_reclaim_windows:
        lower_reasons.append("Recuperó soporte previo tras perforarlo o tocarlo en escalas: " + ", ".join(f"P{w}" for w in support_reclaim_windows[:5]) + ".")
    elif breakdown_without_reclaim:
        lower_blockers.append("Perforó el soporte y cerró debajo: todavía parece ruptura, no rechazo alcista.")
    else:
        lower_blockers.append("No hay recuperación clara de un soporte previo en el cierre; sin ella la mecha no cuenta como rechazo confirmado.")
    if momentum_long:
        lower_reasons.append("El impulso corto ya muestra giro alcista.")
    else:
        lower_blockers.append("Los cierres recientes todavía no confirman giro de impulso alcista.")
    if rsi <= 45 or long_divergence:
        lower_reasons.append("Hay contexto de agotamiento: RSI≤45 o divergencia alcista.")
    else:
        lower_blockers.append("No hay sobreventa relativa ni divergencia alcista.")
    if volume_spike:
        lower_reasons.append(f"Volumen de la vela cerrada: {volume_ratio:.2f}× la mediana reciente.")

    if upper_shape:
        upper_reasons.append(f"Geometría superior: mecha {upper_ratio * 100:.1f}% del rango y {upper_body_ratio:.2f}× el cuerpo; cierre en P15={close_position:.3f}.")
    else:
        if upper_ratio < 0.28:
            upper_blockers.append(f"La mecha superior ocupa {upper_ratio * 100:.1f}% del rango; se exige al menos 28%.")
        if upper_body_ratio < 0.85:
            upper_blockers.append(f"La mecha superior equivale a {upper_body_ratio:.2f}× el cuerpo; se exige al menos 0.85×.")
        if close_position > 0.42:
            upper_blockers.append(f"El cierre quedó en P15={close_position:.3f}; se exige cerrar en la mitad inferior (≤0.42).")
    if upper_location:
        upper_reasons.append(f"Ubicación extrema: la mecha alta entra en zona superior de {top_scale_count} escalas primas.")
    else:
        upper_blockers.append(f"La mecha alta solo coincide con {top_scale_count} escalas superiores; se requieren 2 o una ruptura muy extendida (P≥0.92).")
    if resistance_reject_windows:
        upper_reasons.append("Rechazó resistencia previa en escalas: " + ", ".join(f"P{w}" for w in resistance_reject_windows[:5]) + ".")
    elif breakout_without_rejection:
        upper_blockers.append("Superó resistencia y cerró por encima: todavía parece ruptura, no rechazo bajista.")
    else:
        upper_blockers.append("No hay rechazo claro de una resistencia previa en el cierre; sin él la mecha no cuenta como rechazo confirmado.")
    if momentum_short:
        upper_reasons.append("El impulso corto ya muestra giro bajista.")
    else:
        upper_blockers.append("Los cierres recientes todavía no confirman giro de impulso bajista.")
    if rsi >= 55 or short_divergence:
        upper_reasons.append("Hay contexto de agotamiento: RSI≥55 o divergencia bajista.")
    else:
        upper_blockers.append("No hay sobrecompra relativa ni divergencia bajista.")
    if volume_spike:
        upper_reasons.append(f"Volumen de la vela cerrada: {volume_ratio:.2f}× la mediana reciente.")

    if not candle_closed:
        lower_blockers.insert(0, "La vela de 15 minutos sigue abierta; la mecha puede crecer o desaparecer antes del cierre.")
        upper_blockers.insert(0, "La vela de 15 minutos sigue abierta; la mecha puede crecer o desaparecer antes del cierre.")
    if prime_long_delta is not None and prime_long_delta <= 2:
        lower_reasons.append(f"Coincidencia temporal cercana: diferencia de {prime_long_delta} vela(s) respecto al primo.")
    if prime_short_delta is not None and prime_short_delta <= 2:
        upper_reasons.append(f"Coincidencia temporal cercana: diferencia de {prime_short_delta} vela(s) respecto al primo.")

    if lower_confirmed and min_pivot_confirmed:
        lower_class = "MINIMO_ESTRUCTURAL_CONFIRMADO_CON_MECHA"
    elif lower_confirmed:
        lower_class = "RECHAZO_INFERIOR_CONFIRMADO"
    elif lower_shape and lower_location:
        lower_class = "MECHA_INFERIOR_PROVISIONAL" if not candle_closed else "MECHA_INFERIOR_EN_ZONA_BAJA_SIN_RECHAZO_CONFIRMADO"
    elif lower_shape:
        lower_class = "MECHA_INFERIOR_SIN_ZONA_DE_MINIMO"
    else:
        lower_class = "SIN_MECHA_INFERIOR_RELEVANTE"

    if upper_confirmed and max_pivot_confirmed:
        upper_class = "MAXIMO_ESTRUCTURAL_CONFIRMADO_CON_MECHA"
    elif upper_confirmed:
        upper_class = "RECHAZO_SUPERIOR_CONFIRMADO"
    elif upper_shape and upper_location:
        upper_class = "MECHA_SUPERIOR_PROVISIONAL" if not candle_closed else "MECHA_SUPERIOR_EN_ZONA_ALTA_SIN_RECHAZO_CONFIRMADO"
    elif upper_shape:
        upper_class = "MECHA_SUPERIOR_SIN_ZONA_DE_MAXIMO"
    else:
        upper_class = "SIN_MECHA_SUPERIOR_RELEVANTE"

    if lower_confirmed and upper_confirmed:
        overall = "DOBLE_RECHAZO_O_VELA_EXTERIOR"
    elif lower_confirmed:
        overall = lower_class
    elif upper_confirmed:
        overall = upper_class
    elif lower_shape and lower_location and not candle_closed:
        overall = lower_class
    elif upper_shape and upper_location and not candle_closed:
        overall = upper_class
    elif lower_shape or upper_shape:
        overall = "MECHA_SIN_CONFIRMACION_DE_EXTREMO"
    else:
        overall = "SIN_RECHAZO_CLARO"

    lower_score = min(100, int(round(
        25 * lower_shape + 20 * lower_location + 15 * bool(support_reclaim_windows)
        + 12 * momentum_long + 10 * (rsi <= 45 or long_divergence)
        + 8 * volume_spike + 10 * (prime_long_delta is not None and prime_long_delta <= 2)
    )))
    upper_score = min(100, int(round(
        25 * upper_shape + 20 * upper_location + 15 * bool(resistance_reject_windows)
        + 12 * momentum_short + 10 * (rsi >= 55 or short_divergence)
        + 8 * volume_spike + 10 * (prime_short_delta is not None and prime_short_delta <= 2)
    )))

    return {
        "status": "OK",
        "classification": overall,
        "candle_closed": candle_closed,
        "candle_close_time": close_time,
        "p_15m": round(close_position, 4),
        "body_pct_range": round(body_ratio * 100, 2),
        "lower_wick_pct_range": round(lower_ratio * 100, 2),
        "upper_wick_pct_range": round(upper_ratio * 100, 2),
        "lower_wick_to_body": round(lower_body_ratio, 3),
        "upper_wick_to_body": round(upper_body_ratio, 3),
        "lower_wick_shape": lower_shape,
        "upper_wick_shape": upper_shape,
        "lower_strong_shape": lower_strong,
        "upper_strong_shape": upper_strong,
        "bottom_scale_count": bottom_scale_count,
        "top_scale_count": top_scale_count,
        "p_median": round(sorted(p_values)[len(p_values) // 2], 4) if p_values else None,
        "p_min": round(min(p_values), 4) if p_values else None,
        "p_max": round(max(p_values), 4) if p_values else None,
        "support_reference": round(support_level, 8) if support_level is not None else None,
        "support_reference_window": support_window,
        "support_distance_pct": round(low_distance_pct, 4) if low_distance_pct is not None else None,
        "support_reclaim_windows": support_reclaim_windows,
        "resistance_reference": round(resistance_level, 8) if resistance_level is not None else None,
        "resistance_reference_window": resistance_window,
        "resistance_distance_pct": round(high_distance_pct, 4) if high_distance_pct is not None else None,
        "resistance_reject_windows": resistance_reject_windows,
        "breakdown_without_reclaim": breakdown_without_reclaim,
        "breakout_without_rejection": breakout_without_rejection,
        "volume_ratio": round(volume_ratio, 3) if volume_ratio is not None else None,
        "volume_spike": volume_spike,
        "lower": {
            "classification": lower_class,
            "shape": lower_shape,
            "location_confirmed": lower_location,
            "support_reclaim_confirmed": bool(support_reclaim_windows),
            "rejection_confirmed": lower_confirmed,
            "pivot_minimum_confirmed": min_pivot_confirmed,
            "score": lower_score,
            "confirmations": lower_confirmations,
            "context_confirmations": lower_context_count,
            "checks": lower_context,
            "reasons": lower_reasons,
            "blockers": lower_blockers,
        },
        "upper": {
            "classification": upper_class,
            "shape": upper_shape,
            "location_confirmed": upper_location,
            "resistance_rejection_confirmed": bool(resistance_reject_windows),
            "rejection_confirmed": upper_confirmed,
            "pivot_maximum_confirmed": max_pivot_confirmed,
            "score": upper_score,
            "confirmations": upper_confirmations,
            "context_confirmations": upper_context_count,
            "checks": upper_context,
            "reasons": upper_reasons,
            "blockers": upper_blockers,
        },
        "scale_readings": scale_readings,
        "summary": (
            "Mecha inferior confirmada" if lower_confirmed else
            "Mecha superior confirmada" if upper_confirmed else
            "Vela en formación: lectura provisional" if not candle_closed else
            "No hay rechazo de extremo confirmado"
        ),
    }


def prime_windows_for_length(length: int, maximum: int = 233) -> list[int]:
    """Generate prime analysis windows dynamically up to available history."""
    upper = min(int(maximum), int(length))
    return [value for value in range(7, upper + 1) if _is_prime(value)]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def analyze_temporal_dimension(
    candles: list[dict[str, Any]], pivots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Dimension 1: prime time scales, swing durations and phase alignment.

    Prime coincidences are descriptive features only; they are not treated as
    causal cycles or standalone directional signals.
    """
    if len(candles) < 14:
        return {
            "status": "INSUFFICIENT_DATA", "prime_windows": [], "scales": [],
            "completed_leg_count": 0, "prime_fit_pct": 0.0, "ratio_fit": None,
            "elapsed_from_last_high": None, "elapsed_from_last_low": None,
            "bias": "UNKNOWN", "explanation": "Se necesita más historial para estudiar las escalas temporales.",
        }

    windows = prime_windows_for_length(len(candles))
    current = _f(candles[-1].get("close"))
    scale_rows: list[dict[str, Any]] = []
    lows = highs = 0
    p_closes: list[float] = []
    for window in windows:
        segment = candles[-window:]
        lo = min(_f(c.get("low")) for c in segment)
        hi = max(_f(c.get("high")) for c in segment)
        span = hi - lo
        if span <= 0:
            continue
        p = (current - lo) / span
        near_low = p <= 0.23
        near_high = p >= 0.77
        lows += int(near_low)
        highs += int(near_high)
        p_closes.append(p)
        scale_rows.append({
            "prime": window, "minutes": window * 15,
            "p_close": round(p, 4), "range_low": round(lo, 8),
            "range_high": round(hi, 8), "near_low": near_low,
            "near_high": near_high,
        })

    durations = [int(p.get("duration_bars")) for p in pivots if p.get("duration_bars")]
    recent_durations = durations[-8:]
    fits: list[float] = []
    duration_rows: list[dict[str, Any]] = []
    for duration in recent_durations:
        prime, delta = nearest_prime(duration)
        fit = max(0.0, 100.0 * (1.0 - (float(delta or 0) / 4.0))) if delta is not None else 0.0
        fits.append(fit)
        duration_rows.append({
            "bars": duration, "minutes": duration * 15,
            "nearest_prime": prime, "delta": delta, "fit_score": round(fit, 1),
        })

    ratio_fit = None
    ratio_detail = None
    if len(recent_durations) >= 2 and recent_durations[-2] > 0:
        ratio = recent_durations[-1] / recent_durations[-2]
        nearest_ratio = min(PRIME_RATIOS, key=lambda item: abs(ratio - item) / item)
        error_pct = abs(ratio - nearest_ratio) / nearest_ratio * 100.0
        ratio_fit = max(0.0, 100.0 * (1.0 - error_pct / 10.0)) if error_pct <= 10 else 0.0
        ratio_detail = {
            "observed": round(ratio, 4), "prime_ratio": round(nearest_ratio, 4),
            "error_pct": round(error_pct, 2), "fit_score": round(ratio_fit, 1),
        }

    current_index = len(candles) - 1
    last_high = next((p for p in reversed(pivots) if p.get("type") == "HIGH"), None)
    last_low = next((p for p in reversed(pivots) if p.get("type") == "LOW"), None)
    elapsed_high = max(0, current_index - int(last_high["index"])) if last_high else None
    elapsed_low = max(0, current_index - int(last_low["index"])) if last_low else None
    high_prime, high_delta = nearest_prime(elapsed_high) if elapsed_high and elapsed_high > 0 else (None, None)
    low_prime, low_delta = nearest_prime(elapsed_low) if elapsed_low and elapsed_low > 0 else (None, None)

    low_alignment = lows / max(1, len(scale_rows))
    high_alignment = highs / max(1, len(scale_rows))
    if low_alignment >= 0.20 and (high_delta is not None and high_delta <= 2):
        bias = "LOW_TEMPORAL_CONFLUENCE"
    elif high_alignment >= 0.20 and (low_delta is not None and low_delta <= 2):
        bias = "HIGH_TEMPORAL_CONFLUENCE"
    elif low_alignment > high_alignment * 1.35:
        bias = "LOW_ZONE"
    elif high_alignment > low_alignment * 1.35:
        bias = "HIGH_ZONE"
    else:
        bias = "NO_CLEAR_ALIGNMENT"

    fit_pct = round(_mean(fits), 1) if fits else 0.0
    if not scale_rows:
        explanation = "No hay suficiente amplitud de rango para calcular P en las ventanas primas."
    else:
        explanation = (
            f"{lows}/{len(scale_rows)} escalas primas sitúan el cierre en el 23% inferior; "
            f"{highs}/{len(scale_rows)} en el 23% superior. Coincidencia prima media de tramos: {fit_pct:.1f}/100."
        )

    return {
        "status": "OK", "prime_windows": windows, "scales": scale_rows,
        "scale_count": len(scale_rows), "low_scale_count": lows,
        "high_scale_count": highs, "low_alignment_pct": round(low_alignment * 100, 1),
        "high_alignment_pct": round(high_alignment * 100, 1),
        "p_close_median": round(sorted(p_closes)[len(p_closes) // 2], 4) if p_closes else None,
        "completed_leg_count": len(recent_durations), "recent_durations": duration_rows,
        "prime_fit_pct": fit_pct, "duration_ratio": ratio_detail,
        "elapsed_from_last_high": elapsed_high, "elapsed_high_prime": high_prime,
        "elapsed_high_delta": high_delta,
        "elapsed_from_last_low": elapsed_low, "elapsed_low_prime": low_prime,
        "elapsed_low_delta": low_delta, "bias": bias, "explanation": explanation,
    }


def analyze_amplitude_dimension(
    candles: list[dict[str, Any]], pivots: list[dict[str, Any]], atr_values: list[float] | None = None,
) -> dict[str, Any]:
    """Dimension 2: percentage amplitude, ATR-normalized size and wave ratios."""
    if len(pivots) < 3:
        return {
            "status": "INSUFFICIENT_PIVOTS", "legs": [], "latest_leg": None,
            "same_direction_ratio": None, "retracement_ratio": None,
            "phase": "ESPERANDO ONDAS COMPLETAS", "bias": "UNKNOWN",
            "explanation": "Se necesitan al menos tres pivotes confirmados para comparar amplitudes.",
        }

    legs: list[dict[str, Any]] = []
    for start, end in zip(pivots[:-1], pivots[1:]):
        start_price = _f(start.get("price"))
        end_price = _f(end.get("price"))
        if start_price <= 0 or end_price <= 0:
            continue
        delta = end_price - start_price
        duration = max(1, int(end.get("index", 0)) - int(start.get("index", 0)))
        amplitude_pct = abs(delta / start_price) * 100.0
        atr = 0.0
        if atr_values and int(end.get("index", -1)) in range(len(atr_values)):
            atr = atr_values[int(end.get("index", 0))]
        legs.append({
            "direction": "UP" if delta > 0 else "DOWN",
            "start_index": int(start.get("index", 0)), "end_index": int(end.get("index", 0)),
            "duration_bars": duration, "duration_minutes": duration * 15,
            "start_price": round(start_price, 8), "end_price": round(end_price, 8),
            "amplitude_pct": round(amplitude_pct, 5),
            "amplitude_atr": round(abs(delta) / atr, 3) if atr > 0 else None,
            "speed_pct_per_bar": round(amplitude_pct / duration, 6),
        })

    recent = legs[-8:]
    if not recent:
        return {
            "status": "INSUFFICIENT_PIVOTS", "legs": [], "latest_leg": None,
            "same_direction_ratio": None, "retracement_ratio": None,
            "phase": "ESPERANDO ONDAS COMPLETAS", "bias": "UNKNOWN",
            "explanation": "No se pudieron calcular amplitudes válidas.",
        }
    latest = recent[-1]
    previous_same = next((leg for leg in reversed(recent[:-1]) if leg["direction"] == latest["direction"]), None)
    same_ratio = None
    nearest_ratio = None
    ratio_error = None
    if previous_same and previous_same["amplitude_pct"] > 0:
        same_ratio = latest["amplitude_pct"] / previous_same["amplitude_pct"]
        nearest_ratio = min(PRIME_RATIOS, key=lambda item: abs(same_ratio - item) / item)
        ratio_error = abs(same_ratio - nearest_ratio) / nearest_ratio * 100.0

    prior_leg = recent[-2] if len(recent) >= 2 else None
    retracement = None
    retracement_label = "NO_APLICA"
    if prior_leg and prior_leg["direction"] != latest["direction"] and prior_leg["amplitude_pct"] > 0:
        retracement = latest["amplitude_pct"] / prior_leg["amplitude_pct"]
        retracement_label = (
            "RETRACEMENT_SHALLOW" if retracement < 0.382 else
            "RETRACEMENT_MODERATE" if retracement <= 0.618 else
            "RETRACEMENT_DEEP" if retracement <= 0.786 else "RETRACEMENT_NEAR_FULL"
        )

    if same_ratio is None:
        phase = "SIN_COMPARABLE_DIRECTION"
        bias = "UNKNOWN"
    elif same_ratio >= 1.15:
        phase = "EXPANSION_DE_AMPLITUD"
        bias = "UP_STRENGTHENING" if latest["direction"] == "UP" else "DOWN_STRENGTHENING"
    elif same_ratio <= 0.85:
        phase = "CONTRACCION_DE_AMPLITUD"
        bias = "UP_WEAKENING" if latest["direction"] == "UP" else "DOWN_WEAKENING"
    else:
        phase = "AMPLITUD_ESTABLE"
        bias = f"{latest['direction']}_STABLE"

    # Compare the developing leg from the latest pivot to the current close.
    current_price = _f(candles[-1].get("close"))
    last_pivot = pivots[-1]
    pivot_price = _f(last_pivot.get("price"))
    developed = None
    if pivot_price > 0 and current_price > 0:
        delta = current_price - pivot_price
        elapsed = max(0, len(candles) - 1 - int(last_pivot.get("index", len(candles) - 1)))
        developed = {
            "direction": "UP" if delta > 0 else "DOWN" if delta < 0 else "FLAT",
            "elapsed_bars": elapsed,
            "amplitude_pct": round(abs(delta / pivot_price) * 100.0, 5),
            "speed_pct_per_bar": round(abs(delta / pivot_price) * 100.0 / max(1, elapsed), 6),
        }

    nearest_detail = None
    if same_ratio is not None and nearest_ratio is not None:
        nearest_detail = {
            "observed_ratio": round(same_ratio, 4), "nearest_prime_ratio": round(nearest_ratio, 4),
            "error_pct": round(ratio_error or 0.0, 2),
            "aligned_with_prime_ratio": bool((ratio_error or 100.0) <= 7.0),
        }
    explanation_parts = [
        f"Última onda {latest['direction'].lower()}: amplitud {latest['amplitude_pct']:.4f}% en {latest['duration_bars']} velas.",
        f"Estado: {phase.lower().replace('_', ' ')}.",
    ]
    if same_ratio is not None:
        explanation_parts.append(f"Amplitud frente a la onda anterior del mismo sentido: {same_ratio:.3f}×.")
    if retracement is not None:
        explanation_parts.append(f"Retroceso de la onda previa: {retracement * 100:.1f}% ({retracement_label.lower().replace('_', ' ')}).")
    return {
        "status": "OK", "legs": recent, "latest_leg": latest,
        "previous_same_direction_leg": previous_same,
        "same_direction_ratio": round(same_ratio, 4) if same_ratio is not None else None,
        "same_direction_prime_ratio": nearest_detail,
        "retracement_ratio": round(retracement, 4) if retracement is not None else None,
        "retracement_label": retracement_label,
        "developing_leg": developed, "phase": phase, "bias": bias,
        "explanation": " ".join(explanation_parts),
    }


def _behavior_features(segment: list[dict[str, Any]]) -> dict[str, float]:
    closes = [_f(c.get("close")) for c in segment]
    valid = [x for x in closes if x > 0]
    if len(valid) < 3:
        return {}
    returns = [((valid[i] / valid[i - 1]) - 1.0) * 100.0 for i in range(1, len(valid)) if valid[i - 1] > 0]
    if not returns:
        return {}
    path = sum(abs(x) for x in returns)
    net = ((valid[-1] / valid[0]) - 1.0) * 100.0
    ranges = [max(0.0, _f(c.get("high")) - _f(c.get("low"))) / max(_f(c.get("close")), 1e-12) * 100 for c in segment]
    total_range = sum(ranges)
    lower_wicks: list[float] = []
    upper_wicks: list[float] = []
    volumes = [_f(c.get("volume")) for c in segment]
    for candle in segment:
        op = _f(candle.get("open")); hi = _f(candle.get("high")); lo = _f(candle.get("low")); cl = _f(candle.get("close"))
        candle_range = max(hi - lo, 1e-12)
        lower_wicks.append(max(0.0, min(op, cl) - lo) / candle_range)
        upper_wicks.append(max(0.0, hi - max(op, cl)) / candle_range)
    first_vol = _mean([v for v in volumes[:4] if v > 0])
    last_vol = _mean([v for v in volumes[-4:] if v > 0])
    vol_ratio = last_vol / first_vol if first_vol > 0 and last_vol > 0 else 1.0
    first_momentum = _mean(returns[:4])
    last_momentum = _mean(returns[-4:])
    variance = _mean([(x - _mean(returns)) ** 2 for x in returns])
    return {
        "net_return_pct": net,
        "path_pct": path,
        "efficiency": abs(net) / path if path > 0 else 0.0,
        "up_fraction": sum(1 for x in returns if x > 0) / len(returns),
        "volatility_pct": sqrt(variance),
        "range_pct": total_range / len(ranges) if ranges else 0.0,
        "lower_wick_share": _mean(lower_wicks),
        "upper_wick_share": _mean(upper_wicks),
        "momentum_acceleration": last_momentum - first_momentum,
        "volume_ratio_log": max(-2.0, min(2.0, __import__("math").log(max(1e-9, vol_ratio)))),
    }


_BEHAVIOR_FEATURE_SCALES = {
    "net_return_pct": 0.8, "path_pct": 1.2, "efficiency": 0.35,
    "up_fraction": 0.30, "volatility_pct": 0.45, "range_pct": 1.2,
    "lower_wick_share": 0.30, "upper_wick_share": 0.30,
    "momentum_acceleration": 0.45, "volume_ratio_log": 0.65,
}


def _behavior_distance(left: dict[str, float], right: dict[str, float]) -> float:
    keys = list(_BEHAVIOR_FEATURE_SCALES)
    parts = [((left.get(key, 0.0) - right.get(key, 0.0)) / scale) ** 2 for key, scale in _BEHAVIOR_FEATURE_SCALES.items()]
    return sqrt(sum(parts) / max(1, len(keys)))


def analyze_behavior_dimension(
    candles: list[dict[str, Any]], pattern_bars: int = 13,
    horizons: tuple[int, ...] = (4, 8, 12), max_analogs: int = 6,
) -> dict[str, Any]:
    """Dimension 3: waveform fingerprint and strictly past, outcome-labelled analogs.

    Historical analog features are computed only from candles before each sample's
    outcome. Every analog's full forward outcome must end before the current pattern
    begins, preventing future leakage into the live comparison.
    """
    if len(candles) < pattern_bars * 3 + max(horizons):
        return {
            "status": "INSUFFICIENT_HISTORY", "pattern_bars": pattern_bars,
            "phase": "HISTORIAL_INSUFICIENTE", "fingerprint": {}, "analogs": {},
            "explanation": "Se necesita más historial para buscar ondas históricas comparables.",
        }

    current_start = len(candles) - pattern_bars
    current_features = _behavior_features(candles[current_start:])
    if not current_features:
        return {
            "status": "INVALID_PATTERN", "pattern_bars": pattern_bars,
            "phase": "HUELLA_NO_DISPONIBLE", "fingerprint": {}, "analogs": {},
            "explanation": "No se pudo construir la huella de comportamiento.",
        }

    net = current_features["net_return_pct"]
    efficiency = current_features["efficiency"]
    up_fraction = current_features["up_fraction"]
    lower_wick = current_features["lower_wick_share"]
    upper_wick = current_features["upper_wick_share"]
    acceleration = current_features["momentum_acceleration"]
    if abs(net) < 0.08 and efficiency < 0.22:
        phase = "RANGE_CHOP"
    elif net > 0 and efficiency >= 0.42 and up_fraction >= 0.58:
        phase = "TREND_UP_CONTINUATION"
    elif net < 0 and efficiency >= 0.42 and up_fraction <= 0.42:
        phase = "TREND_DOWN_CONTINUATION"
    elif net > 0 and acceleration < 0 and upper_wick > lower_wick + 0.10:
        phase = "POSSIBLE_UP_EXHAUSTION"
    elif net < 0 and acceleration > 0 and lower_wick > upper_wick + 0.10:
        phase = "POSSIBLE_DOWN_EXHAUSTION"
    else:
        phase = "MIXED_TRANSITION"

    analog_results: dict[str, Any] = {}
    aggregate_dirs: list[str] = []
    for horizon in horizons:
        candidates: list[dict[str, Any]] = []
        # end is exclusive; the pattern outcome must finish before current_start.
        max_end = current_start - horizon
        for end in range(pattern_bars, max_end + 1, 2):
            segment = candles[end - pattern_bars:end]
            features = _behavior_features(segment)
            if not features:
                continue
            base = _f(candles[end - 1].get("close"))
            future = _f(candles[end + horizon - 1].get("close"))
            if base <= 0 or future <= 0:
                continue
            distance = _behavior_distance(current_features, features)
            similarity = 100.0 * __import__("math").exp(-distance)
            candidates.append({
                "end_index": end - 1,
                "similarity_pct": similarity,
                "forward_return_pct": (future / base - 1.0) * 100.0,
            })

        candidates.sort(key=lambda item: item["similarity_pct"], reverse=True)
        selected: list[dict[str, Any]] = []
        for candidate in candidates:
            if all(abs(candidate["end_index"] - picked["end_index"]) >= pattern_bars for picked in selected):
                selected.append(candidate)
            if len(selected) >= max_analogs:
                break

        if len(selected) < 3:
            analog_results[str(horizon)] = {
                "sample_count": len(selected), "direction": "INSUFFICIENT",
                "weighted_return_pct": None, "positive_share_pct": None,
                "similarity_mean_pct": round(_mean([x["similarity_pct"] for x in selected]), 1) if selected else 0.0,
                "cases": selected,
            }
            continue

        weights = [max(1e-6, item["similarity_pct"] / 100.0) ** 2 for item in selected]
        weight_sum = sum(weights)
        weighted_return = sum(item["forward_return_pct"] * weight for item, weight in zip(selected, weights)) / weight_sum
        positive_share = sum(weight for item, weight in zip(selected, weights) if item["forward_return_pct"] > 0) / weight_sum * 100.0
        mean_similarity = _mean([x["similarity_pct"] for x in selected])
        direction = "UP" if positive_share >= 60 and weighted_return > 0.02 else "DOWN" if positive_share <= 40 and weighted_return < -0.02 else "MIXED"
        if direction in {"UP", "DOWN"}:
            aggregate_dirs.append(direction)
        analog_results[str(horizon)] = {
            "sample_count": len(selected), "direction": direction,
            "weighted_return_pct": round(weighted_return, 5),
            "positive_share_pct": round(positive_share, 1),
            "similarity_mean_pct": round(mean_similarity, 1),
            "cases": [
                {"end_index": item["end_index"], "similarity_pct": round(item["similarity_pct"], 1),
                 "forward_return_pct": round(item["forward_return_pct"], 5)}
                for item in selected
            ],
        }

    if net > 0.03 and efficiency >= 0.35:
        direction_now = "UP"
    elif net < -0.03 and efficiency >= 0.35:
        direction_now = "DOWN"
    else:
        direction_now = "RANGE"
    if phase == "POSSIBLE_UP_EXHAUSTION":
        bias = "REVERSAL_RISK_UP"
    elif phase == "POSSIBLE_DOWN_EXHAUSTION":
        bias = "REVERSAL_RISK_DOWN"
    elif len(aggregate_dirs) >= 2 and aggregate_dirs.count("UP") >= 2:
        bias = "HISTORICAL_ANALOGS_UP"
    elif len(aggregate_dirs) >= 2 and aggregate_dirs.count("DOWN") >= 2:
        bias = "HISTORICAL_ANALOGS_DOWN"
    elif direction_now in {"UP", "DOWN"}:
        bias = f"CURRENT_PATTERN_{direction_now}"
    else:
        bias = "MIXED"

    mean_samples = [v.get("similarity_mean_pct", 0) for v in analog_results.values() if v.get("sample_count", 0) >= 3]
    explanation = (
        f"Huella de {pattern_bars} velas: retorno {net:+.4f}%, eficiencia {efficiency:.2f}, "
        f"velas alcistas {up_fraction * 100:.1f}%, fase {phase}. "
        f"Analogías válidas: {len(mean_samples)} horizontes con al menos 3 casos."
    )
    return {
        "status": "OK", "pattern_bars": pattern_bars,
        "current_pattern_start_index": current_start,
        "fingerprint": {key: round(value, 6) for key, value in current_features.items()},
        "phase": phase, "current_direction": direction_now, "bias": bias,
        "analogs": analog_results,
        "average_analogy_similarity_pct": round(_mean(mean_samples), 1) if mean_samples else 0.0,
        "explanation": explanation,
        "no_lookahead": True,
    }


def synthesize_wave_dimensions(
    temporal: dict[str, Any], amplitude: dict[str, Any], behavior: dict[str, Any],
) -> dict[str, Any]:
    """Synthesize directional clues while keeping location and direction distinct."""
    observations: list[str] = []
    bullish: list[str] = []
    bearish: list[str] = []

    temporal_bias = str(temporal.get("bias", ""))
    amplitude_bias = str(amplitude.get("bias", ""))
    behavior_bias = str(behavior.get("bias", ""))
    behavior_phase = str(behavior.get("phase", ""))

    if temporal_bias in {"LOW_TEMPORAL_CONFLUENCE", "LOW_ZONE"}:
        bullish.append("TEMPORAL_LOW_ZONE")
        observations.append("La dimensión temporal concentra el precio en zonas bajas de varias ventanas primas; esto indica ubicación, no giro confirmado.")
    elif temporal_bias in {"HIGH_TEMPORAL_CONFLUENCE", "HIGH_ZONE"}:
        bearish.append("TEMPORAL_HIGH_ZONE")
        observations.append("La dimensión temporal concentra el precio en zonas altas de varias ventanas primas; esto indica ubicación, no giro confirmado.")

    if amplitude_bias == "DOWN_WEAKENING":
        bullish.append("SELL_WAVES_CONTRACTING")
        observations.append("Las ondas bajistas se contraen frente a ondas bajistas anteriores; la presión vendedora pierde amplitud.")
    elif amplitude_bias == "UP_WEAKENING":
        bearish.append("BUY_WAVES_CONTRACTING")
        observations.append("Las ondas alcistas se contraen frente a ondas alcistas anteriores; la presión compradora pierde amplitud.")
    elif amplitude_bias == "DOWN_STRENGTHENING":
        bearish.append("SELL_WAVES_EXPANDING")
        observations.append("Las ondas bajistas se están ampliando: continuidad bajista posible.")
    elif amplitude_bias == "UP_STRENGTHENING":
        bullish.append("BUY_WAVES_EXPANDING")
        observations.append("Las ondas alcistas se están ampliando: continuidad alcista posible.")

    if behavior_bias == "HISTORICAL_ANALOGS_UP" or behavior_phase == "TREND_UP_CONTINUATION":
        bullish.append("BEHAVIOR_UP")
        observations.append("La huella de comportamiento favorece una continuación alcista o sus analogías históricas apuntan arriba.")
    elif behavior_bias == "HISTORICAL_ANALOGS_DOWN" or behavior_phase == "TREND_DOWN_CONTINUATION":
        bearish.append("BEHAVIOR_DOWN")
        observations.append("La huella de comportamiento favorece una continuación bajista o sus analogías históricas apuntan abajo.")
    elif behavior_bias == "REVERSAL_RISK_UP":
        bearish.append("UPSIDE_EXHAUSTION_RISK")
        observations.append("La huella alcista muestra rasgos de agotamiento; falta confirmar rechazo en resistencia.")
    elif behavior_bias == "REVERSAL_RISK_DOWN":
        bullish.append("DOWNSIDE_EXHAUSTION_RISK")
        observations.append("La huella bajista muestra rasgos de agotamiento; falta confirmar rechazo en soporte.")

    interpretation = "SIN_CONFLUENCIA"
    if len(bullish) >= 2 and len(bearish) >= 2:
        interpretation = "DIMENSIONES_EN_CONFLICTO"
    elif len(bullish) >= 2:
        interpretation = "CONFLUENCIA_ALCISTA"
    elif len(bearish) >= 2:
        interpretation = "CONFLUENCIA_BAJISTA"
    elif bullish or bearish:
        interpretation = "CONFLUENCIA_PARCIAL"
    if not observations:
        observations.append("Las tres dimensiones todavía no presentan una coincidencia direccional clara.")
    conflicts = []
    if len(bullish) >= 2 and len(bearish) >= 2:
        conflicts.append("Hay al menos dos pistas alcistas y dos bajistas: se debe evitar tratar la lectura como una dirección única.")
    return {
        "observations": observations,
        "bullish_evidence": bullish,
        "bearish_evidence": bearish,
        "bullish_count": len(bullish),
        "bearish_count": len(bearish),
        "conflicts": conflicts,
        "interpretation": interpretation,
        "warning": "Las coincidencias primas y las analogías históricas son descriptores exploratorios; no son probabilidades calibradas ni garantizan repetición.",
    }


def analyze(
    source_candles: list[dict[str, Any]],
    live_price: float | None = None,
    threshold: float = BASE_THRESHOLD,
    include_3d: bool = True,
    adaptive_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Analyze latest 15m structure and three independent wave dimensions."""
    if len(source_candles) < 35:
        return {
            "signal": "NEUTRAL", "candidate": None, "phase": "REUNIENDO HISTORIAL",
            "long_score": 0, "short_score": 0, "threshold": threshold,
            "reasons": ["Se necesitan más velas para analizar las escalas temporales."],
            "pivots": [], "rsi": 50.0, "atr_pct": 0.0,
            "bottom_zone": False, "bottom_rebound": False,
            "bottom_bounce_confirmed": False, "lower_wick_ratio": 0.0,
            "support_rejection_confirmed": False,
            "resistance_zone": False, "resistance_rejection_confirmed": False,
            "lower_wick_ratio": 0.0, "upper_wick_ratio": 0.0,
            "momentum_long": False, "momentum_short": False,
            "wave_dimensions": {
                "temporal": {"status": "INSUFFICIENT_DATA"},
                "amplitude": {"status": "INSUFFICIENT_PIVOTS"},
                "behavior": {"status": "INSUFFICIENT_HISTORY"},
                "synthesis": {"interpretation": "SIN_CONFLUENCIA", "observations": []},
            },
        }

    # A bounded working set keeps the algorithm fast even with a large SQLite history.
    candles = [dict(c) for c in source_candles[-300:]]
    if live_price and live_price > 0:
        last = candles[-1]
        last["high"] = max(_f(last.get("high")), live_price)
        last["low"] = min(_f(last.get("low")), live_price)
        last["close"] = live_price
    closes = [_f(c.get("close")) for c in candles]
    current = closes[-1]
    if current <= 0:
        current = _f(live_price)
    rsi_values = _rsi_series(closes)
    atr_values = _atr_series(candles)
    rsi = rsi_values[-1] if rsi_values else 50.0
    atr = atr_values[-1] if atr_values else 0.0
    atr_pct = (atr / current * 100.0) if current else 0.0
    pivots = detect_pivots(candles, rsi_values)
    if include_3d:
        temporal_dimension = analyze_temporal_dimension(candles, pivots)
        amplitude_dimension = analyze_amplitude_dimension(candles, pivots, atr_values)
        behavior_dimension = analyze_behavior_dimension(candles)
        wave_dimensions = {
            "temporal": temporal_dimension,
            "amplitude": amplitude_dimension,
            "behavior": behavior_dimension,
        }
        wave_dimensions["synthesis"] = synthesize_wave_dimensions(
            temporal_dimension, amplitude_dimension, behavior_dimension
        )
    else:
        wave_dimensions = {
            "temporal": {"status": "SKIPPED"},
            "amplitude": {"status": "SKIPPED"},
            "behavior": {"status": "SKIPPED"},
            "synthesis": {"interpretation": "SKIPPED", "observations": []},
        }

    zone_long, zone_short, long_windows, short_windows, support, resistance = _zone_scores(candles, current)
    long_score = zone_long
    short_score = zone_short
    long_reasons: list[str] = []
    short_reasons: list[str] = []
    if long_windows >= 3:
        long_reasons.append(f"Confluencia de zona baja en {long_windows} escalas temporales primas")
    if short_windows >= 3:
        short_reasons.append(f"Confluencia de zona alta en {short_windows} escalas temporales primas")

    # RSI is a context feature. It cannot trigger a signal by itself.
    if rsi <= 28:
        long_score += 14
        long_reasons.append("RSI en sobreventa profunda")
    elif rsi <= 35:
        long_score += 10
        long_reasons.append("RSI en zona baja")
    elif rsi <= 42:
        long_score += 5
    if rsi >= 72:
        short_score += 14
        short_reasons.append("RSI en sobrecompra profunda")
    elif rsi >= 65:
        short_score += 10
        short_reasons.append("RSI en zona alta")
    elif rsi >= 58:
        short_score += 5

    last = candles[-1]
    open_ = _f(last.get("open"))
    high = _f(last.get("high"))
    low = _f(last.get("low"))
    close = _f(last.get("close"))
    candle_range = max(high - low, 1e-12)
    body_top = max(open_, close)
    body_bottom = min(open_, close)
    lower_wick = max(0.0, body_bottom - low)
    upper_wick = max(0.0, high - body_top)
    lower_ratio = lower_wick / candle_range
    upper_ratio = upper_wick / candle_range
    close_location = (close - low) / candle_range

    if lower_ratio >= 0.32 and close_location >= 0.55:
        add = 15 if lower_ratio >= 0.48 else 10
        long_score += add
        long_reasons.append("Rechazo de precios bajos: mecha inferior")
    if upper_ratio >= 0.32 and close_location <= 0.45:
        add = 15 if upper_ratio >= 0.48 else 10
        short_score += add
        short_reasons.append("Rechazo de precios altos: mecha superior")

    # A small shift in short-term momentum is required before an alert is confirmed.
    momentum_long = False
    momentum_short = False
    if len(closes) >= 4:
        d1 = closes[-1] - closes[-2]
        d2 = closes[-2] - closes[-3]
        d3 = closes[-3] - closes[-4]
        if d1 > 0 and (d2 <= 0 or d1 > abs(d2) * 0.45):
            long_score += 10
            momentum_long = True
            long_reasons.append("El impulso corto empieza a girar al alza")
        elif d1 > 0:
            long_score += 4
            momentum_long = True
        if d1 < 0 and (d2 >= 0 or abs(d1) > abs(d2) * 0.45):
            short_score += 10
            momentum_short = True
            short_reasons.append("El impulso corto empieza a girar a la baja")
        elif d1 < 0:
            short_score += 4
            momentum_short = True

    long_div = _divergence(pivots, "LONG")
    short_div = _divergence(pivots, "SHORT")
    if long_div:
        long_score += 9
        long_reasons.append("Divergencia alcista entre precio y RSI")
    if short_div:
        short_score += 9
        short_reasons.append("Divergencia bajista entre precio y RSI")

    long_time, long_elapsed, long_prime, long_delta = _temporal_prime_score(pivots, "LONG", len(candles) - 1)
    short_time, short_elapsed, short_prime, short_delta = _temporal_prime_score(pivots, "SHORT", len(candles) - 1)
    long_score += long_time
    short_score += short_time
    dimension_adjustments = {
        "LONG": 0.0, "SHORT": 0.0, "reasons": [],
        "by_dimension": {}, "weights_used": {},
    }
    if include_3d:
        weights = adaptive_weights or {}
        weights = {
            name: max(0.85, min(1.15, _f(weights.get(name, 1.0), 1.0)))
            for name in ("temporal", "amplitude", "behavior")
        }
        components = {
            "temporal": {"LONG": 0.0, "SHORT": 0.0},
            "amplitude": {"LONG": 0.0, "SHORT": 0.0},
            "behavior": {"LONG": 0.0, "SHORT": 0.0},
        }
        if temporal_dimension.get("elapsed_high_delta") is not None and temporal_dimension.get("elapsed_high_delta") <= 1 and temporal_dimension.get("low_alignment_pct", 0) >= 20:
            components["temporal"]["LONG"] += 2.0
            dimension_adjustments["reasons"].append("Tiempo desde el último máximo cerca de un primo y confluencia de zona baja (+2 LONG).")
        if temporal_dimension.get("elapsed_low_delta") is not None and temporal_dimension.get("elapsed_low_delta") <= 1 and temporal_dimension.get("high_alignment_pct", 0) >= 20:
            components["temporal"]["SHORT"] += 2.0
            dimension_adjustments["reasons"].append("Tiempo desde el último mínimo cerca de un primo y confluencia de zona alta (+2 SHORT).")
        amplitude_bias = amplitude_dimension.get("bias")
        if amplitude_bias == "DOWN_WEAKENING":
            components["amplitude"]["LONG"] += 3.0
            dimension_adjustments["reasons"].append("Las ondas bajistas reducen amplitud respecto a sus pares previos (+3 LONG).")
        elif amplitude_bias == "UP_WEAKENING":
            components["amplitude"]["SHORT"] += 3.0
            dimension_adjustments["reasons"].append("Las ondas alcistas reducen amplitud respecto a sus pares previos (+3 SHORT).")
        elif amplitude_bias == "DOWN_STRENGTHENING":
            components["amplitude"]["SHORT"] += 2.0
            dimension_adjustments["reasons"].append("Las ondas bajistas amplían su recorrido (+2 SHORT).")
        elif amplitude_bias == "UP_STRENGTHENING":
            components["amplitude"]["LONG"] += 2.0
            dimension_adjustments["reasons"].append("Las ondas alcistas amplían su recorrido (+2 LONG).")
        behavior_analogs = behavior_dimension.get("analogs", {})
        up_horizons = sum(1 for row in behavior_analogs.values() if row.get("direction") == "UP")
        down_horizons = sum(1 for row in behavior_analogs.values() if row.get("direction") == "DOWN")
        if up_horizons >= 2:
            components["behavior"]["LONG"] += 4.0
            dimension_adjustments["reasons"].append("Analogías históricas similares favorecen subida en al menos dos horizontes (+4 LONG).")
        elif down_horizons >= 2:
            components["behavior"]["SHORT"] += 4.0
            dimension_adjustments["reasons"].append("Analogías históricas similares favorecen bajada en al menos dos horizontes (+4 SHORT).")

        for name, values in components.items():
            weighted_long = values["LONG"] * weights[name]
            weighted_short = values["SHORT"] * weights[name]
            dimension_adjustments["by_dimension"][name] = {
                "raw_long": round(values["LONG"], 3),
                "raw_short": round(values["SHORT"], 3),
                "weight": round(weights[name], 4),
                "weighted_long": round(weighted_long, 3),
                "weighted_short": round(weighted_short, 3),
            }
            dimension_adjustments["LONG"] += weighted_long
            dimension_adjustments["SHORT"] += weighted_short
        # The learned weights can tilt evidence slightly but can never let the
        # three exploratory dimensions contribute more than six points per side.
        dimension_adjustments["LONG"] = round(min(6.0, dimension_adjustments["LONG"]), 3)
        dimension_adjustments["SHORT"] = round(min(6.0, dimension_adjustments["SHORT"]), 3)
        dimension_adjustments["weights_used"] = weights
        if any(abs(weights[name] - 1.0) > 0.0001 for name in weights):
            dimension_adjustments["reasons"].append(
                "Los pesos temporales, de amplitud y comportamiento se ajustaron con correlaciones walk-forward limitadas (±15%)."
            )
        long_score += dimension_adjustments["LONG"]
        short_score += dimension_adjustments["SHORT"]
    if long_prime is not None and long_delta is not None and long_delta <= 2:
        long_reasons.append(f"Resonancia temporal: {long_elapsed} velas, primo cercano {long_prime}")
    if short_prime is not None and short_delta is not None and short_delta <= 2:
        short_reasons.append(f"Resonancia temporal: {short_elapsed} velas, primo cercano {short_prime}")

    long_score = round(min(100.0, long_score), 1)
    short_score = round(min(100.0, short_score), 1)

    long_candidate = long_score >= 52 and long_score >= short_score + 7
    short_candidate = short_score >= 52 and short_score >= long_score + 7
    long_reversal = momentum_long or lower_ratio >= 0.32 or long_div
    short_reversal = momentum_short or upper_ratio >= 0.32 or short_div

    signal = "NEUTRAL"
    candidate = "LONG" if long_candidate else "SHORT" if short_candidate else None
    phase = "SIN EXTREMO CLARO"
    if long_candidate:
        phase = "POSIBLE PISO · ESPERANDO CONFIRMACIÓN"
        if long_score >= threshold and long_score >= short_score + 11 and long_reversal:
            signal = "LONG"
            phase = "GIRO ALCISTA DETECTADO"
    elif short_candidate:
        phase = "POSIBLE TECHO · ESPERANDO CONFIRMACIÓN"
        if short_score >= threshold and short_score >= long_score + 11 and short_reversal:
            signal = "SHORT"
            phase = "GIRO BAJISTA DETECTADO"
    if signal == "NEUTRAL" and candidate is None and max(long_score, short_score) >= 45:
        phase = "TRANSICIÓN / MOVIMIENTO SIN EXTREMO"

    latest_pivots = []
    for p in pivots[-12:]:
        latest_pivots.append({
            "type": p["type"], "price": round(p["price"], 6), "time": p["time"],
            "duration_bars": p.get("duration_bars"), "nearest_prime": p.get("nearest_prime"),
            "prime_delta": p.get("prime_delta"), "duration_minutes": p.get("duration_minutes"),
        })

    # Explanations show the dominant evidence; low scores remain visible as a neutral reading.
    dominant_reasons = long_reasons if long_score >= short_score else short_reasons
    if not dominant_reasons:
        dominant_reasons = ["No hay coincidencia suficiente entre extremos, impulso y estructura temporal."]
    wick_analysis = analyze_wick_context(
        candles=candles,
        pivots=pivots,
        current_price=current,
        atr=atr,
        rsi=rsi,
        momentum_long=momentum_long,
        momentum_short=momentum_short,
        long_divergence=long_div,
        short_divergence=short_div,
        prime_long_delta=long_delta,
        prime_short_delta=short_delta,
    )
    support_zone = bool(
        long_windows >= 2 or wick_analysis.get("bottom_scale_count", 0) >= 2
        or (support is not None and atr > 0 and 0 <= (current - support) <= atr * 1.5)
    )
    resistance_zone = bool(
        short_windows >= 2 or wick_analysis.get("top_scale_count", 0) >= 2
        or (resistance is not None and atr > 0 and 0 <= (resistance - current) <= atr * 1.5)
    )
    support_rejection = bool(wick_analysis.get("lower", {}).get("rejection_confirmed", False))
    resistance_rejection = bool(wick_analysis.get("upper", {}).get("rejection_confirmed", False))

    return {
        "signal": signal,
        "candidate": candidate,
        "phase": phase,
        "long_score": long_score,
        "short_score": short_score,
        "threshold": round(float(threshold), 1),
        "rsi": round(rsi, 1),
        "atr": round(atr, 6),
        "atr_pct": round(atr_pct, 3),
        "current_price": round(current, 8),
        "zone_long_windows": long_windows,
        "zone_short_windows": short_windows,
        "support_31": round(support, 6) if support is not None else None,
        "resistance_31": round(resistance, 6) if resistance is not None else None,
        "prime_long": {"elapsed": long_elapsed, "nearest": long_prime, "delta": long_delta},
        "prime_short": {"elapsed": short_elapsed, "nearest": short_prime, "delta": short_delta},
        "bottom_zone": support_zone,
        "resistance_zone": resistance_zone,
        "bottom_rebound": bool(
            wick_analysis.get("lower", {}).get("shape", False)
            and wick_analysis.get("lower", {}).get("location_confirmed", False)
        ),
        "bottom_bounce_confirmed": support_rejection,
        "support_rejection_confirmed": support_rejection,
        "resistance_rejection_confirmed": resistance_rejection,
        "lower_wick_ratio": round(lower_ratio, 4),
        "upper_wick_ratio": round(upper_ratio, 4),
        "wick_analysis": wick_analysis,
        "wave_dimensions": wave_dimensions,
        "dimension_adjustments": dimension_adjustments,
        "momentum_long": bool(momentum_long),
        "momentum_short": bool(momentum_short),
        "reasons": dominant_reasons[:4],
        "long_reasons": long_reasons[:5],
        "short_reasons": short_reasons[:5],
        "pivots": latest_pivots,
        "last_candle_time": int(last.get("open_time", 0)),
    }


def backtest(candles: list[dict[str, Any]], threshold: float = BASE_THRESHOLD, horizon: int = 4) -> list[dict[str, Any]]:
    """Walk-forward historical evaluation. Each signal sees candles available at its time only."""
    if len(candles) < 180 + horizon:
        return []
    results: list[dict[str, Any]] = []
    last_signal_index = -10_000
    start = max(140, len(candles) - 1800)
    end = len(candles) - horizon
    for i in range(start, end):
        if i - last_signal_index < 4:
            continue
        view = candles[max(0, i - 299): i + 1]
        result = analyze(view, live_price=_f(candles[i].get("close")), threshold=threshold, include_3d=False)
        side = result["signal"]
        if side not in ("LONG", "SHORT"):
            continue
        entry = _f(candles[i].get("close"))
        future = candles[i + 1: i + horizon + 1]
        if entry <= 0 or len(future) < horizon:
            continue
        exit_price = _f(future[-1].get("close"))
        direction = 1.0 if side == "LONG" else -1.0
        ret = direction * (exit_price / entry - 1.0) * 100.0
        if side == "LONG":
            mfe = (max(_f(c.get("high")) for c in future) / entry - 1.0) * 100.0
            mae = (min(_f(c.get("low")) for c in future) / entry - 1.0) * 100.0
        else:
            mfe = (entry / min(_f(c.get("low")) for c in future) - 1.0) * 100.0
            mae = (entry / max(_f(c.get("high")) for c in future) - 1.0) * 100.0
        results.append({
            "bar_time": int(candles[i].get("open_time", 0)),
            "side": side,
            "score": max(result["long_score"], result["short_score"]),
            "entry": entry,
            "return_pct": ret,
            "mfe_pct": mfe,
            "mae_pct": mae,
            "correct": 1 if ret > 0 else 0,
        })
        last_signal_index = i
    return results
