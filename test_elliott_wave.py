import math
import random
import unittest

from elliott_wave import (
    aggregate_candles,
    analyze_multi_degree_elliott,
    evaluate_impulse_pivots,
    zigzag_pivots,
)


def pivot(kind, price, index):
    return {
        "type": kind,
        "price": float(price),
        "index": index,
        "time": index * 900_000,
        "confirmed": True,
        "confirmed_at": (index + 1) * 900_000,
    }


def make_candles(count=1200, interval_ms=900_000, seed=10):
    random.seed(seed)
    rows = []
    last = 5.0
    for i in range(count):
        # Multiple components create swings of different sizes and time degrees.
        target = 5.0 + 0.18 * math.sin(i / 8.0) + 0.35 * math.sin(i / 37.0) + 0.08 * math.sin(i / 130.0)
        last += (target - last) * 0.25 + random.uniform(-0.012, 0.012)
        open_price = last + random.uniform(-0.004, 0.004)
        close = last + random.uniform(-0.005, 0.005)
        high = max(open_price, close) + random.uniform(0.004, 0.015)
        low = min(open_price, close) - random.uniform(0.004, 0.015)
        opened = i * interval_ms
        rows.append({
            "open_time": opened,
            "close_time": opened + interval_ms - 1,
            "open": open_price, "high": high, "low": low, "close": close,
            "volume": random.uniform(10, 100), "quote_volume": random.uniform(50, 500),
            "trades": 10,
        })
    return rows


