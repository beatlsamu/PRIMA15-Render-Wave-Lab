import math
import time
import unittest
from unittest.mock import patch

from fast_entry import (
    FastEntryEngine,
    _build_reference_samples,
    _features,
    _label_future,
    _neighbor_stats,
    _runway,
)


def candles_1m(count=180):
    now = int(time.time() * 1000)
    end_open = (now // 60_000) * 60_000 - 60_000
    rows = []
    price = 5.0
    for i in range(count):
        # Gentle directional motion plus a short oscillation to create varied analogues.
        price *= 1.0 + 0.000045 + 0.00020 * math.sin(i / 5.0)
        op = price * (1.0 - 0.00012 * math.sin(i / 3.0))
        close = price
        rows.append({
            "open_time": end_open - (count - i - 1) * 60_000,
            "close_time": end_open - (count - i - 1) * 60_000 + 59_999,
            "open": op,
            "high": max(op, close) * 1.0005,
            "low": min(op, close) * 0.9995,
            "close": close,
            "volume": 100 + 20 * (1 + math.sin(i / 4.0)),
            "quote_volume": close * 100,
            "trades": 20,
        })
    return rows


class FastEntryTests(unittest.TestCase):
    def test_features_include_crosses_and_short_horizon_momentum(self):
        rows = candles_1m(100)
        result = _features(rows, rows[-1]["close"])
        self.assertIsNotNone(result)
        self.assertIn("ema_gap_pct", result)
        self.assertIn("roc_3m_pct", result)
        self.assertIn("vwap_gap_pct", result)
        self.assertIn("volume_ratio", result)
        self.assertIn("acceleration_3m_pct", result)

    def test_tp_first_label_is_forward_and_conservative(self):
        entry = 100.0
        tp = [{"high": 100.25, "low": 99.95}]
        adverse = [{"high": 100.05, "low": 99.87}]
        both = [{"high": 100.25, "low": 99.87}]
        self.assertEqual(_label_future(entry, tp, "LONG", 0.20, 0.12), (True, 1, "TP_FIRST"))
        self.assertEqual(_label_future(entry, adverse, "LONG", 0.20, 0.12), (False, None, "ADVERSE_FIRST"))
        self.assertEqual(_label_future(entry, both, "LONG", 0.20, 0.12)[2], "BOTH_SAME_BAR_CONSERVATIVE_LOSS")

    def test_nearest_analogues_are_spaced_by_outcome_horizon(self):
        reference = []
        for i in range(60):
            reference.append({
                "time_ms": i * 60_000,
                "long_vector": [0.0] * 12,
                "long_hit": i % 2 == 0,
                "long_bars": 5 if i % 2 == 0 else None,
                "long_label": "TP_FIRST" if i % 2 == 0 else "ADVERSE_FIRST",
            })
        stats = _neighbor_stats([0.0] * 12, reference, "LONG", k=50, min_separation_minutes=20)
        self.assertLessEqual(stats["sample_count"], 3)
        self.assertGreater(stats["sample_count"], 0)

    def test_reference_features_use_only_local_lookback_and_future_labels(self):
        rows = candles_1m(180)
        samples = _build_reference_samples(rows, 0.20, 0.12, 20, max_samples=120)
        self.assertGreater(len(samples), 0)
        self.assertTrue(all("long_label" in item and "short_label" in item for item in samples))
        self.assertLess(samples[-1]["time_ms"], rows[-1]["open_time"])

    def test_engine_requires_sufficient_1m_history_before_candidate(self):
        now = int(time.time() * 1000)
        rows = candles_1m(60)
        result = FastEntryEngine().analyze(rows, rows[-1]["close"], None, None, now_ms=now)
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertFalse(result["watch_candidate"])

    def test_btc_and_eth_opposing_render_block_long_entry(self):
        rows = candles_1m(180)
        now = int(time.time() * 1000)
        price = rows[-1]["close"]
        book = {
            "timestamp_ms": now,
            "bids": [[price * 0.9999, 1000], [price * 0.9998, 900]],
            "asks": [[price * 1.0001, 100], [price * 1.0002, 100]],
        }
        trades = {
            "timestamp_ms": now,
            "trades": [{"price": price, "qty": 100, "side": "BUY", "time_ms": now}],
        }
        cross = {
            "assets": {
                "BTCUSDT": {"available": True, "roc_5m_pct": -0.12, "roc_3m_pct": -0.10, "roc_10m_pct": -0.15, "age_seconds": 10},
                "ETHUSDT": {"available": True, "roc_5m_pct": -0.12, "roc_3m_pct": -0.09, "roc_10m_pct": -0.14, "age_seconds": 12},
            }
        }
        result = FastEntryEngine().analyze(
            rows, price, book, trades, now_ms=now,
            min_samples=2, min_hit_rate_pct=0, min_wilson_pct=0,
            min_live_confirmation_pct=0, max_spread_bps=20,
            cross_market_context=cross,
        )
        self.assertEqual(result["status"], "READY")
        blockers = result["opportunities"]["LONG"]["blockers"]
        self.assertTrue(any("BTC y ETH" in item for item in blockers))
        self.assertEqual(result["cross_market"]["assets"]["BTCUSDT"]["roc_5m_pct"], -0.12)
        self.assertLess(result["cross_market_diagnostics"]["render_relative_strength_5m_pct"], 0.08)

    def test_taker_flow_ignores_prints_older_than_45_seconds(self):
        from fast_entry import _trade_metrics
        now = int(time.time() * 1000)
        result = _trade_metrics({
            "timestamp_ms": now,
            "trades": [
                {"price": 100, "qty": 10, "side": "BUY", "time_ms": now - 120_000},
                {"price": 100, "qty": 2, "side": "SELL", "time_ms": now - 2_000},
            ],
        }, now)
        self.assertTrue(result["available"])
        self.assertEqual(result["trade_count"], 1)
        self.assertEqual(result["stale_or_invalid_trades_ignored"], 1)
        self.assertEqual(result["taker_imbalance"], -1.0)

    def test_render_relative_strength_can_override_btc_eth_headwind(self):
        rows = candles_1m(180)
        now = int(time.time() * 1000)
        price = rows[-1]["close"]
        book = {"timestamp_ms": now, "bids": [[price * 0.9999, 1000]], "asks": [[price * 1.0001, 100]]}
        trades = {"timestamp_ms": now, "trades": [{"price": price, "qty": 100, "side": "BUY", "time_ms": now}]}
        cross = {"assets": {
            "BTCUSDT": {"available": True, "roc_5m_pct": -0.25, "roc_3m_pct": -0.18, "roc_10m_pct": -0.30, "age_seconds": 10},
            "ETHUSDT": {"available": True, "roc_5m_pct": -0.22, "roc_3m_pct": -0.15, "roc_10m_pct": -0.27, "age_seconds": 12},
        }}
        result = FastEntryEngine().analyze(
            rows, price, book, trades, now_ms=now, min_samples=2,
            min_hit_rate_pct=0, min_wilson_pct=0, min_live_confirmation_pct=0,
            max_spread_bps=20, cross_market_context=cross,
        )
        blockers = result["opportunities"]["LONG"]["blockers"]
        self.assertGreaterEqual(result["cross_market_diagnostics"]["render_relative_strength_5m_pct"], 0.08)
        self.assertFalse(any("BTC y ETH contradicen" in item for item in blockers))

    def test_runway_includes_15m_resistance_and_support(self):
        rows = candles_1m(100)
        long_result = _runway(
            rows, 100.0, "LONG", 0.20,
            {"support": 99.0, "resistance": 100.10, "source_interval": "15m"},
        )
        short_result = _runway(
            rows, 100.0, "SHORT", 0.20,
            {"support": 99.90, "resistance": 101.0, "source_interval": "15m"},
        )
        self.assertEqual(long_result["obstacle_source"], "15m_resistance_31")
        self.assertEqual(short_result["obstacle_source"], "15m_support_31")
        self.assertTrue(long_result["blocked"])
        self.assertTrue(short_result["blocked"])

    def test_ambiguous_long_short_edge_is_blocked_not_forced(self):
        rows = candles_1m(180)
        now = int(time.time() * 1000)
        price = rows[-1]["close"] * 0.99
        book = {
            "timestamp_ms": now,
            "bids": [[price * 0.9999, 1000], [price * 0.9998, 900]],
            "asks": [[price * 1.0001, 100], [price * 1.0002, 100]],
        }
        trades = {
            "timestamp_ms": now,
            "trades": [{"price": price, "qty": 100, "side": "BUY", "time_ms": now}],
        }
        historical = {
            "sample_count": 40, "tp_first_count": 28, "tp_first_rate_pct": 70.0,
            "wilson_lower_80_pct": 55.0, "median_minutes_to_tp": 5.0,
            "mean_similarity_distance": 0.1,
            "nearest_labels": {"TP_FIRST": 28, "ADVERSE_FIRST": 12, "BOTH_SAME_BAR_CONSERVATIVE_LOSS": 0, "NO_TOUCH_WITHIN_HORIZON": 0},
        }
        with patch("fast_entry._neighbor_stats", return_value=historical), patch(
            "fast_entry._pressure", return_value={"long_score": 70.0, "short_score": 69.0, "signed_pressure": 0.01, "volume_quality_pct": 60.0, "components": {}, "component_count": 0}
        ):
            result = FastEntryEngine().analyze(
                rows, price, book, trades, now_ms=now,
                min_samples=10, min_hit_rate_pct=60, min_wilson_pct=50,
                min_live_confirmation_pct=55, max_spread_bps=20,
                min_directional_edge_gap_pct=4.5,
            )
        self.assertFalse(result["opportunities"]["LONG"]["eligible"])
        self.assertFalse(result["opportunities"]["SHORT"]["eligible"])
        self.assertFalse(result["opportunities"]["LONG"]["watch_candidate"])
        self.assertFalse(result["opportunities"]["SHORT"]["watch_candidate"])
        self.assertIsNone(result["candidate_side"])
        self.assertTrue(any("demasiado estrecha" in reason for reason in result["opportunities"]["LONG"]["blockers"]))


if __name__ == "__main__":
    unittest.main()
