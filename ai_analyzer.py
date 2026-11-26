"""OpenRouter market-review layer for PRIMA-15.

The deterministic wave engine remains the source of market features. Models receive
a complete, timestamped feature snapshot and return constrained assessments for trend continuity
or support/resistance exceptions, including explicit coverage classifications for every evidence family. They never receive credentials and cannot execute live
orders; their decisions only manage the local paper portfolio.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import requests

from config import AI_MIN_CONFIDENCE, FAST_ENTRY_ENFORCE_GATE

log = logging.getLogger("prima15.ai")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MESSAGES_URL = "https://openrouter.ai/api/v1/messages"
OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"

# Models selected from the user's curated free-model list. The first committee
# combines a typed decision model with two conversational models.
DEFAULT_PRIMARY_MODELS = (
    "perplexity/pplx-decider-flash-v1.3:free",
    "nvidia/nemotron-3.5-lightning:free",
)
DEFAULT_FALLBACK_MODELS = (
    "baidu/ernie-5.5-flash-preview:free",
    "perplexity/pplx-decider-v1.3:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "recursive/flint-v1.0:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "particle-ai/particle-1:free",
    "liquid/lfm-3-1.7b:free",
)

DECISION_MODELS = {
    "perplexity/pplx-decider-flash-v1.3:free",
    "perplexity/pplx-decider-v1.3:free",
}
REASONING_BUDGET_MODELS = {
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
}

# Every conversational model must explicitly classify these context sections.
# Perplexity Decider receives the same checklist as independent typed questions.
EVIDENCE_OPTIONS = {
    # Cada respuesta debe clasificar explícitamente cada bloque que el sistema produce.
    "market": ["RISING", "FALLING", "MIXED", "UNAVAILABLE"],
    "cross_market": ["SUPPORTS_LONG", "SUPPORTS_SHORT", "MIXED", "NEUTRAL", "INSUFFICIENT"],
    "raw_candles": ["UP", "DOWN", "RANGE", "INSUFFICIENT"],
    "engine": ["LONG_BIAS", "SHORT_BIAS", "MIXED", "NEUTRAL", "INSUFFICIENT"],
    "policy": ["ALIGNED", "CONFLICT", "INSUFFICIENT"],
    "temporal": ["LOW_ZONE", "HIGH_ZONE", "MIXED", "NEUTRAL", "INSUFFICIENT"],
    "prime": ["LOW_ALIGNMENT", "HIGH_ALIGNMENT", "BOTH", "NO_ALIGNMENT", "INSUFFICIENT"],
    "amplitude": ["UP_EXPANDING", "UP_CONTRACTING", "DOWN_EXPANDING", "DOWN_CONTRACTING", "MIXED", "INSUFFICIENT"],
    "behavior": ["FAVORS_UP", "FAVORS_DOWN", "MIXED", "INSUFFICIENT"],
    "wick": ["LOWER_REJECTION", "UPPER_REJECTION", "NO_CONFIRMED_REJECTION", "CONFLICT", "INSUFFICIENT"],
    "trend": ["UP", "DOWN", "RANGE", "INSUFFICIENT"],
    "signals": ["SUPPORTS_LONG", "SUPPORTS_SHORT", "MIXED", "INSUFFICIENT"],
    "backtest": ["SUPPORTS_LONG", "SUPPORTS_SHORT", "MIXED", "INSUFFICIENT"],
    "prior_memory": ["FAVORS_LONG", "FAVORS_SHORT", "MIXED", "INSUFFICIENT"],
    "portfolio": ["POSITION_LONG", "POSITION_SHORT", "FLAT", "RISK_REVIEW", "UNAVAILABLE"],
    "learning": ["IMPROVING", "DETERIORATING", "MIXED", "INSUFFICIENT"],
    "adaptive_profile": ["FAVORS_LONG", "FAVORS_SHORT", "MIXED", "INSUFFICIENT"],
    "fast_entry": ["LONG_SETUP", "SHORT_SETUP", "NO_EDGE", "MIXED", "INSUFFICIENT"],
    "elliott": ["BULLISH_IMPULSE", "BEARISH_IMPULSE", "BULLISH_CORRECTION", "BEARISH_CORRECTION", "POSSIBLE_DIAGONAL", "POSSIBLE_TRIANGLE", "MIXED", "NO_CLEAR_COUNT", "INSUFFICIENT"],
    "elliott_prime": ["CONFLUENCE_UP", "CONFLUENCE_DOWN", "DIRECTION_CONFLICT", "PRIME_TIME_FIT_ONLY", "NO_CONFLUENCE", "INSUFFICIENT"],
}


def _requires_evidence_coverage() -> bool:
    return os.getenv("LLM_REQUIRE_EVIDENCE_COVERAGE", "true").strip().lower() not in {"0", "false", "no", "off"}


def _evidence_coverage(assessments: dict[str, Any]) -> tuple[list[str], float]:
    missing: list[str] = []
    for key, allowed in EVIDENCE_OPTIONS.items():
        item = assessments.get(key) if isinstance(assessments, dict) else None
        if not isinstance(item, dict):
            missing.append(key)
            continue
        reading = str(item.get("reading", "")).strip().upper()
        basis = str(item.get("basis", "")).strip()
        if reading not in allowed or len(basis) < 12:
            missing.append(key)
    coverage = (len(EVIDENCE_OPTIONS) - len(missing)) / len(EVIDENCE_OPTIONS) * 100.0
    return missing, round(coverage, 1)


def _validate_evidence_coverage(assessments: dict[str, Any], model: str) -> None:
    if not _requires_evidence_coverage():
        return
    missing, coverage = _evidence_coverage(assessments)
    if missing:
        raise RuntimeError(
            f"Evaluación incompleta del modelo {model}: cobertura {coverage:.0f}% y faltan secciones "
            + ", ".join(missing) + ". Se probará un modelo de respaldo."
        )


def _parse_model_list(raw: str, limit: int) -> list[str]:
    parts = [part.strip() for part in re.split(r"[,;\n]", raw) if part.strip()]
    unique: list[str] = []
    for model in parts:
        if model not in unique:
            unique.append(model)
    return unique[:limit]


def configured_models() -> list[str]:
    # Replace the previous app default automatically, while respecting custom lists.
    raw = os.getenv("OPENROUTER_MODELS", ",".join(DEFAULT_PRIMARY_MODELS)).strip()
    if not raw or raw.lower() == "openrouter/free":
        raw = ",".join(DEFAULT_PRIMARY_MODELS)
    parsed = _parse_model_list(raw, 3)
    # Keep the two decision roles available even when an older .env contains only
    # one general model. The first slot is the exception/rejection reader; the
    # second is the trend-continuation reader.
    if len(parsed) == 1:
        parsed.append(DEFAULT_PRIMARY_MODELS[1] if parsed[0] != DEFAULT_PRIMARY_MODELS[1] else DEFAULT_PRIMARY_MODELS[0])
    return parsed or list(DEFAULT_PRIMARY_MODELS)


def configured_fallback_models() -> list[str]:
    """Backups replace failed votes; they never add extra committee votes."""
    raw = os.getenv("OPENROUTER_FALLBACK_MODELS", ",".join(DEFAULT_FALLBACK_MODELS)).strip()
    # Migrate the exact legacy fallback set from the previous release.
    legacy = {
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "baidu/ernie-5.5-flash-preview:free",
        "inclusionai/ling-3.2-flash:free",
    }
    parsed = _parse_model_list(raw, 10)
    if set(parsed) == legacy:
        parsed = list(DEFAULT_FALLBACK_MODELS)
    primary = set(configured_models())
    return [m for m in parsed if m not in primary]


def api_key_present() -> bool:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    return bool(key and "PEGA_TU_CLAVE_AQUI" not in key.upper() and "TU_CLAVE_AQUI" not in key.upper())


def initial_ai_state() -> dict[str, Any]:
    if not api_key_present():
        return {
            "status": "not_configured", "decision": "WAIT", "confidence": 0,
            "consensus_pct": 0, "votes": [], "summary": "Configura OPENROUTER_API_KEY en el archivo .env para activar el análisis de modelos.",
            "risk": "Sin API key no se abre ni se invierte ninguna posición simulada.",
            "models": configured_models(), "fallback_models": configured_fallback_models(),
            "fallback_used": False, "fallback_attempts": [], "updated_at": None, "bar_time": None,
        }
    return {
        "status": "waiting", "decision": "WAIT", "confidence": 0,
        "consensus_pct": 0, "votes": [], "summary": "Esperando la primera evaluación de OpenRouter.",
        "risk": "Se requieren dos respuestas LLM válidas para gestionar la posición simulada.",
        "models": configured_models(), "fallback_models": configured_fallback_models(),
        "fallback_used": False, "fallback_attempts": [], "updated_at": None, "bar_time": None,
    }


def build_snapshot(
    candles: list[dict[str, Any]], price: float, analysis: dict[str, Any],
    symbol: str, interval: str, portfolio: dict[str, Any] | None = None,
    *, learning_summary: dict[str, Any] | None = None,
    backtest_summary: dict[str, Any] | None = None,
    recent_backtest: list[dict[str, Any]] | None = None,
    recent_signals: list[dict[str, Any]] | None = None,
    prior_ai_memory: list[dict[str, Any]] | None = None,
    market_24h: dict[str, Any] | None = None,
    market_provider: str | None = None,
    fast_entry_context: dict[str, Any] | None = None,
    fast_candles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the complete, auditable model context from the engine's current outputs.

    All computed temporal/amplitude/behavior/wick readings are sent without the old
    scale/case truncation. The model receives the complete 350-candle engine window,
    compact portfolio/backtest history and already-labelled prior AI outcomes.
    """
    engine_candles = list(candles[-350:])
    raw_candles: list[dict[str, Any]] = []
    snapshot_ms = int(time.time() * 1000)
    for candle in engine_candles:
        try:
            close_time = int(candle.get("close_time", int(candle.get("open_time", 0)) + 899999))
            raw_candles.append({
                "t": int(candle.get("open_time", 0)),
                "o": round(float(candle.get("open", 0)), 10),
                "h": round(float(candle.get("high", 0)), 10),
                "l": round(float(candle.get("low", 0)), 10),
                "c": round(float(candle.get("close", 0)), 10),
                "v": round(float(candle.get("volume", 0)), 8),
                "qv": round(float(candle.get("quote_volume", 0)), 8),
                "trades": int(candle.get("trades", 0) or 0),
                "close_time": close_time,
                "closed": close_time < snapshot_ms,
            })
        except (TypeError, ValueError):
            continue

    trend = measure_trend_persistence(engine_candles, price, float(analysis.get("atr_pct", 0) or 0))
    raw_fast_candles: list[dict[str, Any]] = []
    for candle in list(fast_candles or [])[-120:]:
        try:
            close_time = int(candle.get("close_time", int(candle.get("open_time", 0)) + 59999))
            raw_fast_candles.append({
                "t": int(candle.get("open_time", 0)),
                "o": round(float(candle.get("open", 0)), 10),
                "h": round(float(candle.get("high", 0)), 10),
                "l": round(float(candle.get("low", 0)), 10),
                "c": round(float(candle.get("close", 0)), 10),
                "v": round(float(candle.get("volume", 0)), 8),
                "qv": round(float(candle.get("quote_volume", 0)), 8),
                "trades": int(candle.get("trades", 0) or 0),
                "taker_buy_quote": round(float(candle.get("taker_buy_quote", 0)), 8),
                "close_time": close_time,
                "closed": close_time < snapshot_ms,
            })
        except (TypeError, ValueError):
            continue
    fast_entry = dict(fast_entry_context or {})

    # Preserve every output from the deterministic engine, including both sides'
    # reasons, all pivots and every wick-scale reading. Keep dimension bundles whole.
    technical_engine = {
        key: value for key, value in analysis.items()
        if key not in {"wave_dimensions", "wick_analysis", "elliott_wave", "elliott_prime_synthesis"}
    }
    technical_engine["signal_before_ai"] = analysis.get("signal", "NEUTRAL")
    technical_engine["wick_diagnostics"] = dict(analysis.get("wick_analysis", {}) or {})
    technical_engine["last_candle_time"] = analysis.get("last_candle_time")

    wave_dimensions = dict(analysis.get("wave_dimensions", {}) or {})
    temporal = dict(wave_dimensions.get("temporal", {}) or {})
    amplitude = dict(wave_dimensions.get("amplitude", {}) or {})
    behavior = dict(wave_dimensions.get("behavior", {}) or {})
    # Explicit completeness counters let each model know no computed rows were hidden.
    behavior_analogs = behavior.get("analogs", {}) or {}
    completeness = {
        "temporal_scale_readings_total": len(temporal.get("scales", []) or []),
        "amplitude_legs_total": len(amplitude.get("legs", []) or []),
        "behavior_horizons_total": len(behavior_analogs),
        "behavior_cases_total": sum(len((row or {}).get("cases", []) or []) for row in behavior_analogs.values()),
        "wick_scale_readings_total": len((technical_engine.get("wick_diagnostics", {}) or {}).get("scale_readings", []) or []),
        "pivots_total": len(technical_engine.get("pivots", []) or []),
    }

    wallet_context = dict(portfolio or {})
    wallet_context["history"] = list(wallet_context.get("history", []) or [])[:20]
    backtest_rows = list(recent_backtest or [])[:20]
    signal_rows = list(recent_signals or [])[:20]
    ai_memory = list(prior_ai_memory or [])[:12]

    manifest = {
        "schema_version": "prima15.llm-context.v5-elliott-prime",
        "complete_engine_output_sent": True,
        "engine_input_candles": len(engine_candles),
        "raw_candles_sent": len(raw_candles),
        "raw_candle_fields": ["t", "o", "h", "l", "c", "v", "qv", "trades", "close_time", "closed"],
        "computed_detail_counts": completeness,
        "elliott_wave_sent": bool(analysis.get("elliott_wave")),
        "elliott_timeframes_sent": sorted(((analysis.get("elliott_wave") or {}).get("timeframes") or {}).keys()),
        "elliott_15m_history_processed": int((((analysis.get("elliott_wave") or {}).get("history") or {}).get("15m_candles_processed", 0)) or 0),
        "elliott_15m_confirmed_pivots": int(((((analysis.get("elliott_wave") or {}).get("timeframes") or {}).get("15m") or {}).get("pivot_count_total", 0)) or 0),
        "elliott_prime_relation_sent": bool(analysis.get("elliott_prime_synthesis")),
        "portfolio_history_rows_sent": len(wallet_context.get("history", [])),
        "backtest_examples_sent": len(backtest_rows),
        "recent_signal_examples_sent": len(signal_rows),
        "prior_ai_predictions_with_outcomes_sent": len(ai_memory),
        "adaptive_learning_profile_sent": False,
        "adaptive_profile_fields_sent": [],
        "adaptive_profile_history_rows_sent": 0,
        "backtest_summary_sent": bool(backtest_summary),
        "learning_summary_sent": bool(learning_summary),
        "market_24h_sent": bool(market_24h),
        "fast_entry_sent": bool(fast_entry),
        "fast_1m_candles_sent": len(raw_fast_candles),
        "fast_entry_has_orderbook": bool((fast_entry.get("orderbook") or {}).get("available")),
        "fast_entry_has_taker_flow": bool((fast_entry.get("trade_flow") or {}).get("available")),
        "fast_entry_reference_samples": int(fast_entry.get("historical_sample_count", 0) or 0),
        "cross_market_assets_sent": sorted((fast_entry.get("cross_market") or {}).get("assets", {}).keys()),
        "included_sections": [
            "context_manifest", "symbol", "interval", "market_provider", "price_now", "snapshot_time_ms",
            "market_24h", "cross_market", "technical_engine", "decision_policy", "trend_persistence", "wave_dimensions", "simulation_wallet",
            "learning", "backtest", "recent_technical_signals", "prior_ai_memory", "adaptive_learning",
            "recent_15m_candles", "fast_entry", "recent_1m_candles", "elliott_wave", "prime_elliott_synthesis",
        ],
        "same_snapshot_sent_to_each_model": True,
        "same_market_data_different_role_instructions": True,
        "role_instructions_by_seat": {"exception": "primer modelo: soporte/resistencia y anomalías", "continuation": "segundo modelo: continuidad 3/5/8 y persistencia"},
        "required_evidence_families": list(EVIDENCE_OPTIONS.keys()),
        "required_evidence_count": len(EVIDENCE_OPTIONS),
        "required_evidence_coverage_pct": 100,
        "auxiliary_row_limits": {
            "portfolio_history": 20, "backtest_examples": 20, "recent_signals": 20,
            "prior_predictions_with_outcomes": 12, "adaptive_profile_changes": 6,
            "recent_1m_candles": 120, "elliott_pivots_per_timeframe": 24,
        },
        "compression_policy": "No se recortan escalas, pivotes, piernas, lecturas de mechas ni casos analógicos calculados. Se limitan solo los historiales auxiliares a sus N registros más recientes.",
        "not_sent": [
            "Velas 15m antiguas a las últimas 350 no se envían crudas a los LLM; las 5.000 más recientes (o las disponibles) sí son procesadas por el contador Elliott y sus conteos multigrado se envían resumidos.",
            "Credenciales, variables secretas y claves de OpenRouter.",
            "Registros auxiliares históricos más antiguos que los límites declarados; se envían los agregados y filas recientes indicados.",
            "Logs internos, trazas de depuración y estado visual de la interfaz, no datos de mercado.",
        ],
    }

    return {
        "context_manifest": manifest,
        "symbol": symbol,
        "interval": interval,
        "market_provider": market_provider or "unknown",
        "price_now": round(float(price), 10),
        "snapshot_time_ms": snapshot_ms,
        "market_24h": dict(market_24h or {}),
        "cross_market": dict((fast_entry.get("cross_market") or {}) if isinstance(fast_entry, dict) else {}),
        "technical_engine": technical_engine,
        "decision_policy": {
            "required_valid_llm_votes": 2,
            "minimum_llm_confidence": float(AI_MIN_CONFIDENCE),
            "exception_role": "primer modelo: decide ante rechazo extremo confirmado de soporte/resistencia",
            "continuation_role": "segundo modelo: valida continuidad 3/5/8 cuando no existe rechazo confirmado",
            "continuity_rule": "Nemotron debe coincidir con UP/DOWN y el acuerdo 3/5/8 debe superar el umbral adaptativo",
            "confirmed_rejection_rule": "si existe rechazo inferior/superior confirmado, prevalece la decisión del primer LLM",
            "valid_uncertainty_default": "Con dos respuestas LLM válidas, precio fresco y datos esenciales íntegros, si continuidad y excepción no validan otra dirección, la política simulada resuelve SHORT. La ausencia de quórum, precio fresco o datos esenciales produce WAIT",
            "fast_entry_requirement": "fast_entry es evidencia complementaria y diagnóstico de calidad; eligible=false NO bloquea una dirección LONG/SHORT válida del comité cuando FAST_ENTRY_ENFORCE_GATE=false",
            "fast_entry_validation": "TP-first histórico, Wilson, muestra, presión actual, spread, obstáculos y tiempo estimado al TP se reportan como evidencia/advertencias; solo son veto si FAST_ENTRY_ENFORCE_GATE=true",
            "fast_entry_label_warning": "TP-first es una tasa histórica de analogías, no probabilidad garantizada. La barrera adversa solo etiqueta backtest; no es un stop real",
            "api_or_quorum_failure": "WAIT; conservar la posición existente; nunca convertir fallo de API en SHORT",
            "simulated_only": True,
            "live_orders_enabled": False,
            "opening_fee_rate_pct": float(wallet_context.get("opening_fee_rate_pct", 0) or 0),
            "position_size_pct": float(wallet_context.get("position_size_pct", 0) or 0),
            "take_profit_target_pct": float(wallet_context.get("take_profit_target_pct", 0) or 0),
        },
        "trend_persistence": trend,
        "wave_dimensions": wave_dimensions,
        "elliott_wave": dict(analysis.get("elliott_wave", {}) or {}),
        "prime_elliott_synthesis": dict(analysis.get("elliott_prime_synthesis", {}) or {}),
        "simulation_wallet": wallet_context,
        "learning": dict(learning_summary or {}),
        "backtest": {
            "summary": dict(backtest_summary or {}),
            "recent_examples": backtest_rows,
            "horizon_bars": 4,
        },
        "recent_technical_signals": signal_rows,
        "prior_ai_memory": ai_memory,
        "adaptive_learning": {},
        "recent_15m_candles": raw_candles,
        "fast_entry": fast_entry,
        "recent_1m_candles": raw_fast_candles,
    }


