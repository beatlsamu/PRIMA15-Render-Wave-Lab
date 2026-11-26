import unittest
import sqlite3

from portfolio import apply_llm_decision, init_portfolio_db, manage_take_profit, portfolio_snapshot
from cycle_policy import should_run_committee, has_actionable_consensus


class TestCommitteePositionCycle(unittest.TestCase):
    def test_continuous_committee_waits_until_five_minute_interval(self):
        self.assertFalse(should_run_committee(
            api_key_ready=True, price_fresh=True, connection="connected",
            now_seconds=399, last_run_seconds=100, interval_seconds=300,
        ))

    def test_continuous_committee_runs_every_five_minutes(self):
        self.assertTrue(should_run_committee(
            api_key_ready=True, price_fresh=True, connection="connected",
            now_seconds=400, last_run_seconds=100, interval_seconds=300,
        ))

    def test_take_profit_close_forces_immediate_fresh_committee(self):
        self.assertTrue(should_run_committee(
            api_key_ready=True, price_fresh=True, connection="connected",
            now_seconds=105, last_run_seconds=100, interval_seconds=3600,
            portfolio_event={"event": "TAKE_PROFIT_CLOSED"},
        ))

    def test_tp_does_not_bypass_missing_api_or_stale_price(self):
        event = {"event": "TAKE_PROFIT_CLOSED"}
        self.assertFalse(should_run_committee(
            api_key_ready=False, price_fresh=True, connection="connected",
            now_seconds=105, last_run_seconds=100, interval_seconds=3600,
            portfolio_event=event,
        ))
        self.assertFalse(should_run_committee(
            api_key_ready=True, price_fresh=False, connection="connected",
            now_seconds=105, last_run_seconds=100, interval_seconds=3600,
            portfolio_event=event,
        ))
        self.assertFalse(should_run_committee(
            api_key_ready=True, price_fresh=True, connection="offline",
            now_seconds=105, last_run_seconds=100, interval_seconds=3600,
            portfolio_event=event,
        ))

    def test_valid_fresh_directional_consensus_is_actionable(self):
        self.assertTrue(has_actionable_consensus({
            "status": "ok", "decision": "LONG", "valid_models": 2, "required_votes": 2,
        }))
        self.assertTrue(has_actionable_consensus({
            "status": "ok", "decision": "SHORT", "valid_models": 3, "required_votes": 2,
        }))


    def test_take_profit_then_fresh_consensus_opens_next_position_immediately(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_portfolio_db(conn)
        try:
            first = apply_llm_decision(conn, "LONG", 100.0, fee_rate_pct=0.035, now_ms=1_000)
            self.assertEqual(first["event"], "OPENED")
            closed = manage_take_profit(conn, 100.20, target_profit_pct=0.20, now_ms=2_000)
            self.assertEqual(closed["event"], "TAKE_PROFIT_CLOSED")
            self.assertIsNone(portfolio_snapshot(conn, 100.20)["open_position"])

            # The close forces a new committee run even though the ordinary hour has not passed.
            self.assertTrue(should_run_committee(
                api_key_ready=True, price_fresh=True, connection="connected",
                now_seconds=2.1, last_run_seconds=1.0, interval_seconds=3600,
                portfolio_event=closed,
            ))
            fresh_llm_result = {
                "status": "ok", "decision": "SHORT", "valid_models": 2,
                "required_votes": 2, "confidence": 54,
            }
            self.assertTrue(has_actionable_consensus(fresh_llm_result))
            reopened = apply_llm_decision(
                conn, fresh_llm_result["decision"], 100.20,
                confidence=fresh_llm_result["confidence"], fee_rate_pct=0.035,
                now_ms=2_100,
            )
            self.assertEqual(reopened["event"], "OPENED")
            state = portfolio_snapshot(conn, 100.20)
            self.assertEqual(state["open_position"]["side"], "SHORT")
            self.assertEqual(state["total_closed"], 1)
        finally:
            conn.close()

    def test_wait_error_or_missing_quorum_never_reopens(self):
        for result in (
            None,
            {"status": "error", "decision": "SHORT", "valid_models": 2, "required_votes": 2},
            {"status": "ok", "decision": "WAIT", "valid_models": 2, "required_votes": 2},
            {"status": "ok", "decision": "LONG", "valid_models": 1, "required_votes": 2},
        ):
            self.assertFalse(has_actionable_consensus(result))


if __name__ == "__main__":
    unittest.main()
