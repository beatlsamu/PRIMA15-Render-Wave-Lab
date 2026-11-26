"""Bounded, walk-forward learning for PRIMA-15.

This module never rewrites code or model prompts dynamically. It records each
valid committee snapshot, labels it only with fully closed candles that began
after the prediction, and adjusts a small set of bounded strategy parameters.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from statistics import mean
from typing import Any

INTERVAL_MS = 15 * 60 * 1000
HORIZONS = (1, 2, 4, 8, 12)
MIN_ADAPTIVE_SAMPLES = max(20, min(100, int(__import__("os").getenv("ADAPTIVE_MIN_SAMPLES", "30"))))
LOOKBACK = max(40, min(300, int(__import__("os").getenv("ADAPTIVE_LOOKBACK", "120"))))
DEFAULT_TP = max(0.10, min(0.50, float(__import__("os").getenv("SIM_TAKE_PROFIT_PCT", "0.20"))))
MIN_TP = max(0.10, min(DEFAULT_TP, float(__import__("os").getenv("ADAPTIVE_TP_MIN_PCT", "0.20"))))
MAX_TP = max(DEFAULT_TP, min(0.50, float(__import__("os").getenv("ADAPTIVE_TP_MAX_PCT", "0.30"))))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def init_adaptive_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS adaptive_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL UNIQUE,
            anchor_bar_time INTEGER NOT NULL DEFAULT 0,
            anchor_price REAL NOT NULL,
            decision TEXT NOT NULL,
            decision_basis TEXT NOT NULL DEFAULT '',
            trend_direction TEXT NOT NULL DEFAULT 'RANGE',
            trend_agreement_pct REAL NOT NULL DEFAULT 0,
            anomaly_type TEXT NOT NULL DEFAULT 'NONE',
            feature_json TEXT NOT NULL,
            votes_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_adaptive_predictions_time
            ON adaptive_predictions(created_at DESC);
        CREATE TABLE IF NOT EXISTS adaptive_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id INTEGER NOT NULL,
            horizon_bars INTEGER NOT NULL,
            market_return_pct REAL NOT NULL,
            long_mfe_pct REAL NOT NULL,
            long_mae_pct REAL NOT NULL,
            short_mfe_pct REAL NOT NULL,
            short_mae_pct REAL NOT NULL,
            path_json TEXT NOT NULL,
            evaluated_at INTEGER NOT NULL,
            UNIQUE(prediction_id, horizon_bars),
            FOREIGN KEY(prediction_id) REFERENCES adaptive_predictions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_adaptive_outcomes_horizon
            ON adaptive_outcomes(horizon_bars, evaluated_at DESC);
        CREATE TABLE IF NOT EXISTS adaptive_profile_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            profile_hash TEXT NOT NULL UNIQUE,
            profile_json TEXT NOT NULL,
            reason TEXT NOT NULL
        );
        """
    )