def measure_trend_persistence(
    candles: list[dict[str, Any]], price: float, atr_pct: float = 0.0,
) -> dict[str, Any]:
    """Measure whether 3/5/8-candle price horizons share the same direction.

    The score is a directional agreement across horizons (not an estimated
    probability of future profit). A direction is considered established when
    at least two of the three horizons agree, which yields 66.7% or 100%.
    """
    closes: list[float] = []
    for candle in candles:
        try:
            value = float(candle.get("close", 0))
            if value > 0:
                closes.append(value)
        except (TypeError, ValueError):
            continue
    if price <= 0 or len(closes) < 9:
        return {
            "direction": "RANGE", "agreement_pct": 0.0,
            "up_horizons": 0, "down_horizons": 0,
            "horizons": [], "threshold_move_pct": 0.0,
        }
    min_move = max(0.025, max(0.0, atr_pct) * 0.10)
    horizons: list[dict[str, Any]] = []
    up = down = 0
    for bars in (3, 5, 8):
        reference = closes[-(bars + 1)]
        change_pct = (price / reference - 1.0) * 100.0
        if change_pct > min_move:
            direction = "UP"
            up += 1
        elif change_pct < -min_move:
            direction = "DOWN"
            down += 1
        else:
            direction = "FLAT"
        horizons.append({
            "bars": bars,
            "minutes": bars * 15,
            "change_pct": round(change_pct, 5),
            "direction": direction,
        })
    direction = "UP" if up >= 2 else "DOWN" if down >= 2 else "RANGE"
    agreement = max(up, down) / 3.0 * 100.0
    return {
        "direction": direction,
        "agreement_pct": round(agreement, 1),
        "up_horizons": up,
        "down_horizons": down,
        "horizons": horizons,
        "threshold_move_pct": round(min_move, 5),
    }


