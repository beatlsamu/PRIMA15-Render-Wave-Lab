"""Percentage-only paper wallet and single-position manager for PRIMA-15.

The wallet is an index where 100.0 means the initial paper balance. One open
position uses a configurable fraction of the wallet. Returns are based only on
price percentage movement; the opening fee is charged once when a position is
opened. Closing has no fee.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

INITIAL_WALLET_PCT = 100.0
OPEN_FEE_RATE_PCT = 0.035
POSITION_SIZE_PCT = 100.0
TAKE_PROFIT_PCT = 0.20


def init_portfolio_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sim_wallet (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            initial_balance_pct REAL NOT NULL,
            balance_pct REAL NOT NULL,
            realized_pnl_points REAL NOT NULL DEFAULT 0,
            total_fees_points REAL NOT NULL DEFAULT 0,
            total_opened INTEGER NOT NULL DEFAULT 0,
            total_closed INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sim_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            side TEXT NOT NULL CHECK (side IN ('LONG','SHORT')),
            status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED')),
            opened_at INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            last_price REAL NOT NULL,
            wallet_before_fee_pct REAL NOT NULL,
            wallet_basis_pct REAL NOT NULL,
            exposure_pct REAL NOT NULL,
            opening_fee_points REAL NOT NULL,
            peak_return_pct REAL NOT NULL DEFAULT 0,
            trough_return_pct REAL NOT NULL DEFAULT 0,
            open_confidence REAL NOT NULL DEFAULT 0,
            open_reason TEXT NOT NULL DEFAULT ''
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sim_one_open_position
            ON sim_positions(status) WHERE status = 'OPEN';
        CREATE TABLE IF NOT EXISTS sim_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL UNIQUE,
            side TEXT NOT NULL,
            opened_at INTEGER NOT NULL,
            closed_at INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            price_return_pct REAL NOT NULL,
            wallet_pnl_points REAL NOT NULL,
            opening_fee_points REAL NOT NULL,
            net_wallet_change_points REAL NOT NULL,
            net_wallet_return_pct REAL NOT NULL,
            balance_after_close_pct REAL NOT NULL,
            close_reason TEXT NOT NULL,
            FOREIGN KEY(position_id) REFERENCES sim_positions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_sim_trades_closed ON sim_trades(closed_at DESC);
        """
    )
    conn.execute(
        """INSERT INTO sim_wallet
           (id, initial_balance_pct, balance_pct, realized_pnl_points, total_fees_points,
            total_opened, total_closed, updated_at)
           VALUES (1, ?, ?, 0, 0, 0, 0, ?)
           ON CONFLICT(id) DO NOTHING""",
        (INITIAL_WALLET_PCT, INITIAL_WALLET_PCT, int(time.time() * 1000)),
    )


def price_return_pct(side: str, entry_price: float, current_price: float) -> float:
    if entry_price <= 0 or current_price <= 0:
        return 0.0
    if side == "LONG":
        return (current_price / entry_price - 1.0) * 100.0
    # Symmetric, unleveraged percentage return on the entry notional.
    return (entry_price - current_price) / entry_price * 100.0