def _dimension_features(snapshot: dict[str, Any]) -> dict[str, float]:
    dims = snapshot.get("wave_dimensions", {}) or {}
    temporal = dims.get("temporal", {}) or {}
    amplitude = dims.get("amplitude", {}) or {}
    behavior = dims.get("behavior", {}) or {}
    synthesis = dims.get("synthesis", {}) or {}

    low = _safe_float(temporal.get("low_alignment_pct"))
    high = _safe_float(temporal.get("high_alignment_pct"))
    temporal_score = max(-1.0, min(1.0, (low - high) / 100.0))
    amplitude_bias = str(amplitude.get("bias", ""))
    amplitude_map = {
        "DOWN_WEAKENING": 1.0,
        "UP_WEAKENING": -1.0,
        "DOWN_STRENGTHENING": -1.0,
        "UP_STRENGTHENING": 1.0,
    }
    amplitude_score = amplitude_map.get(amplitude_bias, 0.0)

    behavior_bias = str(behavior.get("bias", ""))
    behavior_map = {
        "HISTORICAL_ANALOGS_UP": 1.0,
        "CURRENT_PATTERN_UP": 0.5,
        "REVERSAL_RISK_DOWN": 1.0,
        "HISTORICAL_ANALOGS_DOWN": -1.0,
        "CURRENT_PATTERN_DOWN": -0.5,
        "REVERSAL_RISK_UP": -1.0,
    }
    behavior_score = behavior_map.get(behavior_bias, 0.0)
    if behavior_score == 0.0:
        analogs = behavior.get("analogs", {}) or {}
        returns = [
            _safe_float(row.get("weighted_return_pct"))
            for row in analogs.values()
            if row.get("weighted_return_pct") is not None
        ]
        if returns:
            behavior_score = max(-1.0, min(1.0, mean(returns) / 0.30))

    return {
        "temporal": round(temporal_score, 5),
        "amplitude": round(amplitude_score, 5),
        "behavior": round(behavior_score, 5),
        "bullish_evidence": float(synthesis.get("bullish_count", 0) or 0),
        "bearish_evidence": float(synthesis.get("bearish_count", 0) or 0),
    }


def record_ai_prediction(
    conn: sqlite3.Connection,
    ai_result: dict[str, Any],
    snapshot: dict[str, Any],
) -> int | None:
    """Persist one valid committee result and the exact inputs it saw."""
    if ai_result.get("status") != "ok":
        return None
    decision = str(ai_result.get("decision", "WAIT")).upper()
    if decision not in {"LONG", "SHORT", "WAIT"}:
        return None
    votes = ai_result.get("votes", []) or []
    if len(votes) < 2:
        return None
    price = _safe_float(snapshot.get("price_now"))
    if price <= 0:
        return None

    technical = snapshot.get("technical_engine", {}) or {}
    trend = snapshot.get("trend_persistence", {}) or {}
    wick = technical.get("wick_diagnostics", {}) or {}
    lower = (wick.get("lower", {}) or {}).get("rejection_confirmed", False)
    upper = (wick.get("upper", {}) or {}).get("rejection_confirmed", False)
    anomaly = "LOWER_REJECTION" if lower and not upper else "UPPER_REJECTION" if upper and not lower else "CONFLICT" if lower and upper else "NONE"
    features = _dimension_features(snapshot)
    features.update({
        "long_score": _safe_float(technical.get("long_score")),
        "short_score": _safe_float(technical.get("short_score")),
        "rsi": _safe_float(technical.get("rsi_14"), 50.0),
        "atr_pct": _safe_float(technical.get("atr_pct")),
        "trend_agreement_pct": _safe_float(trend.get("agreement_pct")),
    })
    created_at = int(ai_result.get("updated_at") or time.time() * 1000)
    anchor_bar_time = int(technical.get("last_candle_time") or 0)
    cursor = conn.execute(
        """INSERT OR IGNORE INTO adaptive_predictions
           (created_at, anchor_bar_time, anchor_price, decision, decision_basis,
            trend_direction, trend_agreement_pct, anomaly_type, feature_json, votes_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            created_at, anchor_bar_time, price, decision,
            str(ai_result.get("decision_basis", "")),
            str(trend.get("direction", "RANGE")),
            _safe_float(trend.get("agreement_pct")), anomaly,
            json.dumps(features, ensure_ascii=False, separators=(",", ":")),
            json.dumps(votes, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    if cursor.rowcount == 0:
        row = conn.execute("SELECT id FROM adaptive_predictions WHERE created_at=?", (created_at,)).fetchone()
        return int(row["id"]) if row else None
    return int(cursor.lastrowid)


def evaluate_pending_predictions(
    conn: sqlite3.Connection,
    closed_candles: list[dict[str, Any]],
    now_ms: int | None = None,
) -> int:
    """Label predictions from fully closed candles that started after the snapshot."""
    if not closed_candles:
        return 0
    now_ms = int(now_ms or time.time() * 1000)
    rows = conn.execute(
        "SELECT * FROM adaptive_predictions ORDER BY created_at DESC LIMIT 500"
    ).fetchall()
    inserted = 0
    ordered = sorted(closed_candles, key=lambda c: int(c.get("open_time", 0)))
    for prediction in rows:
        created_at = int(prediction["created_at"])
        # Exclude the still-forming candle in which the LLM was called. This avoids
        # using highs/lows that may have happened before the forecast was made.
        current_bar_open = (created_at // INTERVAL_MS) * INTERVAL_MS
        future = [
            candle for candle in ordered
            if int(candle.get("open_time", 0)) > current_bar_open
            and int(candle.get("close_time", int(candle.get("open_time", 0)) + INTERVAL_MS - 1)) <= now_ms
        ]
        if len(future) < 1:
            continue
        anchor = _safe_float(prediction["anchor_price"])
        if anchor <= 0:
            continue
        for horizon in HORIZONS:
            if len(future) < horizon:
                continue
            already = conn.execute(
                "SELECT 1 FROM adaptive_outcomes WHERE prediction_id=? AND horizon_bars=?",
                (int(prediction["id"]), horizon),
            ).fetchone()
            if already:
                continue
            path = future[:horizon]
            final_close = _safe_float(path[-1].get("close"))
            if final_close <= 0:
                continue
            max_high = max(_safe_float(c.get("high")) for c in path)
            min_low = min(_safe_float(c.get("low")) for c in path)
            market_return = (final_close / anchor - 1.0) * 100.0
            long_mfe = max(0.0, (max_high / anchor - 1.0) * 100.0)
            long_mae = min(0.0, (min_low / anchor - 1.0) * 100.0)
            short_mfe = max(0.0, (anchor / min_low - 1.0) * 100.0) if min_low > 0 else 0.0
            short_mae = min(0.0, (anchor / max_high - 1.0) * 100.0) if max_high > 0 else 0.0
            compact_path = [
                {
                    "h": round(_safe_float(c.get("high")), 10),
                    "l": round(_safe_float(c.get("low")), 10),
                    "c": round(_safe_float(c.get("close")), 10),
                }
                for c in path
            ]
            conn.execute(
                """INSERT OR IGNORE INTO adaptive_outcomes
                   (prediction_id, horizon_bars, market_return_pct, long_mfe_pct,
                    long_mae_pct, short_mfe_pct, short_mae_pct, path_json, evaluated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(prediction["id"]), horizon, market_return, long_mfe,
                    long_mae, short_mfe, short_mae,
                    json.dumps(compact_path, separators=(",", ":")), now_ms,
                ),
            )
            inserted += 1
    return inserted