def parse_model_content(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {None, "text", "output_text"}:
                value = item.get("text")
                if isinstance(value, str) and value.strip():
                    text_parts.append(value)
        content = "\n".join(text_parts)
    if isinstance(content, dict):
        content = content.get("text") or content.get("output_text") or ""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("El modelo devolvió una respuesta final vacía.")

    # Extract the first complete JSON object, even if the provider adds a short preamble.
    decoder = json.JSONDecoder()
    data = None
    for match in re.finditer(r"\{", content):
        try:
            candidate, _ = decoder.raw_decode(content[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            data = candidate
            break
    if data is None:
        raise ValueError("La respuesta del modelo no contiene un objeto JSON válido.")

    decision = str(data.get("decision", "WAIT")).strip().upper()
    if decision in {"NEUTRAL", "HOLD", "WAITING", "ESPERAR", "NEUTRO"}:
        decision = "WAIT"
    if decision not in {"LONG", "SHORT", "WAIT"}:
        decision = "WAIT"
    try:
        confidence = max(0.0, min(100.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    raw_evidence = data.get("evidence_assessments", {})
    evidence_assessments: dict[str, Any] = {}
    if isinstance(raw_evidence, dict):
        for section in EVIDENCE_OPTIONS:
            item = raw_evidence.get(section)
            if isinstance(item, dict):
                reading = str(item.get("reading", "")).strip().upper()
                basis = str(item.get("basis", item.get("evidence", ""))).strip()[:280]
            elif isinstance(item, str):
                reading, basis = item.strip().upper(), ""
            else:
                continue
            if reading:
                evidence_assessments[section] = {"reading": reading, "basis": basis}
    missing_evidence, evidence_coverage = _evidence_coverage(evidence_assessments)
    return {
        "decision": decision,
        "confidence": round(confidence, 1),
        "summary": str(data.get("summary", data.get("reason", "Sin explicación.")))[:500],
        "risk": str(data.get("risk", "No especificado por el modelo."))[:350],
        "evidence_assessments": evidence_assessments,
        "evidence_coverage_pct": evidence_coverage,
        "missing_evidence_sections": missing_evidence,
    }


def aggregate_votes(votes: list[dict[str, Any]], min_confidence: float = 30.0) -> dict[str, Any]:
    valid = [v for v in votes if v.get("decision") in {"LONG", "SHORT", "WAIT"}]
    if not valid:
        return {
            "status": "error", "decision": "WAIT", "confidence": 0,
            "consensus_pct": 0, "summary": "Ningún modelo entregó una evaluación válida.",
            "risk": "La señal queda bloqueada hasta obtener una respuesta válida.",
        }
    counts = {side: sum(1 for v in valid if v["decision"] == side) for side in ("LONG", "SHORT", "WAIT")}
    winner = max(counts, key=counts.get)
    tied = sum(1 for count in counts.values() if count == counts[winner]) > 1
    majority = counts[winner] > len(valid) / 2
    winners = [v for v in valid if v["decision"] == winner]
    confidence = sum(float(v.get("confidence", 0)) for v in winners) / len(winners) if winners and not tied else 0
    consensus = (counts[winner] / len(valid) * 100.0) if not tied else 0.0
    final_decision = winner if majority and not tied else "WAIT"
    if final_decision in {"LONG", "SHORT"} and confidence < min_confidence:
        final_decision = "WAIT"
    summaries = [str(v.get("summary", "")).strip() for v in winners if str(v.get("summary", "")).strip()]
    risks = [str(v.get("risk", "")).strip() for v in valid if str(v.get("risk", "")).strip()]
    summary = "El comité quedó dividido; no existe consenso direccional." if tied else (" / ".join(summaries[:2]) or "Los modelos no aportaron una explicación.")
    return {
        "status": "ok", "decision": final_decision,
        "confidence": round(confidence, 1), "consensus_pct": round(consensus, 1),
        "summary": summary,
        "risk": " / ".join(risks[:2]) or "No se informaron riesgos adicionales.",
    }


def _auth_headers() -> dict[str, str]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Falta OPENROUTER_API_KEY.")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5000"),
        "X-Title": "PRIMA-15 Render Wave Lab",
    }


def _timeout() -> tuple[int, int]:
    value = max(10, min(60, int(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "35"))))
    return (5, value)


def _http_error(response: requests.Response) -> RuntimeError:
    detail = "solicitud rechazada"
    try:
        error_data = response.json()
        error_obj = error_data.get("error", {}) if isinstance(error_data, dict) else {}
        detail = str(error_obj.get("message", error_obj)) if isinstance(error_obj, dict) else str(error_obj)
    except (ValueError, AttributeError):
        body = getattr(response, "text", "")
        if body:
            detail = str(body)[:220]
    return RuntimeError(f"OpenRouter HTTP {response.status_code}: {detail[:220]}")


def _request_decision_model(
    model: str, snapshot: dict[str, Any], role: str = "exception",
) -> dict[str, Any]:
    """Perplexity Decider uses OpenRouter's typed Decisions API, not chat completions."""
    market_state = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    if role == "continuation":
        required = _required_trend_agreement(snapshot)
        instructions = (
            "Eres el analista de CONTINUIDAD. Exige que trend_persistence.direction sea UP o DOWN "
            f"y que agreement_pct supere {required:.1f}%; "
            "contrasta esa dirección con wave_dimensions.behavior. Si la huella "
            "histórica favorece la misma dirección, refuerza la lectura; si wave_dimensions.amplitude indica "
            "debilitamiento contrario o la huella marca agotamiento, reduce convicción o WAIT. Las coincidencias "
            "primas solo describen tiempos y proporciones; no prueban causalidad. No anticipes giros por una vela. "
            "Las anomalías confirmadas de soporte/resistencia corresponden al primer LLM. "
            "Además, estudia fast_entry y cross_market como evidencia complementaria. La elegibilidad TP-first es "
            "un indicador de calidad, NO un veto automático: si no es elegible, explica los bloqueadores, pero "
            "decide según continuidad, estructura y consenso del rol; no respondas WAIT solo por fast_entry. "
            "Comprueba si BTC/ETH acompañan o contradicen RENDER; contrasta tasa TP-first, volumen, presión 1m, "
            "VWAP, EMA, agresión, libro, spread, obstáculos y tiempo mediano al TP."
        )
    else:
        instructions = (
            "Eres el PRIMER LLM y detectas EXCEPCIONES por rechazo real en soportes/resistencias. Lee "
            "technical_engine.wick_diagnostics junto con wave_dimensions.temporal, amplitude y behavior. "
            "Un rechazo inferior solo favorece LONG si la vela está cerrada, la mecha está realmente en zona "
            "baja, recuperó soporte previo y hay confirmaciones independientes. Un rechazo superior exige la "
            "condición simétrica. Si no hay rechazo confirmado, responde WAIT y explica qué prueba falta. "
            "La dimensión de amplitud puede mostrar contracción o expansión; la de comportamiento compara "
            "huellas con secuencias históricas ya resueltas. No llames probabilidad a la similitud. "
            "Contrasta fast_entry y cross_market como evidencia complementaria. La elegibilidad TP-first no es un veto "
            "automático: si el filtro no pasa, señala exactamente los bloqueadores y resuelve según las evidencias "
            "de tendencia y estructura; no respondas WAIT únicamente por ese filtro. Comprueba si BTC/ETH acompañan "
            "a RENDER o se mueven simultáneamente contra él."
        )
    questions: dict[str, Any] = {
        "market_direction": {
            "type": "choice",
            "instructions": (
                "Evalúa solamente el snapshot completo proporcionado para RENDERUSDT en velas de 15 minutos. "
                + instructions
                + " No inventes datos, precios ni probabilidades. La wallet es simulada. "
                "Los números primos son referencias exploratorias, no una ley."
            ),
            "criteria": {"LONG": None, "SHORT": None, "WAIT": None},
        }
    }
    evidence_instructions = {
        "market": "Clasifica el contexto de mercado de 24h y proveedor usando market_24h; sepáralo de la dirección intradía de RENDER.",
        "raw_candles": "Interpreta la secuencia completa recent_15m_candles (hasta 350 velas OHLCV): dirección, rango y cambios de ritmo; no mires datos futuros.",
        "engine": "Clasifica la salida completa technical_engine: señal, puntuaciones, razones, pivotes, soportes/resistencias, RSI, ATR y bloqueadores.",
        "policy": "Comprueba decision_policy y el contexto actual. Indica ALIGNED si la salida propuesta respeta el rol y reglas; CONFLICT si las contradice; INSUFFICIENT si faltan datos. No cambies las reglas.",
        "temporal": "Clasifica la ubicación del cierre en TODAS las ventanas primas según P y alineación baja/alta; ubicación no equivale a reversión.",
        "prime": "Clasifica si ventanas, tiempos y proporciones primas se alinean abajo, arriba, en ambos lados o no se alinean.",
        "amplitude": "Clasifica expansión/contracción porcentual de TODAS las piernas de ondas, con proporciones y retrocesos.",
        "behavior": "Clasifica la huella frente a TODOS los casos analógicos enviados y sus resultados históricos resueltos; similitud no es probabilidad.",
        "wick": "Clasifica TODAS las lecturas de mecha, P15, mecha/rango, mecha/cuerpo, escalas, barrido y recuperación/rechazo.",
        "cross_market": "Clasifica BTCUSDT y ETHUSDT con sus retornos 3/5/10 minutos y antigüedad. Compara también el retorno RENDER 5m con el promedio de ambos líderes: indica si RENDER los supera, queda rezagado o se mueve alineado. Un mercado líder opuesto no debe vetar automáticamente a RENDER si existe desacople relativo claro. No atribuyas sus movimientos directamente a RENDER.",
        "trend": "Clasifica las variaciones por horizonte 3/5/8 velas y su acuerdo porcentual.",
        "signals": "Clasifica señales técnicas recientes y resultados conocidos, diferenciando señales todavía sin etiqueta.",
        "backtest": "Clasifica resumen y ejemplos de backtest según muestras, acierto, retorno, MFE y MAE.",
        "prior_memory": "Clasifica decisiones LLM previas y outcomes observados por horizonte; no inventes resultados ausentes.",
        "portfolio": "Describe wallet porcentual, equity, P&L, comisiones, tamaño, posición e historial reciente; no uses saldo como señal direccional.",
        "learning": "Clasifica estadísticas walk-forward, tamaños de muestra, acierto y retorno del aprendizaje.",
        "adaptive_profile": "Clasifica pesos, umbral de continuidad, TP, fiabilidad por modelo/rol, muestras y cambios recientes del perfil adaptativo.",
        "fast_entry": "Evalúa fast_entry como diagnóstico de calidad de entrada de horizonte corto: tasa histórica TP-first y límite Wilson, analogías temporales, tiempo mediano al objetivo, EMA/ROC/VWAP, volumen, imbalance del libro, agresión taker, spread, obstáculos 1m/15m y fuerza relativa RENDER/BTC/ETH. No confundas tasa histórica con probabilidad garantizada. Si eligible=false, clasifica NO_EDGE o MIXED y explica los bloqueadores; esta lectura es evidencia complementaria y no debe forzar WAIT si otras fuentes sustentan dirección.",
        "elliott": "Interpreta los conteos Elliott completos de 1m, 15m, 1h y 4h. Distingue impulso estándar válido (5 ondas), conteo parcial, ABC provisional, diagonal/triángulo posible y ausencia de conteo claro. Comprueba las reglas duras del impulso (onda 2 no supera origen de 1; onda 3 supera el extremo de 1 y no es la más corta; onda 4 no solapa el territorio de 1 en un impulso estándar). Trata Fibonacci y alternancia como guías, no reglas. No declares que la subdivisión 5-3-5-3-5/5-3-5/3-3-5 está confirmada cuando solo hay pivotes exteriores. Usa el grado 1h/4h como contexto y prioriza 15m para la estructura de decisión; 1m es microestructura.",
        "elliott_prime": "Relaciona prime_elliott_synthesis con los conteos: verifica duración en velas de cada tramo, primo cercano, diferencia y dirección del motor prima. Informa confluencia o conflicto, sin afirmar causalidad. Si Elliott no presenta impulso válido, selecciona INSUFFICIENT o NO_CONFLUENCE y explica qué conteo falta.",
        "cross_market": "Evalúa cross_market: rendimientos 3/5/10 minutos y frescura de BTCUSDT y ETHUSDT. Compara el rendimiento 5m de RENDER frente al promedio de ambos para detectar desacople relativo. Indica si los líderes confirman o contradicen la dirección y si RENDER los supera o queda rezagado. Son contexto externo y nunca sustituyen la evidencia propia de RENDER.",
    }
    for section, choices in EVIDENCE_OPTIONS.items():
        questions[f"evidence_{section}"] = {
            "type": "choice",
            "instructions": evidence_instructions[section] + " Si faltan datos, selecciona INSUFFICIENT o UNAVAILABLE cuando corresponda.",
            "criteria": {choice: None for choice in choices},
        }
    payload = {"model": model, "state": [market_state], "questions": questions}
    response = requests.post(
        OPENROUTER_DECISIONS_URL, headers=_auth_headers(), json=payload, timeout=_timeout()
    )
    if response.status_code != 200:
        raise _http_error(response)
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Decisions API devolvió un cuerpo que no es JSON.") from exc
    answer = (data.get("answers") or {}).get("market_direction") if isinstance(data, dict) else None
    if not isinstance(answer, dict):
        raise RuntimeError("Decisions API no devolvió answers.market_direction.")
    choice = str(answer.get("choice", "WAIT")).strip().upper()
    if choice not in {"LONG", "SHORT", "WAIT"}:
        choice = "WAIT"
    probabilities = answer.get("probabilities", {})
    probability = probabilities.get(choice) if isinstance(probabilities, dict) else None
    if probability is None:
        probability = answer.get("confidence", 0)
    try:
        confidence_value = float(probability)
        confidence = confidence_value * 100.0 if confidence_value <= 1.0 else confidence_value
    except (TypeError, ValueError):
        confidence = 0.0
    answers = data.get("answers", {}) if isinstance(data, dict) else {}
    evidence_assessments: dict[str, Any] = {}
    for section, options in EVIDENCE_OPTIONS.items():
        answer = answers.get(f"evidence_{section}", {}) if isinstance(answers, dict) else {}
        if not isinstance(answer, dict):
            continue
        reading = str(answer.get("choice", "")).strip().upper()
        probabilities = answer.get("probabilities", {})
        if reading in options and isinstance(probabilities, dict) and probabilities:
            evidence_assessments[section] = {
                "reading": reading,
                "basis": "Distribución devuelta por Decider: " + json.dumps(probabilities, ensure_ascii=False, separators=(",", ":"))[:220],
                "probabilities": probabilities,
            }
    _validate_evidence_coverage(evidence_assessments, model)
    missing, coverage = _evidence_coverage(evidence_assessments)
    return {
        "decision": choice,
        "confidence": round(max(0.0, min(100.0, confidence)), 1),
        "summary": f"Perplexity Decider seleccionó {choice}; su puntuación corresponde a la selección entre opciones, no a la probabilidad de ganar una operación.",
        "risk": "La salida estructurada no sustituye la validación técnica ni garantiza el resultado del mercado.",
        "evidence_assessments": evidence_assessments,
        "evidence_coverage_pct": coverage,
        "missing_evidence_sections": missing,
        "assessment_mode": "typed_probabilities",
        "requested_model": model,
        "served_model": str(data.get("model", model)),
        "usage": data.get("usage", {}) if isinstance(data.get("usage"), dict) else {},
    }


def _request_messages_model(
    model: str, snapshot: dict[str, Any], role: str = "continuation",
) -> dict[str, Any]:
    """Recursive Flint is called through the Anthropic-compatible Messages endpoint."""
    system_prompt = _role_prompt(role, _required_trend_agreement(snapshot))
    payload = {
        "model": model,
        "max_tokens": max(900, min(2400, int(os.getenv("OPENROUTER_MAX_TOKENS", "1400")))),
        "system": system_prompt,
        "messages": [{
            "role": "user",
            "content": json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        }],
        "temperature": 0.1,
        "stream": False,
    }
    headers = _auth_headers()
    headers["anthropic-version"] = "2023-06-01"
    response = requests.post(
        OPENROUTER_MESSAGES_URL, headers=headers, json=payload, timeout=_timeout()
    )
    if response.status_code != 200:
        raise _http_error(response)
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Messages API devolvió un cuerpo que no es JSON.") from exc
    content = data.get("content", []) if isinstance(data, dict) else []
    try:
        parsed = parse_model_content(content)
    except ValueError as exc:
        if data.get("stop_reason") == "max_tokens":
            raise RuntimeError("Messages API agotó max_tokens antes de completar el JSON; aumenta OPENROUTER_MAX_TOKENS o usa otro respaldo.") from exc
        raise
    _validate_evidence_coverage(parsed.get("evidence_assessments", {}), model)
    parsed["assessment_mode"] = "textual_basis"
    parsed["requested_model"] = model
    parsed["served_model"] = str(data.get("model", model))
    parsed["usage"] = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
    return parsed


def _role_prompt(role: str, required_trend_agreement: float = 60.0) -> str:
    required_trend_agreement = max(58.0, min(72.0, float(required_trend_agreement)))
    common = (
        "Evalúa solamente el snapshot completo de RENDERUSDT en velas de 15 minutos. Debes interpretar "
        "todas las secciones disponibles: context_manifest, market_24h, technical_engine, decision_policy, trend_persistence, "
        "wave_dimensions.temporal, wave_dimensions.amplitude, wave_dimensions.behavior, "
        "wave_dimensions.synthesis, elliott_wave (1m/15m/1h/4h), prime_elliott_synthesis, simulation_wallet, learning, backtest, recent_technical_signals, "
        "prior_ai_memory, adaptive_learning, cross_market y recent_15m_candles. Debes clasificar cada uno de los 20 bloques "
        "requeridos en evidence_assessments con valores verificables; no basta con devolver LONG/SHORT. "
        "No ignores una dimensión por favorecer otra: identifica acuerdos, contradicciones, datos faltantes y condiciones que invaliden tu lectura. "
        "Comprueba context_manifest para conocer el alcance exacto de los datos. No inventes datos ni uses "
        "información futura. Los números primos son referencias exploratorias, no una ley. La wallet "
        "es simulada. confidence es convicción heurística, no probabilidad calibrada de beneficio. "
    )
    if role == "continuation":
        task = (
            "Tu rol es CONTINUIDAD DE TENDENCIA. Usa trend_persistence en ventanas 3, 5 y 8 y contrástalo "
            f"con wave_dimensions.behavior y wave_dimensions.amplitude. El mínimo dinámico de acuerdo es {required_trend_agreement:.1f}%. "
            "Si direction=UP y agreement_pct supera ese mínimo, la hipótesis base es LONG; si direction=DOWN "
            "y agreement_pct supera ese mínimo, la hipótesis base es SHORT. Refuerza solo si la huella de "
            "comportamiento concuerda; reduce convicción si hay contracción de amplitud en esa dirección o agotamiento. "
            "Si direction=RANGE o agreement_pct queda por debajo del mínimo, responde WAIT en tu rol; el comité "
            "aplicará SHORT por defecto direccional cuando los datos estén disponibles y la continuidad no esté confirmada. "
            "Usa BTC/ETH 1m como contexto: dos activos líderes moviéndose fuertemente contra RENDER reducen o invalidan la continuidad; si acompañan, es confirmación secundaria. "
            "Revisa también backtest, resultados previos por modelo, perfil adaptativo, datos de wallet y "
            "velas OHLCV; usa los primeros como calibración y nunca como garantía. No uses un primo aislado "
            "como gatillo y no decidas excepciones de soporte/resistencia: pertenecen al primer LLM. "
            "Si el movimiento está en rango o la continuidad no alcanza el umbral, aplica la política del sistema: "
            "SHORT por defecto direccional, salvo que los datos esenciales estén ausentes o corruptos."
        )
    else:
        task = (
            "Tu rol es detectar EXCEPCIONES Y RECHAZOS en soportes/resistencias. Lee "
            "technical_engine.wick_diagnostics como pruebas separadas: P15 del cierre, mecha/rango, "
            "mecha/cuerpo, P de mecha baja/alta en cada escala prima, barrido y recuperación de soporte, "
            "rechazo de resistencia, volumen, impulso, RSI y divergencia. Contrasta además las tres "
            "dimensiones: temporal (confluencia de escalas y duración prima), amplitud (contracción/expansión "
            "y retroceso entre ondas) y comportamiento (huellas históricas con horizontes 4/8/12 velas). "
            "No llames mínimo/máximo confirmado a una vela abierta. Una mecha larga por sí sola no confirma "
            "un giro: exige zona extrema en varias escalas, recuperación de soporte para LONG o rechazo de "
            "resistencia para SHORT y al menos dos confirmaciones independientes. Si lower.rejection_confirmed "
            "es true, considera LONG; si upper.rejection_confirmed es true, considera SHORT. Si ambas o ninguna "
            "lo son, no declares una excepción y responde WAIT en tu rol; el comité aplicará la regla de continuidad "
            "y SHORT por incertidumbre válida. Explica exactamente qué umbral se cumplió y qué falta, citando P15, "
            "porcentaje de mecha, escalas, recuperación y bloqueadores. La coincidencia prima nunca es prueba "
            "causal ni sustituye la geometría de la vela. Usa fast_entry.opportunities como advertencia de calidad: "
            "indica sus bloqueadores, pero no declares WAIT únicamente porque eligible=false. El comité conserva "
            "la decisión direccional y el gestor aplica el quórum LLM."
        )
    return (
        common + task + f" Devuelve únicamente JSON válido e incluye TODAS las {len(EVIDENCE_OPTIONS)} secciones de evidence_assessments. "
        "Cada sección debe tener reading y basis; reading puede ser INSUFFICIENT/UNAVAILABLE si la fuente no aporta datos, "
        "pero basis debe citar valores o campos concretos del snapshot y explicar por qué. No rellenes con suposiciones. "
        "Formato: {\"decision\":\"LONG|SHORT|WAIT\",\"confidence\":0-100,"
        "\"summary\":\"explicación breve en español\",\"risk\":\"riesgo o condición\", "
        "\"evidence_assessments\":{"
        "\"market\":{\"reading\":\"RISING|FALLING|MIXED|UNAVAILABLE\",\"basis\":\"cambio 24h y proveedor\"},"
        "\"cross_market\":{\"reading\":\"SUPPORTS_LONG|SUPPORTS_SHORT|MIXED|NEUTRAL|INSUFFICIENT\",\"basis\":\"retornos 3/5/10 minutos y frescura de BTC/ETH\"},"
        "\"raw_candles\":{\"reading\":\"UP|DOWN|RANGE|INSUFFICIENT\",\"basis\":\"retorno y dirección de velas incluidas\"},"
        "\"engine\":{\"reading\":\"LONG_BIAS|SHORT_BIAS|MIXED|NEUTRAL|INSUFFICIENT\",\"basis\":\"señal, puntuaciones y razones concretas\"},"
        "\"policy\":{\"reading\":\"ALIGNED|CONFLICT|INSUFFICIENT\",\"basis\":\"regla activa y condición de la decisión\"},"
        "\"temporal\":{\"reading\":\"LOW_ZONE|HIGH_ZONE|MIXED|NEUTRAL|INSUFFICIENT\",\"basis\":\"valores P y escalas\"},"
        "\"prime\":{\"reading\":\"LOW_ALIGNMENT|HIGH_ALIGNMENT|BOTH|NO_ALIGNMENT|INSUFFICIENT\",\"basis\":\"primos y diferencias observadas\"},"
        "\"amplitude\":{\"reading\":\"UP_EXPANDING|UP_CONTRACTING|DOWN_EXPANDING|DOWN_CONTRACTING|MIXED|INSUFFICIENT\",\"basis\":\"porcentajes y piernas comparadas\"},"
        "\"behavior\":{\"reading\":\"FAVORS_UP|FAVORS_DOWN|MIXED|INSUFFICIENT\",\"basis\":\"horizontes, casos y retornos históricos\"},"
        "\"wick\":{\"reading\":\"LOWER_REJECTION|UPPER_REJECTION|NO_CONFIRMED_REJECTION|CONFLICT|INSUFFICIENT\",\"basis\":\"P15, mecha, escalas y soporte/resistencia\"},"
        "\"trend\":{\"reading\":\"UP|DOWN|RANGE|INSUFFICIENT\",\"basis\":\"cambios 3/5/8 y acuerdo\"},"
        "\"signals\":{\"reading\":\"SUPPORTS_LONG|SUPPORTS_SHORT|MIXED|INSUFFICIENT\",\"basis\":\"señales recientes y outcomes conocidos\"},"
        "\"backtest\":{\"reading\":\"SUPPORTS_LONG|SUPPORTS_SHORT|MIXED|INSUFFICIENT\",\"basis\":\"muestras, acierto, retorno, MFE y MAE\"},"
        "\"prior_memory\":{\"reading\":\"FAVORS_LONG|FAVORS_SHORT|MIXED|INSUFFICIENT\",\"basis\":\"decisiones previas y outcomes observados\"},"
        "\"portfolio\":{\"reading\":\"POSITION_LONG|POSITION_SHORT|FLAT|RISK_REVIEW|UNAVAILABLE\",\"basis\":\"wallet, posición y costes en porcentajes\"},"
        "\"learning\":{\"reading\":\"IMPROVING|DETERIORATING|MIXED|INSUFFICIENT\",\"basis\":\"muestras y resultados walk-forward\"},"
        "\"adaptive_profile\":{\"reading\":\"FAVORS_LONG|FAVORS_SHORT|MIXED|INSUFFICIENT\",\"basis\":\"pesos, umbrales, TP, fiabilidad y muestras\"},"
        "\"fast_entry\":{\"reading\":\"LONG_SETUP|SHORT_SETUP|NO_EDGE|MIXED|INSUFFICIENT\",\"basis\":\"TP-first histórico, Wilson, muestra, presión viva, spread, obstáculo y tiempo a TP\"},"
        "\"elliott\":{\"reading\":\"BULLISH_IMPULSE|BEARISH_IMPULSE|BULLISH_CORRECTION|BEARISH_CORRECTION|POSSIBLE_DIAGONAL|POSSIBLE_TRIANGLE|MIXED|NO_CLEAR_COUNT|INSUFFICIENT\",\"basis\":\"grados, patrón, pivotes, reglas duras, fase y alternativas\"},"
        "\"elliott_prime\":{\"reading\":\"CONFLUENCE_UP|CONFLUENCE_DOWN|DIRECTION_CONFLICT|PRIME_TIME_FIT_ONLY|NO_CONFLUENCE|INSUFFICIENT\",\"basis\":\"ajuste primo de las duraciones y dirección del motor prima frente al conteo\"}}}."
    )


def _request_chat_model(
    model: str, snapshot: dict[str, Any], role: str = "continuation",
) -> dict[str, Any]:
    system_prompt = _role_prompt(role, _required_trend_agreement(snapshot))
    # 320 tokens were insufficient for reasoning models: their private reasoning could
    # consume the entire budget, leaving an empty final message (finish_reason=length).
    max_tokens = max(900, min(2400, int(os.getenv("OPENROUTER_MAX_TOKENS", "1400"))))
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # OpenRouter documents reasoning-budget controls for these NVIDIA families.
    # Cap internal reasoning so a visible final vote still has room in max_tokens.
    if model in REASONING_BUDGET_MODELS:
        payload["reasoning"] = {"max_tokens": 600}
    response = requests.post(
        OPENROUTER_URL, headers=_auth_headers(), json=payload, timeout=_timeout()
    )
    if response.status_code != 200:
        raise _http_error(response)
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Chat Completions devolvió HTTP 200, pero el cuerpo no es JSON válido.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("OpenRouter devolvió un formato de respuesta inesperado.")
    if data.get("error"):
        error_obj = data.get("error")
        detail = error_obj.get("message", str(error_obj)) if isinstance(error_obj, dict) else str(error_obj)
        raise RuntimeError(f"OpenRouter informó un error: {str(detail)[:220]}")
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise RuntimeError("OpenRouter no devolvió choices; se activará el modelo de respaldo.")
    choice = choices[0]
    finish_reason = str(choice.get("finish_reason", ""))
    if choice.get("error") or finish_reason == "error":
        detail = choice.get("error") or "el proveedor terminó la generación con error"
        raise RuntimeError(f"OpenRouter no completó la generación: {str(detail)[:220]}")
    message = choice.get("message", {})
    if not isinstance(message, dict):
        raise RuntimeError("OpenRouter devolvió un mensaje inválido.")
    content = message.get("content")
    if not content or (isinstance(content, str) and not content.strip()):
        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else {}
        reasoning_tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        if finish_reason == "length":
            detail = f"límite de {max_tokens} tokens alcanzado"
            if reasoning_tokens is not None:
                detail += f"; {reasoning_tokens} tokens se consumieron en razonamiento"
            raise RuntimeError(f"Respuesta final vacía: {detail}. Aumenta OPENROUTER_MAX_TOKENS o usa otro modelo.")
        if message.get("reasoning"):
            raise RuntimeError("El proveedor devolvió razonamiento, pero no generó respuesta final.")
        raise RuntimeError(f"El proveedor devolvió contenido vacío (finish_reason={finish_reason or 'sin dato'}, completion_tokens={completion_tokens}).")
    try:
        parsed = parse_model_content(content)
    except ValueError as exc:
        if finish_reason == "length":
            raise RuntimeError(f"El JSON quedó truncado al alcanzar max_tokens={max_tokens}; aumenta OPENROUTER_MAX_TOKENS o usa otro modelo.") from exc
        raise
    _validate_evidence_coverage(parsed.get("evidence_assessments", {}), model)
    parsed["assessment_mode"] = "textual_basis"
    parsed["requested_model"] = model
    parsed["served_model"] = str(data.get("model", model))
    parsed["usage"] = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
    return parsed


def _request_model(
    model: str, snapshot: dict[str, Any], role: str = "continuation",
) -> dict[str, Any]:
    """Dispatch each curated model to the OpenRouter endpoint it actually supports."""
    if not api_key_present():
        raise RuntimeError("Falta OPENROUTER_API_KEY.")
    if model in DECISION_MODELS:
        return _request_decision_model(model, snapshot, role)
    if model == "recursive/flint-v1.0:free":
        return _request_messages_model(model, snapshot, role)
    return _request_chat_model(model, snapshot, role)


def _required_trend_agreement(snapshot: dict[str, Any]) -> float:
    adaptive = snapshot.get("adaptive_learning", {}) or {}
    try:
        value = float(adaptive.get("trend_agreement_min_pct", 60.0))
    except (TypeError, ValueError):
        value = 60.0
    return max(58.0, min(72.0, value))


def evaluate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Evaluate preferred models, then use backup models only to fill missing quorum votes.

    A backup is a substitute committee member, never an additional vote. Empty content,
    malformed JSON, provider errors and timeouts all advance to the next available model.
    """
    models = configured_models()
    fallback_pool = configured_fallback_models()
    votes: list[dict[str, Any]] = []
    errors: list[str] = []
    attempts: list[dict[str, str]] = []
    attempted_models: set[str] = set()
    served_models: set[str] = set()
    fatal_auth_error = False
    fatal_rate_limit = False
    consecutive_429s = 0
    max_attempts = max(3, min(10, int(os.getenv("OPENROUTER_MAX_ATTEMPTS", "10"))))
    min_confidence = max(0.0, min(90.0, float(AI_MIN_CONFIDENCE)))
    required_votes = 2

    def try_one(model: str, source: str, analysis_role: str) -> bool:
        nonlocal fatal_auth_error, fatal_rate_limit, consecutive_429s
        if fatal_auth_error or fatal_rate_limit or model in attempted_models or len(attempted_models) >= max_attempts:
            return False
        attempted_models.add(model)
        try:
            vote = _request_model(model, snapshot, analysis_role)
            served = str(vote.get("served_model", model))
            if served in served_models:
                attempts.append({"model": model, "status": "duplicate", "detail": f"Ya respondió {served}; no se duplica su voto."})
                return False
            consecutive_429s = 0
            served_models.add(served)
            vote["source"] = source
            vote["analysis_role"] = analysis_role
            vote["served_model"] = served
            votes.append(vote)
            attempts.append({"model": model, "status": "ok", "detail": f"Respondió {served}."})
            return True
        except Exception as exc:  # Failover is deliberate; a failed LLM must not stop the worker.
            reason = str(exc).strip() or exc.__class__.__name__
            reason = reason[:180]
            log.warning("Fallo modelo OpenRouter (%s): %s", model, reason)
            errors.append(f"{model}: {reason}")
            attempts.append({"model": model, "status": "error", "detail": reason})
            # A 401 means the shared API key is invalid; other models cannot repair it.
            if "HTTP 401" in reason or "invalid api key" in reason.lower():
                fatal_auth_error = True
            if "HTTP 429" in reason:
                consecutive_429s += 1
                # Two consecutive 429s usually indicate an account/provider rate cap.
                # Stop instead of burning the remaining free-model requests.
                if consecutive_429s >= 2:
                    fatal_rate_limit = True
            else:
                consecutive_429s = 0
            return False

    # Ask primary models until two independent valid votes are available.
    missing_roles = ["exception", "continuation"]
    for slot, model in enumerate(models):
        if len(votes) >= required_votes:
            break
        if slot == 0:
            assigned_role = "exception"
        elif slot == 1:
            assigned_role = "continuation"
        else:
            # An optional third primary fills whichever semantic seat is still missing.
            assigned_role = missing_roles[0] if missing_roles else "continuation"
        if try_one(model, "principal", assigned_role) and assigned_role in missing_roles:
            missing_roles.remove(assigned_role)

    # A backup fills the missing semantic seat; it cannot create an extra vote.
    fallback_used: list[str] = []
    for backup in fallback_pool:
        if fatal_auth_error or fatal_rate_limit or len(votes) >= required_votes:
            break
        assigned_role = missing_roles[0] if missing_roles else "continuation"
        before = len(votes)
        if try_one(backup, "respaldo", assigned_role):
            if assigned_role in missing_roles:
                missing_roles.remove(assigned_role)
            fallback_used.append(backup)

    result = aggregate_votes(votes, min_confidence=0.0)
    long_votes = sum(1 for vote in votes if vote.get("decision") == "LONG")
    short_votes = sum(1 for vote in votes if vote.get("decision") == "SHORT")
    wait_votes = sum(1 for vote in votes if vote.get("decision") == "WAIT")
    technical = snapshot.get("technical_engine", {}) or {}
    wick = technical.get("wick_diagnostics", {}) or {}
    lower_rejection = bool(
        (wick.get("lower", {}) or {}).get("rejection_confirmed", technical.get("support_rejection_confirmed", False))
    )
    upper_rejection = bool(
        (wick.get("upper", {}) or {}).get("rejection_confirmed", technical.get("resistance_rejection_confirmed", False))
    )
    anomaly = lower_rejection or upper_rejection
    exception_vote = next((v for v in votes if v.get("analysis_role") == "exception"), None)
    continuation_vote = next((v for v in votes if v.get("analysis_role") == "continuation"), None)
    trend = snapshot.get("trend_persistence", {}) or {}
    trend_direction = str(trend.get("direction", "RANGE")).upper()
    trend_agreement = float(trend.get("agreement_pct", 0) or 0)

    if len(votes) >= required_votes:
        result["status"] = "ok"
        result["valid_models"] = len(votes)
        result["committee_decision"] = result.get("decision", "WAIT")
        if anomaly:
            # A verified extreme rejection switches control from trend-following to Perplexity's exception role.
            exception_side = str((exception_vote or {}).get("decision", "WAIT")).upper()
            exception_confidence = float((exception_vote or {}).get("confidence", 0) or 0)
            if exception_side in {"LONG", "SHORT"} and exception_confidence >= min_confidence:
                result["decision"] = exception_side
                result["confidence"] = round(exception_confidence, 1)
                result["decision_basis"] = "EXCEPTION_LLM"
                result["selected_model"] = (exception_vote or {}).get("served_model")
                result["policy_reason"] = (
                    "Se confirmó un rechazo extremo (inferior o superior); por la excepción de soporte/resistencia "
                    f"prevalece el primer LLM: {exception_side}."
                )
            else:
                result["decision"] = "SHORT"
                result["confidence"] = round(exception_confidence, 1)
                result["decision_basis"] = "EXCEPTION_UNCERTAIN_DEFAULT_SHORT"
                result["selected_model"] = (exception_vote or {}).get("served_model")
                result["policy_reason"] = (
                    "Hay una anomalía de extremo, pero el primer LLM no entregó una dirección suficientemente "
                    "convencida; se aplica SHORT por la regla de incertidumbre."
                )
        else:
            # Without a verified rejection, Nemotron's continuity role follows the 3/5/8-candle majority.
            continuation_side = str((continuation_vote or {}).get("decision", "WAIT")).upper()
            continuation_confidence = float((continuation_vote or {}).get("confidence", 0) or 0)
            expected_side = "LONG" if trend_direction == "UP" else "SHORT" if trend_direction == "DOWN" else "WAIT"
            required_trend_agreement = _required_trend_agreement(snapshot)
            trend_is_established = expected_side in {"LONG", "SHORT"} and trend_agreement > required_trend_agreement
            if (
                trend_is_established and continuation_side == expected_side
                and continuation_confidence >= min_confidence
            ):
                result["decision"] = expected_side
                result["confidence"] = round(continuation_confidence, 1)
                result["decision_basis"] = "TREND_CONTINUITY_NEMOTRON"
                result["selected_model"] = (continuation_vote or {}).get("served_model")
                result["policy_reason"] = (
                    f"Sin rechazo confirmado, prevalece la continuidad: {trend_direction} en 3/5/8 velas "
                    f"({trend_agreement:.1f}% de acuerdo; mínimo adaptativo {required_trend_agreement:.1f}%) y Nemotron coincide en {expected_side}."
                )
            else:
                result["decision"] = "SHORT"
                result["confidence"] = round(continuation_confidence, 1)
                result["decision_basis"] = "TREND_UNCONFIRMED_DEFAULT_SHORT"
                result["selected_model"] = (continuation_vote or {}).get("served_model")
                result["policy_reason"] = (
                    f"La continuidad no quedó validada: tendencia {trend_direction} ({trend_agreement:.1f}%; mínimo {required_trend_agreement:.1f}%), "
                    f"Nemotron {continuation_side} ({continuation_confidence:.0f}/100); se aplica SHORT por incertidumbre."
                )
        result["consensus_pct"] = round(max(long_votes, short_votes, wait_votes) / len(votes) * 100.0, 1)
        result["long_votes"] = long_votes
        result["short_votes"] = short_votes
        result["wait_votes"] = wait_votes
        result["exception_vote"] = exception_vote.get("decision") if exception_vote else None
        result["continuation_vote"] = continuation_vote.get("decision") if continuation_vote else None
        result["lower_rejection_confirmed"] = lower_rejection
        result["upper_rejection_confirmed"] = upper_rejection
        result["trend_direction"] = trend_direction
        result["trend_agreement_pct"] = round(trend_agreement, 1)
        result["summary"] = (result.get("summary", "") + " · " + result["policy_reason"]).strip(" ·")
    else:
        result["status"] = "error"
        result["decision"] = "WAIT"
        result["summary"] = (
            f"Quórum insuficiente: respondieron {len(votes)} de {required_votes} votos requeridos, "
            f"después de probar {len(attempted_models)} modelo(s)."
        )
        result["risk"] = "No se cambia ni se abre posición sin dos respuestas LLM válidas. Revisa API key, saldo, límites o disponibilidad."
    fast_entry_present = isinstance(snapshot.get("fast_entry"), dict) and bool(snapshot.get("fast_entry"))
    fast_entry = snapshot.get("fast_entry", {}) or {}
    fast_gate_passed = None
    if fast_entry_present and result.get("status") == "ok" and result.get("decision") in {"LONG", "SHORT"}:
        selected_side = str(result["decision"])
        opportunity = (fast_entry.get("opportunities", {}) or {}).get(selected_side, {}) if isinstance(fast_entry, dict) else {}
        fast_gate_passed = bool(opportunity.get("eligible"))
        blockers = opportunity.get("blockers", []) if isinstance(opportunity, dict) else []
        result["pre_fast_gate_decision"] = selected_side
        result["fast_entry_gate_passed"] = fast_gate_passed
        result["fast_entry_gate_side"] = selected_side
        result["fast_entry_gate_mode"] = "ENFORCED" if FAST_ENTRY_ENFORCE_GATE else "ADVISORY"
        result["fast_entry_opportunity"] = opportunity
        result["fast_entry_warnings"] = [str(item) for item in blockers[:8]]
        if not fast_gate_passed and FAST_ENTRY_ENFORCE_GATE:
            result["decision"] = "WAIT"
            result["decision_basis"] = "FAST_ENTRY_QUALITY_BLOCK"
            result["policy_reason"] = (
                f"El comité propuso {selected_side}, pero FAST_ENTRY_ENFORCE_GATE=true y el filtro local no pasó. "
                + (" | ".join(str(item) for item in blockers[:4]) if blockers else "Falta evidencia TP-first.")
            )
        elif not fast_gate_passed:
            result["policy_reason"] = (
                str(result.get("policy_reason", ""))
                + " · Fast-entry queda como diagnóstico, no como veto; advertencias: "
                + (" | ".join(str(item) for item in blockers[:4]) if blockers else "sin elegibilidad cuantitativa completa.")
            ).strip(" ·")
        else:
            hist = opportunity.get("historical", {}) or {}
            result["policy_reason"] = (
                str(result.get("policy_reason", ""))
                + f" · Fast-entry validado: TP-first {hist.get('tp_first_rate_pct', 0):.1f}%, "
                f"Wilson 80% {hist.get('wilson_lower_80_pct', 0):.1f}%, "
                f"presión {opportunity.get('live_confirmation_pct', 0):.1f}/100."
            ).strip(" ·")
    result["fast_entry_gate_passed"] = fast_gate_passed
    result["fast_entry_gate_mode"] = "ENFORCED" if FAST_ENTRY_ENFORCE_GATE else "ADVISORY"
    result["fast_entry_gate_skipped_legacy_snapshot"] = not fast_entry_present

    result.update({
        "valid_models": len(votes),
        "total_models": len(models),
        "required_votes": required_votes,
        "votes": [
            {
                "model": v.get("served_model", v.get("requested_model", "modelo")),
                "requested_model": v.get("requested_model", ""),
                "source": v.get("source", "principal"),
                "analysis_role": v.get("analysis_role", "continuation"),
                "decision": v["decision"], "confidence": v["confidence"],
                "summary": v.get("summary", ""), "risk": v.get("risk", ""),
                "assessment_mode": v.get("assessment_mode", "unknown"),
                "evidence_assessments": v.get("evidence_assessments", {}),
                "evidence_coverage_pct": v.get("evidence_coverage_pct", 0),
                "missing_evidence_sections": v.get("missing_evidence_sections", []),
            }
            for v in votes
        ],
        "models": models[:required_votes],
        "fallback_models": fallback_pool,
        "fallback_used": bool(fallback_used),
        "fallback_used_models": fallback_used,
        "fallback_attempts": attempts,
        "updated_at": int(time.time() * 1000),
        "bar_time": snapshot.get("technical_engine", {}).get("last_candle_time"),
        "price": snapshot.get("price_now"),
        "errors": errors,
        "requests_count": len(attempted_models),
        "minimum_confidence": min_confidence,
        "policy": "Sin anomalía: Nemotron sigue continuidad UP/DOWN cuando el acuerdo de 3/5/8 velas supera el umbral adaptativo; con rechazo confirmado, prevalece el primer LLM; incertidumbre válida => SHORT",
        "required_trend_agreement_pct": _required_trend_agreement(snapshot),
    })
    if not votes:
        result["status"] = "error"
        result["decision"] = "WAIT"
        result["summary"] = "Ningún modelo principal ni de respaldo entregó una respuesta válida."
        result["risk"] = "La señal permanece bloqueada; revisa la API key, el saldo, los límites y la disponibilidad de los modelos."
    if fatal_rate_limit:
        result["risk"] += " Se detuvo la cadena después de dos respuestas HTTP 429 consecutivas para evitar más solicitudes limitadas."
    if votes and errors:
        result["partial_errors"] = True
        if result.get("status") == "ok" and fallback_used:
            result["summary"] = "Se recuperó la evaluación usando respaldo: " + ", ".join(fallback_used) + ". " + result.get("summary", "")
    serialized_context = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    manifest = snapshot.get("context_manifest", {}) or {}
    context_audit = {
        "schema_version": manifest.get("schema_version", "legacy.snapshot"),
        "successful_model_responses": len(votes),
        "attempted_model_requests": len(attempted_models),
        "models_attempted": sorted(attempted_models),
        "same_snapshot_sent_to_each_model": True,
        "payload_bytes": len(serialized_context.encode("utf-8")),
        "payload_sha256": hashlib.sha256(serialized_context.encode("utf-8")).hexdigest(),
        "manifest": manifest,
    }
    result["context_audit"] = context_audit
    for vote in result.get("votes", []):
        vote["context_sha256"] = context_audit["payload_sha256"]
        vote["context_bytes"] = context_audit["payload_bytes"]
    return result


def apply_ai_gate(analysis: dict[str, Any], ai: dict[str, Any], required: bool = True,
                  min_confidence: float = 30.0, max_age_seconds: int = 3600) -> dict[str, Any]:
    """AI may confirm or veto a technical signal, never invent a signal from neutral data."""
    output = dict(analysis)
    output["algorithm_signal"] = analysis.get("signal", "NEUTRAL")
    if not required:
        return output
    if ai.get("status") != "ok" or not ai.get("updated_at"):
        if output.get("signal") in {"LONG", "SHORT"}:
            side = output["signal"]
            output["signal"] = "NEUTRAL"
            output["candidate"] = side
            output["phase"] = "ESPERANDO VALIDACIÓN DE OPENROUTER"
        return output
    age = max(0, int(time.time() * 1000) - int(ai.get("updated_at") or 0)) / 1000
    if age > max_age_seconds:
        if output.get("signal") in {"LONG", "SHORT"}:
            side = output["signal"]
            output["signal"] = "NEUTRAL"
            output["candidate"] = side
            output["phase"] = "EVALUACIÓN DE IA DESACTUALIZADA · ESPERANDO NUEVA LECTURA"
        return output
    snapshot_price = ai.get("price")
    current_price = output.get("current_price")
    if snapshot_price and current_price:
        try:
            drift_pct = abs(float(current_price) / float(snapshot_price) - 1.0) * 100.0
            allowed_drift = max(0.4, float(output.get("atr_pct", 0) or 0) * 1.8)
            if drift_pct > allowed_drift:
                if output.get("signal") in {"LONG", "SHORT"}:
                    side = output["signal"]
                    output["signal"] = "NEUTRAL"
                    output["candidate"] = side
                    output["phase"] = "PRECIO SE ALEJÓ DE LA LECTURA IA · ESPERAR"
                return output
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    base_signal = output.get("signal")
    if base_signal in {"LONG", "SHORT"}:
        ai_side = ai.get("decision")
        confidence = float(ai.get("confidence", 0) or 0)
        if ai_side == base_signal and confidence >= min_confidence:
            output["phase"] = f"{output.get('phase', 'GIRO DETECTADO')} · OPENROUTER CONFIRMA"
            output["reasons"] = list(output.get("reasons", [])) + [
                f"OpenRouter coincide con {base_signal} ({confidence:.0f}/100 de convicción; no es probabilidad calibrada)."
            ]
        else:
            output["signal"] = "NEUTRAL"
            output["candidate"] = base_signal
            output["phase"] = "SEÑAL EN PAUSA · LA IA NO CONFIRMA EL GIRO"
            output["reasons"] = list(output.get("reasons", [])) + [
                f"Motor técnico: {base_signal}; evaluación OpenRouter: {ai_side or 'WAIT'} ({confidence:.0f}/100)."
            ]
    elif output.get("candidate") in {"LONG", "SHORT"}:
        ai_side = ai.get("decision")
        if ai_side == output["candidate"]:
            output["phase"] = "IA COINCIDE CON LA ZONA · FALTA CONFIRMACIÓN TÉCNICA"
        elif ai_side in {"LONG", "SHORT"}:
            output["phase"] = "DESACUERDO ENTRE MOTOR Y MODELOS · ESPERAR"
    return output