class ElliottWaveTests(unittest.TestCase):
    def test_valid_bullish_impulse_obeys_hard_rules(self):
        seq = [
            pivot("LOW", 100, 0), pivot("HIGH", 110, 11),
            pivot("LOW", 104, 18), pivot("HIGH", 120, 31),
            pivot("LOW", 114, 44), pivot("HIGH", 126, 57),
        ]
        result = evaluate_impulse_pivots(seq, "UP")
        self.assertIsNotNone(result)
        self.assertTrue(result["valid"])
        self.assertEqual(result["pattern"], "STANDARD_IMPULSE_5_WAVES")
        self.assertTrue(all(result["hard_rules"].values()))
        self.assertEqual([leg["duration_bars"] for leg in result["legs"]], [11, 7, 13, 13, 13])

    def test_valid_bearish_impulse_obeys_hard_rules(self):
        seq = [
            pivot("HIGH", 126, 0), pivot("LOW", 114, 11),
            pivot("HIGH", 120, 18), pivot("LOW", 104, 31),
            pivot("HIGH", 110, 44), pivot("LOW", 98, 57),
        ]
        result = evaluate_impulse_pivots(seq, "DOWN")
        self.assertTrue(result["valid"])
        self.assertEqual(result["direction"], "DOWN")

    def test_wave_two_breaking_origin_invalidates_impulse(self):
        seq = [
            pivot("LOW", 100, 0), pivot("HIGH", 110, 11),
            pivot("LOW", 99, 18), pivot("HIGH", 120, 31),
            pivot("LOW", 114, 44), pivot("HIGH", 126, 57),
        ]
        result = evaluate_impulse_pivots(seq, "UP")
        self.assertFalse(result["valid"])
        self.assertIn("wave_2_does_not_exceed_wave_1_origin", result["violations"])

    def test_wave_three_shortest_invalidates_impulse(self):
        seq = [
            pivot("LOW", 100, 0), pivot("HIGH", 110, 11),
            pivot("LOW", 105, 18), pivot("HIGH", 112, 31),
            pivot("LOW", 108, 44), pivot("HIGH", 116, 57),
        ]
        result = evaluate_impulse_pivots(seq, "UP")
        self.assertFalse(result["valid"])
        self.assertIn("wave_3_is_not_shortest", result["violations"])

    def test_wave_four_overlap_invalidates_standard_impulse(self):
        seq = [
            pivot("LOW", 100, 0), pivot("HIGH", 110, 11),
            pivot("LOW", 104, 18), pivot("HIGH", 120, 31),
            pivot("LOW", 108, 44), pivot("HIGH", 128, 57),
        ]
        result = evaluate_impulse_pivots(seq, "UP")
        self.assertFalse(result["valid"])
        self.assertIn("wave_4_does_not_overlap_wave_1", result["violations"])

    def test_aggregation_preserves_ohlcv_and_omits_incomplete_or_gapped_groups(self):
        candles = make_candles(8)
        candles[5]["open_time"] += 900_000  # create a gap in the second group
        result = aggregate_candles(candles, 4)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["open"], candles[0]["open"])
        self.assertEqual(result[0]["close"], candles[3]["close"])
        self.assertEqual(result[0]["high"], max(row["high"] for row in candles[:4]))
        self.assertEqual(result[0]["low"], min(row["low"] for row in candles[:4]))

    def test_zigzag_pivots_are_confirmed_only_after_reversal_and_open_extreme_is_provisional(self):
        candles = []
        prices = [100 + i * 0.3 for i in range(45)]
        for i, close in enumerate(prices):
            opened = close - 0.02
            candles.append({"open_time": i * 60_000, "high": close + 0.02, "low": opened - 0.02,
                            "close": close, "close_time": (i + 1) * 60_000 - 1})
        out = zigzag_pivots(candles, floor_pct=0.1, atr_multiplier=1.0)
        self.assertTrue(out["pivots"])
        self.assertTrue(all(row["confirmed"] for row in out["pivots"]))
        self.assertIsNotNone(out["provisional_pivot"])
        self.assertFalse(out["provisional_pivot"]["confirmed"])

    def test_multi_degree_analysis_processes_full_15m_history_and_returns_four_degrees(self):
        m15 = make_candles(5000, 900_000, 13)
        m1 = make_candles(3000, 60_000, 14)
        result = analyze_multi_degree_elliott(m15, m1, {"signal": "NEUTRAL"})
        self.assertEqual(result["history"]["15m_candles_processed"], 5000)
        self.assertEqual(result["history"]["1m_candles_processed"], 3000)
        self.assertEqual(set(result["timeframes"]), {"1m", "15m", "1h", "4h"})
        self.assertEqual(result["history"]["1h_candles_aggregated"], 1250)
        self.assertEqual(result["history"]["4h_candles_aggregated"], 312)
        self.assertIn(result["multi_degree_synthesis"]["direction"], {"UP", "DOWN", "MIXED", "UNDETERMINED"})
        self.assertIn(result["prime_elliott_relation"]["state"], {
            "ELLIOTT_IMPULSE_AND_PRIME_CONFLUENCE", "ELLIOTT_PROVISIONAL_AND_PRIME_CONFLUENCE",
            "DIRECTIONAL_CONFLICT", "ELLIOTT_PRIME_TIME_FIT_ONLY",
            "ELLIOTT_COUNT_WITHOUT_STRONG_PRIME_FIT", "NO_VALID_PRIMARY_ELLIOTT_COUNT",
        })

    def test_fibonacci_guidelines_do_not_override_hard_rules(self):
        # A structurally legal impulse remains valid even when its price ratios are not textbook Fibonacci values.
        seq = [
            pivot("LOW", 100, 0), pivot("HIGH", 109, 11),
            pivot("LOW", 105, 18), pivot("HIGH", 119, 31),
            pivot("LOW", 116, 44), pivot("HIGH", 123, 57),
        ]
        result = evaluate_impulse_pivots(seq, "UP")
        self.assertTrue(result["valid"])
        self.assertIn("guidelines", result)

    def test_abc_candidate_is_not_mislabeled_as_a_completed_impulse(self):
        from elliott_wave import _prime_elliott_relation
        frames = {"15m": {"count": {
            "pattern": "ABC_ZIGZAG_LIKE", "direction": "UP", "valid": True,
            "provisional": True, "trend_direction_signal": "UP",
            "direction_role": "POSSIBLE_PARENT_TREND_RESUMPTION", "prime_time_overlay": {"fit_pct": 75.0},
        }}}
        relation = _prime_elliott_relation(frames, {"signal": "LONG"})
        self.assertFalse(relation["elliott_valid_impulse"])
        self.assertTrue(relation["elliott_directional_candidate"])
        self.assertEqual(relation["state"], "ELLIOTT_PROVISIONAL_AND_PRIME_CONFLUENCE")

    def test_no_clear_or_range_count_does_not_claim_directional_confluence(self):
        from elliott_wave import _prime_elliott_relation
        frames = {"15m": {"count": {
            "pattern": "POSSIBLE_CONTRACTING_TRIANGLE", "direction": "RANGE", "trend_direction_signal": None,
            "valid": False, "provisional": True, "prime_time_overlay": {"fit_pct": 90.0},
        }}}
        relation = _prime_elliott_relation(frames, {"signal": "LONG"})
        self.assertFalse(relation["elliott_valid_impulse"])
        self.assertEqual(relation["state"], "NO_VALID_PRIMARY_ELLIOTT_COUNT")

    def test_latest_alternative_count_is_selected_not_first_candidate(self):
        from unittest.mock import patch
        from elliott_wave import _select_count
        pivots = [pivot("LOW", 100, 0), pivot("HIGH", 110, 10), pivot("LOW", 104, 20), pivot("HIGH", 120, 30)]
        stale_abc = {"pattern": "ABC_ZIGZAG_LIKE", "direction": "UP", "score": 70,
                     "pivots": [pivot("LOW", 100, 0), pivot("HIGH", 110, 1), pivot("LOW", 104, 2), pivot("HIGH", 120, 3)]}
        recent_triangle = {"pattern": "POSSIBLE_CONTRACTING_TRIANGLE", "direction": "RANGE", "score": 62,
                           "pivots": [pivot("HIGH", 130, 2), pivot("LOW", 105, 3), pivot("HIGH", 120, 4), pivot("LOW", 108, 5), pivot("HIGH", 115, 8)]}
        with patch("elliott_wave.evaluate_impulse_pivots", return_value=None), \
             patch("elliott_wave._abc_candidate", return_value=stale_abc), \
             patch("elliott_wave._triangle_candidate", return_value=recent_triangle), \
             patch("elliott_wave._partial_impulse", return_value=None), \
             patch("elliott_wave._diagonal_candidate", return_value=None):
            result = _select_count(pivots)
        self.assertEqual(result["pattern"], "POSSIBLE_CONTRACTING_TRIANGLE")


    def test_unfinished_abc_correction_does_not_claim_parent_trend_confluence(self):
        from elliott_wave import _prime_elliott_relation
        frames = {"15m": {"count": {
            "pattern": "ABC_ZIGZAG_LIKE", "direction": "UP", "correction_direction": "DOWN",
            "valid": False, "provisional": True, "trend_direction_signal": None,
            "direction_role": "CORRECTION_NOT_TERMINAL", "prime_time_overlay": {"fit_pct": 75.0},
        }}}
        relation = _prime_elliott_relation(frames, {"signal": "LONG"})
        self.assertFalse(relation["elliott_directional_candidate"])
        self.assertEqual(relation["state"], "NO_VALID_PRIMARY_ELLIOTT_COUNT")

    def test_possible_diagonal_is_not_treated_as_simple_continuation_signal(self):
        from elliott_wave import _prime_elliott_relation
        frames = {"15m": {"count": {
            "pattern": "POSSIBLE_DIAGONAL", "direction": "UP", "valid": False,
            "provisional": True, "trend_direction_signal": None,
            "direction_role": "POSSIBLE_TERMINAL_DIAGONAL_REVERSAL_RISK",
            "prime_time_overlay": {"fit_pct": 90.0},
        }}}
        relation = _prime_elliott_relation(frames, {"signal": "LONG"})
        self.assertFalse(relation["elliott_directional_candidate"])
        self.assertEqual(relation["state"], "NO_VALID_PRIMARY_ELLIOTT_COUNT")



if __name__ == "__main__":
    unittest.main()
