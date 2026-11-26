import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import (
    analyze,
    analyze_amplitude_dimension,
    analyze_behavior_dimension,
    analyze_temporal_dimension,
    analyze_wick_context,
    detect_pivots,
    prime_windows_for_length,
)
from test_engine import synthetic_candles


class ThreeDimensionalWaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candles = synthetic_candles(1500)

    def test_prime_windows_are_generated_dynamically(self):
        windows = prime_windows_for_length(300)
        self.assertIn(127, windows)
        self.assertIn(131, windows)
        self.assertIn(233, windows)
        self.assertNotIn(121, windows)
        self.assertEqual(windows, sorted(windows))

    def test_temporal_dimension_reports_multiple_prime_scales(self):
        pivots = detect_pivots(self.candles[-300:])
        result = analyze_temporal_dimension(self.candles[-300:], pivots)
        self.assertEqual(result["status"], "OK")
        self.assertGreaterEqual(result["scale_count"], 20)
        self.assertIn(result["bias"], {
            "LOW_TEMPORAL_CONFLUENCE", "HIGH_TEMPORAL_CONFLUENCE",
            "LOW_ZONE", "HIGH_ZONE", "NO_CLEAR_ALIGNMENT",
        })
        for row in result["scales"]:
            self.assertGreaterEqual(row["p_close"], -2.0)
            self.assertLessEqual(row["p_close"], 3.0)

    def test_amplitude_dimension_uses_percentage_swing_sizes(self):
        candles = self.candles[-300:]
        pivots = detect_pivots(candles)
        result = analyze_amplitude_dimension(candles, pivots)
        self.assertEqual(result["status"], "OK")
        self.assertGreater(len(result["legs"]), 0)
        latest = result["latest_leg"]
        self.assertGreaterEqual(latest["amplitude_pct"], 0.0)
        self.assertGreater(latest["duration_bars"], 0)
        self.assertIn(result["phase"], {
            "SIN_COMPARABLE_DIRECTION", "EXPANSION_DE_AMPLITUD",
            "CONTRACCION_DE_AMPLITUD", "AMPLITUD_ESTABLE",
        })

    def test_behavior_analogs_are_strictly_past_and_have_multiple_horizons(self):
        candles = self.candles[-300:]
        result = analyze_behavior_dimension(candles)
        self.assertEqual(result["status"], "OK")
        self.assertTrue(result["no_lookahead"])
        self.assertEqual(result["pattern_bars"], 13)
        self.assertEqual(set(result["analogs"]), {"4", "8", "12"})
        current_start = result["current_pattern_start_index"]
        for horizon, row in result["analogs"].items():
            for case in row["cases"]:
                self.assertLess(case["end_index"] + int(horizon), current_start)

    def test_analyze_exposes_all_three_dimensions(self):
        result = analyze(self.candles[-300:], self.candles[-1]["close"])
        dimensions = result["wave_dimensions"]
        self.assertEqual(set(dimensions), {"temporal", "amplitude", "behavior", "synthesis"})
        self.assertIn(dimensions["synthesis"]["interpretation"], {
            "CONFLUENCIA_PARCIAL", "CONFLUENCIA_ALCISTA", "CONFLUENCIA_BAJISTA",
            "DIMENSIONES_EN_CONFLICTO", "SIN_CONFLUENCIA",
        })

    def test_rejected_upper_wick_is_classified_with_reasons(self):
        candles = []
        for i in range(80):
            candles.append({
                "open_time": i * 900_000, "close_time": 1,
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.0, "volume": 100.0,
            })
        candles[-1].update({
            "open": 100.2, "high": 101.5, "low": 99.7,
            "close": 99.8, "volume": 250.0,
        })
        pivots = [{"type": "HIGH", "index": 79, "price": 101.5, "rsi": 66.0}]
        result = analyze_wick_context(
            candles=candles, pivots=pivots, current_price=99.8,
            atr=1.0, rsi=66.0, momentum_long=False, momentum_short=True,
            long_divergence=False, short_divergence=True,
            prime_long_delta=9, prime_short_delta=1,
        )
        self.assertTrue(result["upper"]["shape"])
        self.assertTrue(result["upper"]["location_confirmed"])
        self.assertTrue(result["upper"]["rejection_confirmed"])
        self.assertIn("MAXIMO_ESTRUCTURAL", result["classification"])
        self.assertGreater(result["upper_wick_pct_range"], 50.0)

    def test_lower_wick_without_support_reclaim_is_not_confirmed_rejection(self):
        candles = []
        for i in range(80):
            candles.append({
                "open_time": i * 900_000, "close_time": 1,
                "open": 95.0, "high": 100.0, "low": 90.0,
                "close": 96.0, "volume": 100.0,
            })
        # Visually impressive lower wick in the lower part of the broader range,
        # but it never touched/reclaimed the prior support at 90.0 (tolerance 0.2).
        candles[-1].update({
            "open": 97.5, "high": 98.0, "low": 91.0,
            "close": 97.0, "volume": 260.0,
        })
        result = analyze_wick_context(
            candles=candles, pivots=[], current_price=97.0,
            atr=1.0, rsi=34.0, momentum_long=True, momentum_short=False,
            long_divergence=True, short_divergence=False,
            prime_long_delta=1, prime_short_delta=9,
        )
        self.assertTrue(result["lower"]["shape"])
        self.assertTrue(result["lower"]["location_confirmed"])
        self.assertFalse(result["lower"]["support_reclaim_confirmed"])
        self.assertFalse(result["lower"]["rejection_confirmed"])
        self.assertIn("no cuenta como rechazo confirmado", " ".join(result["lower"]["blockers"]).lower())

    def test_open_candle_wick_is_only_provisional(self):
        import time
        candles = []
        for i in range(80):
            candles.append({
                "open_time": i * 900_000, "close_time": 1,
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.0, "volume": 100.0,
            })
        candles[-1].update({
            "open": 99.8, "high": 100.3, "low": 98.5,
            "close": 100.2, "volume": 250.0,
            "close_time": int(time.time() * 1000) + 600_000,
        })
        result = analyze_wick_context(
            candles=candles, pivots=[], current_price=100.2,
            atr=1.0, rsi=34.0, momentum_long=True, momentum_short=False,
            long_divergence=True, short_divergence=False,
            prime_long_delta=1, prime_short_delta=9,
        )
        self.assertFalse(result["candle_closed"])
        self.assertFalse(result["lower"]["rejection_confirmed"])
        self.assertIn("PROVISIONAL", result["lower"]["classification"])

    def test_rejected_lower_wick_is_classified_with_reasons(self):
        candles = []
        for i in range(80):
            candles.append({
                "open_time": i * 900_000,
                "close_time": 1,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 100.0,
            })
        candles[-1].update({
            "open": 99.8, "high": 100.3, "low": 98.5,
            "close": 100.2, "volume": 250.0,
        })
        pivots = [{"type": "LOW", "index": 79, "price": 98.5, "rsi": 34.0}]
        result = analyze_wick_context(
            candles=candles, pivots=pivots, current_price=100.2,
            atr=1.0, rsi=34.0, momentum_long=True, momentum_short=False,
            long_divergence=True, short_divergence=False,
            prime_long_delta=1, prime_short_delta=9,
        )
        self.assertTrue(result["candle_closed"])
        self.assertTrue(result["lower"]["shape"])
        self.assertTrue(result["lower"]["location_confirmed"])
        self.assertTrue(result["lower"]["rejection_confirmed"])
        self.assertIn("MECHA", result["classification"])
        self.assertGreater(result["lower_wick_pct_range"], 50.0)
        self.assertTrue(result["lower"]["reasons"])


if __name__ == "__main__":
    unittest.main()
