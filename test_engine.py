import math
import random
import unittest

from engine import analyze, backtest, detect_pivots, nearest_prime


def synthetic_candles(count=1500):
    random.seed(21)
    candles = []
    price = 5.0
    for i in range(count):
        target = 5 + 0.35 * math.sin(i / 17) + 0.12 * math.sin(i / 53) + i * 0.00003
        price += (target - price) * 0.22 + random.uniform(-0.018, 0.018)
        open_price = price + random.uniform(-0.01, 0.01)
        close = price + random.uniform(-0.015, 0.015)
        high = max(open_price, close) + random.uniform(0.002, 0.02)
        low = min(open_price, close) - random.uniform(0.002, 0.02)
        candles.append({
            "open_time": i * 900_000,
            "close_time": (i + 1) * 900_000 - 1,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1.0,
        })
    return candles


class PrimeWaveEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candles = synthetic_candles()

    def test_nearest_prime(self):
        self.assertEqual(nearest_prime(17), (17, 0))
        self.assertEqual(nearest_prime(18), (17, 1))

    def test_pivots_are_confirmed_and_have_types(self):
        pivots = detect_pivots(self.candles)
        self.assertGreater(len(pivots), 10)
        self.assertTrue(all(p["type"] in ("HIGH", "LOW") for p in pivots))

    def test_scores_stay_between_zero_and_one_hundred(self):
        result = analyze(self.candles, self.candles[-1]["close"])
        self.assertIn(result["signal"], ("LONG", "SHORT", "NEUTRAL"))
        self.assertGreaterEqual(result["long_score"], 0)
        self.assertLessEqual(result["long_score"], 100)
        self.assertGreaterEqual(result["short_score"], 0)
        self.assertLessEqual(result["short_score"], 100)


    def test_adaptive_dimension_weights_are_reported_and_bounded(self):
        result = analyze(
            self.candles,
            self.candles[-1]['close'],
            adaptive_weights={'temporal': 99.0, 'amplitude': 0.01, 'behavior': -5.0},
        )
        weights = result['dimension_adjustments']['weights_used']
        self.assertEqual(weights['temporal'], 1.15)
        self.assertEqual(weights['amplitude'], 0.85)
        self.assertEqual(weights['behavior'], 0.85)
        self.assertLessEqual(result['dimension_adjustments']['LONG'], 6.0)
        self.assertLessEqual(result['dimension_adjustments']['SHORT'], 6.0)

    def test_backtest_returns_forward_evaluations(self):
        results = backtest(self.candles)
        self.assertGreater(len(results), 0)
        for item in results:
            self.assertIn(item["side"], ("LONG", "SHORT"))
            self.assertIn(item["correct"], (0, 1))
            self.assertTrue(math.isfinite(item["return_pct"]))


if __name__ == "__main__":
    unittest.main()