def _pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3 or len(xs) != len(ys):
        return 0.0
    mx, my = mean(xs), mean(ys)
    dx = [v - mx for v in xs]
    dy = [v - my for v in ys]
    denom = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    if denom <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(dx, dy)) / denom))


def _simulate_target(side: str, entry: float, path: list[dict[str, Any]], target_pct: float, terminal_return: float) -> float:
    """Conservative one-target replay: first future candle touching the target exits there.

    There is no stop in the current paper strategy. If a target is not reached
    within the four-bar observation window, the test closes at the final close.
    """
    if entry <= 0:
        return terminal_return
    threshold = target_pct / 100.0
    for candle in path:
        high = _safe_float(candle.get("h"))
        low = _safe_float(candle.get("l"))
        if side == "LONG" and high >= entry * (1.0 + threshold):
            return target_pct
        if side == "SHORT" and low <= entry * (1.0 - threshold):
            return target_pct
    return terminal_return


def calculate_adaptive_profile(
    conn: sqlite3.Connection,
    min_samples: int = MIN_ADAPTIVE_SAMPLES,
    lookback: int = LOOKBACK,
    opening_fee_pct: float = 0.035,
    position_size_pct: float = 100.0,
) -> dict[str, Any]:
    """Return conservative parameters learned from recent, fully labelled snapshots."""
    rows = conn.execute(
        """SELECT p.*, o.market_return_pct, o.long_mfe_pct, o.long_mae_pct,
                  o.short_mfe_pct, o.short_mae_pct, o.path_json
           FROM adaptive_predictions p
           JOIN adaptive_outcomes o ON o.prediction_id=p.id AND o.horizon_bars=4
           ORDER BY p.created_at DESC LIMIT ?""",
        (max(1, min(500, int(lookback))),),
    ).fetchall()
    valid_rows = [r for r in rows if r["decision"] in {"LONG", "SHORT"}]
    n = len(valid_rows)
    directed_returns = [
        _safe_float(r["market_return_pct"]) * (1.0 if r["decision"] == "LONG" else -1.0)
        for r in valid_rows
    ]
    wins = sum(1 for value in directed_returns if value > 0)
    accuracy = wins / n * 100.0 if n else None
    avg_return = mean(directed_returns) if directed_returns else None

    trend_rows = [r for r in valid_rows if r["decision_basis"] == "TREND_CONTINUITY_NEMOTRON"]
    trend_n = len(trend_rows)
    trend_accuracy = (
        sum(
            1 for r in trend_rows
            if (_safe_float(r["market_return_pct"]) * (1 if r["decision"] == "LONG" else -1)) > 0
        ) / trend_n * 100.0 if trend_n else None
    )
    trend_threshold = 60.0
    if trend_n >= min_samples and trend_accuracy is not None:
        # Better validated continuity permits a slightly lower threshold; poor
        # recent continuity demands more agreement. Movement is bounded 58–72%.
        if trend_accuracy >= 58.0:
            trend_threshold = 58.0
        elif trend_accuracy <= 45.0:
            trend_threshold = 72.0
        else:
            trend_threshold = 72.0 - ((trend_accuracy - 45.0) / 13.0) * 14.0

    dimension_weights = {"temporal": 1.0, "amplitude": 1.0, "behavior": 1.0}
    dimension_correlations: dict[str, dict[str, Any]] = {}
    if n >= min_samples:
        for dimension in dimension_weights:
            xs: list[float] = []
            ys: list[float] = []
            for row in valid_rows:
                try:
                    features = json.loads(row["feature_json"] or "{}")
                except (ValueError, TypeError):
                    features = {}
                xs.append(_safe_float(features.get(dimension)))
                ys.append(_safe_float(row["market_return_pct"]))
            correlation = _pearson(xs, ys)
            shrink = n / (n + 30.0)
            weight = max(0.85, min(1.15, 1.0 + correlation * shrink * 0.20))
            dimension_weights[dimension] = round(weight, 4)
            dimension_correlations[dimension] = {
                "correlation": round(correlation, 4),
                "weight": round(weight, 4),
                "samples": n,
            }

    tp_samples = valid_rows
    tp_target = DEFAULT_TP
    tp_comparison: dict[str, Any] = {
        "0.20": None,
        "0.30": None,
        "method": f"Se activa con al menos {min_samples} resultados completos a 4 velas; mientras tanto se conserva el TP base configurado.",
    }
    if len(tp_samples) >= min_samples:
        fee = max(0.0, _safe_float(opening_fee_pct))
        size = max(1.0, min(100.0, _safe_float(position_size_pct, 100.0))) / 100.0
        targets = sorted({MIN_TP, MAX_TP})
        net_scores: dict[float, list[float]] = {target: [] for target in targets}
        for row in tp_samples:
            side = str(row["decision"])
            entry = _safe_float(row["anchor_price"])
            market_ret = _safe_float(row["market_return_pct"])
            terminal = market_ret if side == "LONG" else -market_ret
            try:
                path = json.loads(row["path_json"] or "[]")
            except (ValueError, TypeError):
                path = []
            if not path:
                continue
            for target in targets:
                gross = _simulate_target(side, entry, path, target, terminal)
                # Net effect on the 100.000% starting wallet index, including
                # the one-time opening fee but never a close fee.
                net_points = gross * size * (1.0 - fee / 100.0) - fee
                net_scores[target].append(net_points)
        for target, values in net_scores.items():
            tp_comparison[f"{target:.2f}"] = {
                "samples": len(values),
                "mean_net_wallet_points_pct": round(mean(values), 5) if values else None,
                "positive_share_pct": round(sum(v > 0 for v in values) / len(values) * 100.0, 1) if values else None,
            }
        if len(targets) == 2 and all(net_scores[target] for target in targets):
            low_target, high_target = targets
            if mean(net_scores[high_target]) > mean(net_scores[low_target]) + 0.005:
                tp_target = high_target
            else:
                tp_target = low_target

    model_rows: dict[str, dict[str, Any]] = {}
    for row in valid_rows:
        try:
            votes = json.loads(row["votes_json"] or "[]")
        except (ValueError, TypeError):
            votes = []
        market_ret = _safe_float(row["market_return_pct"])
        for vote in votes if isinstance(votes, list) else []:
            side = str(vote.get("decision", "")).upper()
            if side not in {"LONG", "SHORT"}:
                continue
            model = str(vote.get("model") or vote.get("served_model") or vote.get("requested_model") or "unknown")
            role = str(vote.get("analysis_role", "unknown"))
            key = f"{model}|{role}"
            entry = model_rows.setdefault(key, {"model": model, "role": role, "samples": 0, "wins": 0, "returns": []})
            directional = market_ret if side == "LONG" else -market_ret
            entry["samples"] += 1
            entry["wins"] += int(directional > 0)
            entry["returns"].append(directional)
    model_reliability = []
    for entry in model_rows.values():
        samples = int(entry["samples"])
        if samples >= 10:
            model_reliability.append({
                "model": entry["model"], "role": entry["role"], "samples": samples,
                "win_rate_pct": round(entry["wins"] / samples * 100.0, 1),
                "avg_directional_return_pct": round(mean(entry["returns"]), 5),
            })
    model_reliability.sort(key=lambda item: (item["win_rate_pct"], item["avg_directional_return_pct"]), reverse=True)

    profile = {
        "status": "ADAPTIVE" if n >= min_samples else "LEARNING",
        "learning_ready": n >= min_samples,
        "sample_count": n,
        "lookback_limit": int(lookback),
        "min_samples": int(min_samples),
        "directional_accuracy_pct": round(accuracy, 1) if accuracy is not None else None,
        "avg_directional_return_pct": round(avg_return, 5) if avg_return is not None else None,
        "trend_samples": trend_n,
        "trend_accuracy_pct": round(trend_accuracy, 1) if trend_accuracy is not None else None,
        "trend_agreement_min_pct": round(trend_threshold, 1),
        "tp_samples": len(tp_samples),
        "take_profit_target_pct": round(tp_target, 3),
        "tp_comparison": tp_comparison,
        "dimension_weights": dimension_weights,
        "dimension_correlations": dimension_correlations,
        "model_reliability": model_reliability[:8],
        "guardrails": {
            "parameters_bounded": True,
            "trend_threshold_range_pct": [58.0, 72.0],
            "dimension_weight_range": [0.85, 1.15],
            "take_profit_candidates_pct": [MIN_TP, MAX_TP],
            "no_code_self_modification": True,
            "only_fully_closed_future_candles": True,
        },
    }
    return profile


