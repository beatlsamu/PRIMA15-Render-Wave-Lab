"""Decision-cycle helpers for the PRIMA-15 paper portfolio."""
from __future__ import annotations

from typing import Any


TAKE_PROFIT_CLOSED = "TAKE_PROFIT_CLOSED"


def should_run_committee(
    *,
    api_key_ready: bool,
    price_fresh: bool,
    connection: str,
    now_seconds: float,
    last_run_seconds: float,
    interval_seconds: int,
    portfolio_event: dict[str, Any] | None = None,
) -> bool:
    """Run on cadence, or immediately after TP closes a position.

    A take-profit close is a state transition: the next committee must receive a
    newly-built snapshot showing a FLAT wallet and the latest market data, rather
    than waiting for the ordinary polling interval.
    """
    if not api_key_ready or not price_fresh or connection != "connected":
        return False
    event_name = str((portfolio_event or {}).get("event", ""))
    just_closed = event_name == TAKE_PROFIT_CLOSED
    interval_due = (float(now_seconds) - float(last_run_seconds)) >= max(1, int(interval_seconds))
    return just_closed or interval_due


def has_actionable_consensus(ai_result: dict[str, Any] | None) -> bool:
    """Only a fresh successful two-vote directional committee can open/reverse."""
    if not isinstance(ai_result, dict) or ai_result.get("status") != "ok":
        return False
    if str(ai_result.get("decision", "")).upper() not in {"LONG", "SHORT"}:
        return False
    try:
        required = max(2, int(ai_result.get("required_votes", 2)))
        valid = int(ai_result.get("valid_models", len(ai_result.get("votes", []) or [])))
    except (TypeError, ValueError):
        return False
    return valid >= required
