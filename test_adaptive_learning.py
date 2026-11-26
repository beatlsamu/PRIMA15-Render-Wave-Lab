import json
import sqlite3
import unittest

from adaptive_learning import (
    HORIZONS,
    calculate_adaptive_profile,
    evaluate_pending_predictions,
    init_adaptive_db,
    record_ai_prediction,
)


class AdaptiveLearningTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_adaptive_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def _snapshot(self, created_at, price=100.0):
        return {
            "price_now": price,
            "technical_engine": {
                "last_candle_time": created_at // 900_000 * 900_000,
                "long_score": 70,
                "short_score": 30,
                "rsi_14": 38,
                "atr_pct": 0.8,
                "wick_diagnostics": {"lower": {"rejection_confirmed": True}, "upper": {"rejection_confirmed": False}},
            },
            "trend_persistence": {"direction": "UP", "agreement_pct": 66.7},
            "wave_dimensions": {
                "temporal": {"low_alignment_pct": 70, "high_alignment_pct": 10},
                "amplitude": {"bias": "DOWN_WEAKENING"},
                "behavior": {"bias": "HISTORICAL_ANALOGS_UP"},
                "synthesis": {"bullish_count": 3, "bearish_count": 0},
            },
        }

    def _ai(self, created_at, decision="LONG", basis="TREND_CONTINUITY_NEMOTRON"):
        return {
            "status": "ok",
            "decision": decision,
            "decision_basis": basis,
            "updated_at": created_at,
            "votes": [
                {"model": "model-a", "analysis_role": "exception", "decision": "LONG", "confidence": 70},
                {"model": "model-b", "analysis_role": "continuation", "decision": "LONG", "confidence": 75},
            ],
        }

    def test_no_lookahead_excludes_prediction_candle_and_labels_multiple_horizons(self):
        created = 10 * 900_000 + 5 * 60_000
        snapshot = self._snapshot(created)
        record_ai_prediction(self.conn, self._ai(created), snapshot)
        candles = []
        # Candle 10 contains pre-prediction highs/lows and must be excluded.
        for index in range(10, 23):
            open_time = index * 900_000
            candles.append({
                "open_time": open_time,
                "close_time": open_time + 900_000 - 1,
                "open": 100.0, "high": 100.5, "low": 99.8,
                "close": 100.1 + (index - 10) * 0.1,
            })
        inserted = evaluate_pending_predictions(self.conn, candles, now_ms=23 * 900_000)
        self.assertEqual(inserted, len(HORIZONS))
        rows = self.conn.execute(
            "SELECT horizon_bars, market_return_pct, path_json FROM adaptive_outcomes ORDER BY horizon_bars"
        ).fetchall()
        self.assertEqual([row["horizon_bars"] for row in rows], list(HORIZONS))
        path = json.loads(rows[0]["path_json"])
        self.assertEqual(len(path), 1)
        # Re-running the evaluator cannot duplicate horizon labels.
        self.assertEqual(evaluate_pending_predictions(self.conn, candles, now_ms=23 * 900_000), 0)

    def test_learning_stays_in_safe_baseline_until_minimum_sample(self):
        profile = calculate_adaptive_profile(self.conn, min_samples=30)
        self.assertEqual(profile["status"], "LEARNING")
        self.assertEqual(profile["trend_agreement_min_pct"], 60.0)
        self.assertEqual(profile["take_profit_target_pct"], 0.20)
        self.assertEqual(profile["dimension_weights"], {"temporal": 1.0, "amplitude": 1.0, "behavior": 1.0})

    def test_profile_adapts_weights_threshold_tp_and_model_reliability_with_30_samples(self):
        base_time = 1_000_000_000
        for i in range(35):
            created = base_time + i * 20 * 900_000
            # Vary each feature so correlation is measurable rather than undefined.
            x = (i - 17) / 17.0
            features = {
                "temporal": x,
                "amplitude": -x,
                "behavior": x * 0.8,
                "bullish_evidence": 3,
                "bearish_evidence": 0,
            }
            self.conn.execute(
                """INSERT INTO adaptive_predictions
                   (created_at, anchor_bar_time, anchor_price, decision, decision_basis,
                    trend_direction, trend_agreement_pct, anomaly_type, feature_json, votes_json)
                   VALUES (?, ?, ?, 'LONG', 'TREND_CONTINUITY_NEMOTRON', 'UP', 66.7, 'NONE', ?, ?)""",
                (
                    created, created // 900_000 * 900_000, 100.0,
                    json.dumps(features),
                    json.dumps([
                        {"model": "model-a", "analysis_role": "exception", "decision": "LONG"},
                        {"model": "model-b", "analysis_role": "continuation", "decision": "LONG"},
                    ]),
                ),
            )
            prediction_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            market_return = 0.02 + (i / 34.0) * 0.20
            path = [{"h": 100.35, "l": 99.95, "c": 100.0 * (1 + market_return / 100.0)}] * 4
            self.conn.execute(
                """INSERT INTO adaptive_outcomes
                   (prediction_id, horizon_bars, market_return_pct, long_mfe_pct, long_mae_pct,
                    short_mfe_pct, short_mae_pct, path_json, evaluated_at)
                   VALUES (?, 4, ?, 0.35, -0.05, 0.05, -0.35, ?, ?)""",
                (prediction_id, market_return, json.dumps(path), created + 4 * 900_000),
            )
        self.conn.commit()

        profile = calculate_adaptive_profile(self.conn, min_samples=30, lookback=120)
        self.assertEqual(profile["status"], "ADAPTIVE")
        self.assertEqual(profile["sample_count"], 35)
        self.assertEqual(profile["trend_agreement_min_pct"], 58.0)
        self.assertGreater(profile["dimension_weights"]["temporal"], 1.0)
        self.assertLess(profile["dimension_weights"]["amplitude"], 1.0)
        self.assertTrue(0.85 <= profile["dimension_weights"]["behavior"] <= 1.15)
        self.assertEqual(profile["take_profit_target_pct"], 0.30)
        self.assertTrue(any(item["model"] == "model-b" for item in profile["model_reliability"]))

    def test_invalid_ai_result_is_not_recorded(self):
        result = record_ai_prediction(self.conn, {"status": "error", "votes": []}, self._snapshot(900_000))
        self.assertIsNone(result)
        count = self.conn.execute("SELECT COUNT(*) FROM adaptive_predictions").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