def save_profile_if_changed(conn: sqlite3.Connection, profile: dict[str, Any], reason: str = "walk-forward refresh") -> bool:
    stable = {
        "status": profile.get("status"),
        "sample_count": profile.get("sample_count"),
        "trend_agreement_min_pct": profile.get("trend_agreement_min_pct"),
        "take_profit_target_pct": profile.get("take_profit_target_pct"),
        "dimension_weights": profile.get("dimension_weights"),
    }
    serialized = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    # A compact deterministic hash without external dependencies.
    import hashlib
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    cursor = conn.execute(
        "INSERT OR IGNORE INTO adaptive_profile_history(created_at, profile_hash, profile_json, reason) VALUES (?, ?, ?, ?)",
        (int(time.time() * 1000), digest, json.dumps(profile, ensure_ascii=False, separators=(",", ":")), reason[:200]),
    )
    return cursor.rowcount > 0


def recent_profile_history(conn: sqlite3.Connection, limit: int = 8) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT created_at, profile_json, reason FROM adaptive_profile_history ORDER BY id DESC LIMIT ?",
        (max(1, min(30, int(limit))),),
    ).fetchall()
    result = []
    for row in rows:
        try:
            profile = json.loads(row["profile_json"])
        except (ValueError, TypeError):
            profile = {}
        result.append({"created_at": int(row["created_at"]), "reason": row["reason"], "profile": profile})
    return result


