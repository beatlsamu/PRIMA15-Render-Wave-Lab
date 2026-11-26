import sqlite3
import unittest

from portfolio import (
    apply_llm_decision,
    init_portfolio_db,
    manage_take_profit,
    portfolio_snapshot,
    price_return_pct,
)


class TestPercentagePortfolio(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        init_portfolio_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_open_charges_fee_once_and_uses_percentage_wallet(self):
        opened = apply_llm_decision(
            self.conn, 'LONG', 100.0, confidence=55, fee_rate_pct=0.035,
            exposure_pct=100, now_ms=1_000,
        )
        self.assertEqual(opened['event'], 'OPENED')
        state = portfolio_snapshot(self.conn, 100.0)
        self.assertAlmostEqual(state['wallet_balance_pct'], 99.965, places=8)
        self.assertAlmostEqual(state['total_fees_pct'], 0.035, places=8)
        self.assertEqual(state['open_position']['side'], 'LONG')
        self.assertNotIn('total_fees_points', state)
        self.assertNotIn('unrealized_wallet_points', state)
        self.assertNotIn('realized_pnl_points', state)
        self.assertNotIn('total_fees_points', state)
        self.assertIn('total_fees_pct', state)
        self.assertIn('unrealized_wallet_pnl_pct', state['open_position'])

    def test_same_direction_holds_without_another_fee(self):
        apply_llm_decision(self.conn, 'LONG', 100.0, fee_rate_pct=0.035, now_ms=1_000)
        held = apply_llm_decision(self.conn, 'LONG', 100.5, fee_rate_pct=0.035, now_ms=2_000)
        self.assertEqual(held['event'], 'HELD')
        state = portfolio_snapshot(self.conn, 100.5)
        self.assertAlmostEqual(state['total_fees_pct'], 0.035, places=8)
        self.assertAlmostEqual(state['open_position']['price_return_pct'], 0.5, places=6)

    def test_close_has_no_fee_and_realizes_compounded_percentage_return(self):
        apply_llm_decision(self.conn, 'LONG', 100.0, fee_rate_pct=0.035, now_ms=1_000)
        reversed_result = apply_llm_decision(
            self.conn, 'SHORT', 101.0, fee_rate_pct=0.035, now_ms=2_000,
        )
        self.assertEqual(reversed_result['event'], 'REVERSED')
        closed = reversed_result['closed_trade']
        self.assertAlmostEqual(closed['price_return_pct'], 1.0, places=6)
        self.assertAlmostEqual(closed['wallet_pnl_pct'], 0.99965, places=6)
        state = portfolio_snapshot(self.conn, 101.0)
        # One close does not add a fee; the only charge is for the new SHORT opening.
        self.assertAlmostEqual(state['total_fees_pct'], 0.070338, places=5)
        self.assertEqual(state['open_position']['side'], 'SHORT')
        self.assertEqual(state['total_closed'], 1)

    def test_short_close_uses_symmetric_percentage_return_without_close_fee(self):
        apply_llm_decision(self.conn, 'SHORT', 100.0, fee_rate_pct=0.035, now_ms=1_000)
        reversed_result = apply_llm_decision(
            self.conn, 'LONG', 99.0, fee_rate_pct=0.035, now_ms=2_000,
        )
        self.assertEqual(reversed_result['event'], 'REVERSED')
        self.assertAlmostEqual(reversed_result['closed_trade']['price_return_pct'], 1.0, places=6)
        self.assertAlmostEqual(reversed_result['closed_trade']['wallet_pnl_pct'], 0.99965, places=6)
        self.assertAlmostEqual(reversed_result['closed_trade']['opening_cost_pct'], 0.035, places=6)
        state = portfolio_snapshot(self.conn, 99.0)
        self.assertAlmostEqual(state['total_fees_pct'], 0.070338, places=6)

    def test_missing_llm_quorum_keeps_current_position(self):
        apply_llm_decision(self.conn, 'SHORT', 100.0, now_ms=1_000)
        held = apply_llm_decision(self.conn, 'WAIT', 99.0, now_ms=2_000)
        self.assertEqual(held['event'], 'HOLD_NO_VALID_VOTE')
        state = portfolio_snapshot(self.conn, 99.0)
        self.assertEqual(state['open_position']['side'], 'SHORT')
        self.assertAlmostEqual(state['total_fees_pct'], 0.035, places=8)


    def test_take_profit_closes_at_configured_target_without_close_fee(self):
        apply_llm_decision(self.conn, 'LONG', 100.0, fee_rate_pct=0.035, now_ms=1_000)
        # Below target: position remains open and no additional fee is charged.
        not_closed = manage_take_profit(self.conn, 100.19, target_profit_pct=0.20, now_ms=2_000)
        self.assertIsNone(not_closed)
        # Target is measured on favorable price movement, not wallet percentage.
        closed = manage_take_profit(self.conn, 100.20, target_profit_pct=0.20, now_ms=3_000)
        self.assertEqual(closed['event'], 'TAKE_PROFIT_CLOSED')
        self.assertAlmostEqual(closed['price_return_pct'], 0.20, places=6)
        state = portfolio_snapshot(self.conn, 100.20)
        self.assertAlmostEqual(state['total_fees_pct'], 0.035, places=8)
        self.assertIsNone(state['open_position'])
        self.assertEqual(state['total_closed'], 1)

    def test_portfolio_snapshot_reports_configured_exposure(self):
        apply_llm_decision(
            self.conn, 'LONG', 100.0, fee_rate_pct=0.035,
            exposure_pct=45.0, now_ms=1_000,
        )
        state = portfolio_snapshot(self.conn, 100.0, position_size_pct=45.0)
        self.assertEqual(state['position_size_pct'], 45.0)

    def test_long_and_short_returns_are_percentage_based(self):
        self.assertAlmostEqual(price_return_pct('LONG', 100, 101), 1.0, places=6)
        self.assertAlmostEqual(price_return_pct('SHORT', 100, 99), 1.0, places=6)


if __name__ == '__main__':
    unittest.main()