def _wallet(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sim_wallet WHERE id=1").fetchone()
    if row is None:
        init_portfolio_db(conn)
        row = conn.execute("SELECT * FROM sim_wallet WHERE id=1").fetchone()
    return row


def _open_position(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sim_positions WHERE status='OPEN' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _close_position(
    conn: sqlite3.Connection,
    position: sqlite3.Row,
    exit_price: float,
    close_reason: str,
    now_ms: int,
) -> dict[str, Any]:
    side = str(position["side"])
    entry = float(position["entry_price"])
    gross_return = price_return_pct(side, entry, exit_price)
    wallet_basis = float(position["wallet_basis_pct"])
    exposure = float(position["exposure_pct"])
    wallet_pnl = wallet_basis * exposure / 100.0 * gross_return / 100.0
    opening_fee_points = float(position["opening_fee_points"])
    wallet_before_fee = float(position["wallet_before_fee_pct"])

    wallet = _wallet(conn)
    initial_balance = float(wallet["initial_balance_pct"]) or INITIAL_WALLET_PCT
    new_balance = float(wallet["balance_pct"]) + wallet_pnl
    net_change = new_balance - wallet_before_fee
    net_return = (net_change / wallet_before_fee * 100.0) if wallet_before_fee else 0.0
    conn.execute(
        "UPDATE sim_positions SET status='CLOSED', last_price=? WHERE id=?",
        (exit_price, int(position["id"])),
    )
    conn.execute(
        """INSERT INTO sim_trades
           (position_id, side, opened_at, closed_at, entry_price, exit_price,
            price_return_pct, wallet_pnl_points, opening_fee_points,
            net_wallet_change_points, net_wallet_return_pct, balance_after_close_pct,
            close_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            int(position["id"]), side, int(position["opened_at"]), now_ms,
            entry, exit_price, gross_return, wallet_pnl, opening_fee_points,
            net_change, net_return, new_balance, close_reason[:300],
        ),
    )
    conn.execute(
        """UPDATE sim_wallet SET balance_pct=?, realized_pnl_points=realized_pnl_points+?,
           total_closed=total_closed+1, updated_at=? WHERE id=1""",
        (new_balance, wallet_pnl, now_ms),
    )
    return {
        "position_id": int(position["id"]),
        "side": side,
        "price_return_pct": gross_return,
        "wallet_pnl_pct": (wallet_pnl / initial_balance) * 100.0,
        "opening_cost_pct": (opening_fee_points / initial_balance) * 100.0,
        "net_wallet_return_pct": net_return,
        "balance_after_close_pct": new_balance,
        "close_reason": close_reason,
    }


def _open_new_position(
    conn: sqlite3.Connection,
    side: str,
    price: float,
    fee_rate_pct: float,
    exposure_pct: float,
    confidence: float,
    reason: str,
    now_ms: int,
) -> dict[str, Any]:
    wallet = _wallet(conn)
    before_fee = float(wallet["balance_pct"])
    if before_fee <= 0:
        return {"event": "OPEN_BLOCKED", "reason": "El saldo porcentual llegó a cero o menos."}
    fee_points = before_fee * fee_rate_pct / 100.0
    after_fee = before_fee - fee_points
    if after_fee <= 0:
        return {"event": "OPEN_BLOCKED", "reason": "Saldo insuficiente para cubrir el coste de apertura."}
    cursor = conn.execute(
        """INSERT INTO sim_positions
           (side, status, opened_at, entry_price, last_price, wallet_before_fee_pct,
            wallet_basis_pct, exposure_pct, opening_fee_points, peak_return_pct,
            trough_return_pct, open_confidence, open_reason)
           VALUES (?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
        (side, now_ms, price, price, before_fee, after_fee, exposure_pct, fee_points, confidence, reason[:500]),
    )
    conn.execute(
        """UPDATE sim_wallet SET balance_pct=?, total_fees_points=total_fees_points+?,
           total_opened=total_opened+1, updated_at=? WHERE id=1""",
        (after_fee, fee_points, now_ms),
    )
    initial = float(wallet["initial_balance_pct"]) or INITIAL_WALLET_PCT
    return {
        "event": "OPENED", "position_id": int(cursor.lastrowid), "side": side,
        "opening_fee_rate_pct": fee_rate_pct,
        "opening_cost_pct": (fee_points / initial) * 100.0,
        "wallet_balance_pct": after_fee,
    }


def apply_llm_decision(
    conn: sqlite3.Connection,
    decision: str,
    current_price: float,
    confidence: float = 0.0,
    reason: str = "",
    fee_rate_pct: float = OPEN_FEE_RATE_PCT,
    exposure_pct: float = POSITION_SIZE_PCT,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Open, hold, or reverse one simulated position based on the LLM committee."""
    now_ms = int(now_ms or time.time() * 1000)
    side = str(decision or "WAIT").upper()
    if current_price <= 0:
        return {"event": "NO_PRICE", "reason": "Precio de mercado no válido."}
    if side not in {"LONG", "SHORT"}:
        return {"event": "HOLD_NO_VALID_VOTE", "reason": "No hubo quórum LLM válido; se conserva la posición actual."}

    position = _open_position(conn)
    closed = None
    if position is not None and str(position["side"]) == side:
        ret = price_return_pct(side, float(position["entry_price"]), current_price)
        conn.execute(
            """UPDATE sim_positions SET last_price=?, peak_return_pct=?, trough_return_pct=?
               WHERE id=?""",
            (
                current_price,
                max(float(position["peak_return_pct"]), ret),
                min(float(position["trough_return_pct"]), ret),
                int(position["id"]),
            ),
        )
        conn.execute("UPDATE sim_wallet SET updated_at=? WHERE id=1", (now_ms,))
        return {"event": "HELD", "side": side, "position_id": int(position["id"]), "price_return_pct": ret}

    if position is not None:
        closed = _close_position(
            conn, position, current_price,
            f"El comité cambió la dirección a {side}: {reason}", now_ms,
        )

    opened = _open_new_position(
        conn, side, current_price, fee_rate_pct, exposure_pct,
        confidence, reason, now_ms,
    )
    if closed and opened.get("event") == "OPENED":
        opened["event"] = "REVERSED"
        opened["closed_trade"] = closed
    elif closed:
        opened["closed_trade"] = closed
    return opened


def manage_take_profit(
    conn: sqlite3.Connection,
    current_price: float,
    target_profit_pct: float = TAKE_PROFIT_PCT,
    now_ms: int | None = None,
) -> dict[str, Any] | None:
    """Close an open paper position once its favorable price return hits the target.

    This function is intended to be called on every market-price poll, independently
    of the slower LLM schedule. Closing has no fee; the opening fee was already paid.
    """
    now_ms = int(now_ms or time.time() * 1000)
    if current_price <= 0 or target_profit_pct <= 0:
        return None
    position = _open_position(conn)
    if position is None:
        return None
    side = str(position["side"])
    entry = float(position["entry_price"])
    live_return = price_return_pct(side, entry, current_price)
    conn.execute(
        """UPDATE sim_positions SET last_price=?, peak_return_pct=?, trough_return_pct=?
           WHERE id=?""",
        (
            current_price,
            max(float(position["peak_return_pct"]), live_return),
            min(float(position["trough_return_pct"]), live_return),
            int(position["id"]),
        ),
    )
    if live_return + 1e-9 < target_profit_pct:
        return None
    closed = _close_position(
        conn,
        position,
        current_price,
        f"TAKE PROFIT alcanzado: retorno favorable {live_return:.4f}% (objetivo +{target_profit_pct:.4f}%)",
        now_ms,
    )
    closed.update({
        "event": "TAKE_PROFIT_CLOSED",
        "target_profit_pct": target_profit_pct,
        "price_return_pct": live_return,
        "message": f"{side} cerrada automáticamente al alcanzar +{live_return:.3f}% de retorno favorable.",
    })
    return closed


def portfolio_snapshot(
    conn: sqlite3.Connection,
    current_price: float | None = None,
    history_limit: int = 10,
    take_profit_target_pct: float = TAKE_PROFIT_PCT,
    opening_fee_rate_pct: float = OPEN_FEE_RATE_PCT,
    position_size_pct: float = POSITION_SIZE_PCT,
) -> dict[str, Any]:
    wallet = _wallet(conn)
    position = _open_position(conn)
    initial = float(wallet["initial_balance_pct"])
    balance = float(wallet["balance_pct"])
    open_view: dict[str, Any] | None = None
    unrealized_price_return = 0.0
    unrealized_wallet_points = 0.0
    if position is not None:
        entry = float(position["entry_price"])
        mark = float(current_price or position["last_price"] or entry)
        unrealized_price_return = price_return_pct(str(position["side"]), entry, mark)
        unrealized_wallet_points = (
            float(position["wallet_basis_pct"]) * float(position["exposure_pct"]) / 100.0
            * unrealized_price_return / 100.0
        )
        if current_price and current_price > 0:
            conn.execute(
                """UPDATE sim_positions SET last_price=?, peak_return_pct=?, trough_return_pct=?
                   WHERE id=?""",
                (mark, max(float(position["peak_return_pct"]), unrealized_price_return),
                 min(float(position["trough_return_pct"]), unrealized_price_return), int(position["id"])),
            )
        open_view = {
            "id": int(position["id"]),
            "side": str(position["side"]),
            "opened_at": int(position["opened_at"]),
            "price_return_pct": round(unrealized_price_return, 5),
            "unrealized_wallet_pnl_pct": round((unrealized_wallet_points / initial) * 100.0, 6) if initial else 0.0,
            "unrealized_wallet_return_pct": round((unrealized_wallet_points / initial) * 100.0, 6) if initial else 0.0,
            "peak_return_pct": round(max(float(position["peak_return_pct"]), unrealized_price_return), 5),
            "trough_return_pct": round(min(float(position["trough_return_pct"]), unrealized_price_return), 5),
            "opening_fee_rate_pct": opening_fee_rate_pct,
            "take_profit_target_pct": take_profit_target_pct,
        }
    equity = balance + unrealized_wallet_points
    history_rows = conn.execute(
        """SELECT id, side, opened_at, closed_at, price_return_pct,
                  net_wallet_return_pct, opening_fee_points, close_reason
           FROM sim_trades ORDER BY closed_at DESC LIMIT ?""",
        (max(1, min(50, int(history_limit))),),
    ).fetchall()
    history = [
        {
            "id": int(row["id"]),
            "side": str(row["side"]),
            "opened_at": int(row["opened_at"]),
            "closed_at": int(row["closed_at"]),
            "price_return_pct": round(float(row["price_return_pct"]), 6),
            "net_wallet_return_pct": round(float(row["net_wallet_return_pct"]), 6),
            "opening_cost_pct": round((float(row["opening_fee_points"]) / initial) * 100.0, 6) if initial else 0.0,
            "close_reason": str(row["close_reason"]),
        }
        for row in history_rows
    ]
    total_closed = int(wallet["total_closed"])
    total_wins = conn.execute(
        "SELECT COUNT(*) AS n FROM sim_trades WHERE net_wallet_return_pct > 0"
    ).fetchone()["n"]
    return {
        "initial_balance_pct": round(initial, 6),
        "wallet_balance_pct": round(balance, 6),
        "equity_pct": round(equity, 6),
        "realized_return_pct": round((balance / initial - 1.0) * 100.0, 6) if initial else 0.0,
        "total_return_pct": round((equity / initial - 1.0) * 100.0, 6) if initial else 0.0,
        "unrealized_price_return_pct": round(unrealized_price_return, 6),
        "realized_pnl_pct": round((float(wallet["realized_pnl_points"]) / initial) * 100.0, 6) if initial else 0.0,
        "total_fees_pct": round((float(wallet["total_fees_points"]) / initial) * 100.0, 6) if initial else 0.0,
        "total_opened": int(wallet["total_opened"]),
        "total_closed": total_closed,
        "win_rate_pct": round((int(total_wins) / total_closed) * 100.0, 2) if total_closed else None,
        "opening_fee_rate_pct": opening_fee_rate_pct,
        "take_profit_target_pct": take_profit_target_pct,
        "position_size_pct": max(1.0, min(100.0, float(position_size_pct))),
        "open_position": open_view,
        "history": history,
    }