def recent_prediction_memory(
    conn: sqlite3.Connection, limit: int = 8,
) -> list[dict[str, Any]]:
    """Return recent prior LLM decisions with their already-observed future outcomes.

    This is historical context only: outcomes are included only after the candles
    have closed and been written to adaptive_outcomes. No current/future candle is
    added to a past prediction.
    """
    safe_limit = max(1, min(20, int(limit)))
    predictions = conn.execute(
        "SELECT * FROM adaptive_predictions ORDER BY created_at DESC LIMIT ?",
        (safe_limit,),
    ).fetchall()
    memory: list[dict[str, Any]] = []
    for row in predictions:
        prediction_id = int(row["id"])
        outcomes = conn.execute(
            """SELECT horizon_bars, market_return_pct, long_mfe_pct, long_mae_pct,
                      short_mfe_pct, short_mae_pct, evaluated_at
               FROM adaptive_outcomes WHERE prediction_id=? ORDER BY horizon_bars""",
            (prediction_id,),
        ).fetchall()
        if not outcomes:
            continue
        try:
            features = json.loads(row["feature_json"] or "{}")
        except (TypeError, ValueError):
            features = {}
        try:
            votes = json.loads(row["votes_json"] or "[]")
        except (TypeError, ValueError):
            votes = []
        memory.append({
            "prediction_id": prediction_id,
            "created_at": int(row["created_at"]),
            "anchor_bar_time": int(row["anchor_bar_time"]),
            "decision": str(row["decision"]),
            "decision_basis": str(row["decision_basis"]),
            "trend_direction": str(row["trend_direction"]),
            "trend_agreement_pct": round(float(row["trend_agreement_pct"]), 2),
            "anomaly_type": str(row["anomaly_type"]),
            "features": features,
            "model_votes": [
                {
                    "model": str(v.get("model", v.get("served_model", v.get("requested_model", "unknown")))),
                    "role": str(v.get("analysis_role", "unknown")),
                    "decision": str(v.get("decision", "WAIT")),
                    "confidence": float(v.get("confidence", 0) or 0),
                    "summary": str(v.get("summary", ""))[:300],
                    "risk": str(v.get("risk", ""))[:220],
                    "evidence_coverage_pct": float(v.get("evidence_coverage_pct", 0) or 0),
                    "evidence_assessments": v.get("evidence_assessments", {}),
                }
                for v in votes if isinstance(v, dict)
            ],
            "observed_outcomes": [
                {
                    "horizon_bars": int(o["horizon_bars"]),
                    "market_return_pct": round(float(o["market_return_pct"]), 5),
                    "long_mfe_pct": round(float(o["long_mfe_pct"]), 5),
                    "long_mae_pct": round(float(o["long_mae_pct"]), 5),
                    "short_mfe_pct": round(float(o["short_mfe_pct"]), 5),
                    "short_mae_pct": round(float(o["short_mae_pct"]), 5),
                }
                for o in outcomes
            ],
        })
    return memory
