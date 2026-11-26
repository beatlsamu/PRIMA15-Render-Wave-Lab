from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request

from config import (
    OPENROUTER_INTERVAL_SECONDS,
    APP_VERSION,
    BINANCE_BASES,
    DB_PATH,
    DATA_DIR,
    HISTORY_CANDLES,
    INTERVAL,
    INTERVAL_MS,
    MARKET_PROVIDER,
    BYBIT_BASES,
    KLINE_REFRESH_SECONDS,
    POLL_SECONDS,
    SYMBOL,
    SIM_OPEN_FEE_PCT,
    SIM_POSITION_SIZE_PCT,
    SIM_TAKE_PROFIT_PCT,
    FAST_MARKET_REFRESH_SECONDS, FAST_MARKET_HISTORY_CANDLES, FAST_ENTRY_REFERENCE_CANDLES,
    FAST_SETUP_COOLDOWN_SECONDS, FAST_ENTRY_ADVERSE_PCT, FAST_ENTRY_HORIZON_MINUTES,
    FAST_ENTRY_MIN_SAMPLES, FAST_ENTRY_MIN_HIT_RATE_PCT, FAST_ENTRY_MIN_WILSON_PCT,
    FAST_ENTRY_MIN_LIVE_CONFIRMATION_PCT, FAST_ENTRY_MAX_SPREAD_BPS,
    FAST_ENTRY_MAX_MINUTES_TO_TARGET, FAST_ENTRY_MIN_DIRECTIONAL_EDGE_GAP_PCT,
    FAST_ENTRY_ENFORCE_GATE, OPENROUTER_MAX_COMMITTEES_PER_DAY,
)
from engine import BASE_THRESHOLD, analyze, backtest
from ai_analyzer import (
    api_key_present,
    build_snapshot,
    evaluate_snapshot,
    initial_ai_state,
)
from portfolio import init_portfolio_db, apply_llm_decision, portfolio_snapshot, manage_take_profit
from cycle_policy import should_run_committee, has_actionable_consensus
from fast_entry import FastEntryEngine
from elliott_wave import analyze_multi_degree_elliott
from adaptive_learning import (
    init_adaptive_db,
    record_ai_prediction,
    evaluate_pending_predictions,
    calculate_adaptive_profile,
    save_profile_if_changed,
    recent_profile_history,
    recent_prediction_memory,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("prima15")

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

_worker_lock = threading.Lock()
_worker_started = False
_active_provider: str | None = None
_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "ready": False,
    "connection": "starting",
    "symbol": SYMBOL,
    "interval": INTERVAL,
    "price": None,
    "error": "Iniciando el motor de mercado…",
    "ai": initial_ai_state(),
}


def db_connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=25)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=25000")
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS candles (
                open_time INTEGER PRIMARY KEY,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL DEFAULT 0,
                close_time INTEGER NOT NULL DEFAULT 0,
                quote_volume REAL NOT NULL DEFAULT 0,
                trades INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_candles_open_time ON candles(open_time DESC);

            CREATE TABLE IF NOT EXISTS fast_candles_1m (
                open_time INTEGER PRIMARY KEY,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL DEFAULT 0,
                quote_volume REAL NOT NULL DEFAULT 0,
                trades INTEGER NOT NULL DEFAULT 0,
                taker_buy_base REAL NOT NULL DEFAULT 0,
                taker_buy_quote REAL NOT NULL DEFAULT 0,
                close_time INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_fast_candles_time ON fast_candles_1m(open_time DESC);

            CREATE TABLE IF NOT EXISTS committee_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                trigger_reason TEXT NOT NULL DEFAULT 'SCHEDULED',
                setup_key TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_committee_runs_created ON committee_runs(created_at DESC);

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS backtest_samples (
                bar_time INTEGER NOT NULL,
                side TEXT NOT NULL,
                score REAL NOT NULL,
                entry REAL NOT NULL,
                return_pct REAL NOT NULL,
                mfe_pct REAL NOT NULL,
                mae_pct REAL NOT NULL,
                correct INTEGER NOT NULL,
                PRIMARY KEY (bar_time, side)
            );
            CREATE INDEX IF NOT EXISTS idx_backtest_time ON backtest_samples(bar_time DESC);

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bar_time INTEGER NOT NULL,
                side TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                entry REAL NOT NULL,
                score REAL NOT NULL,
                rsi REAL NOT NULL,
                prime_elapsed INTEGER,
                prime_nearest INTEGER,
                UNIQUE(bar_time, side)
            );

            CREATE TABLE IF NOT EXISTS signal_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                horizon INTEGER NOT NULL,
                return_pct REAL NOT NULL,
                mfe_pct REAL NOT NULL,
                mae_pct REAL NOT NULL,
                correct INTEGER NOT NULL,
                evaluated_at INTEGER NOT NULL,
                UNIQUE(signal_id, horizon),
                FOREIGN KEY(signal_id) REFERENCES signals(id)
            );
            CREATE INDEX IF NOT EXISTS idx_signal_outcomes_horizon ON signal_outcomes(horizon, evaluated_at DESC);

            CREATE TABLE IF NOT EXISTS ai_assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bar_time INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                requested_model TEXT NOT NULL,
                served_model TEXT NOT NULL,
                decision TEXT NOT NULL,
                confidence REAL NOT NULL,
                summary TEXT NOT NULL,
                risk TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_assessments_time ON ai_assessments(created_at DESC);
            """
        )
        init_portfolio_db(conn)
        init_adaptive_db(conn)


def _request_binance(path: str, params: dict[str, Any]) -> Any:
    errors: list[str] = []
    # A single public REST request is attempted against each domain until one works.
    for base in dict.fromkeys(BINANCE_BASES):
        try:
            response = requests.get(
                f"{base}{path}",
                params=params,
                timeout=(3.5, 6.5),
                headers={"User-Agent": "PRIMA-15/1.0 market-monitor"},
            )
            if response.status_code != 200:
                errors.append(f"{base}: HTTP {response.status_code}")
                continue
            payload = response.json()
            if isinstance(payload, dict) and payload.get("code") is not None:
                errors.append(f"{base}: {payload.get('msg', payload.get('code'))}")
                continue
            return payload
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{base}: {type(exc).__name__}")
    raise RuntimeError("No se pudo conectar con Binance. " + "; ".join(errors[-3:]))


def _request_bybit(path: str, params: dict[str, Any]) -> Any:
    errors: list[str] = []
    for base in BYBIT_BASES:
        try:
            response = requests.get(
                f"{base}{path}", params=params, timeout=(3.5, 6.5),
                headers={"User-Agent": "PRIMA-15/1.0 market-monitor"},
            )
            if response.status_code != 200:
                errors.append(f"{base}: HTTP {response.status_code}")
                continue
            payload = response.json()
            if not isinstance(payload, dict) or int(payload.get("retCode", -1)) != 0:
                errors.append(f"{base}: {payload.get('retMsg', 'respuesta no válida') if isinstance(payload, dict) else 'JSON no válido'}")
                continue
            return payload.get("result", {})
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{base}: {type(exc).__name__}")
    raise RuntimeError("No se pudo conectar con Bybit. " + "; ".join(errors[-2:]))


def _select_provider() -> str:
    global _active_provider
    candidates = ["binance", "bybit"] if MARKET_PROVIDER == "auto" else [MARKET_PROVIDER]
    failures = []
    for provider in candidates:
        try:
            if provider == "binance":
                _request_binance("/api/v3/ticker/price", {"symbol": SYMBOL})
                _request_binance("/api/v3/klines", {"symbol": SYMBOL, "interval": INTERVAL, "limit": 1})
            elif provider == "bybit":
                _request_bybit("/v5/market/tickers", {"category": "spot", "symbol": SYMBOL})
                _request_bybit("/v5/market/kline", {"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": 1})
            else:
                raise RuntimeError("MARKET_PROVIDER debe ser auto, binance o bybit")
            _active_provider = provider
            log.info("Fuente de mercado seleccionada: %s", provider)
            return provider
        except Exception as exc:
            failures.append(f"{provider}: {exc}")
    raise RuntimeError("No hay fuente de mercado disponible. " + " | ".join(failures))


def _prepare_provider_database(provider: str) -> None:
    previous_provider = get_meta("provider")
    previous_symbol = get_meta("symbol")
    if (previous_provider and previous_provider != provider) or (previous_symbol and previous_symbol != SYMBOL):
        log.warning(
            "Cambiando mercado (%s/%s → %s/%s); se reinicia el histórico para no mezclar series",
            previous_provider, previous_symbol, provider, SYMBOL,
        )
        with db_connect() as conn:
            conn.execute("DELETE FROM signal_outcomes")
            conn.execute("DELETE FROM signals")
            conn.execute("DELETE FROM backtest_samples")
            conn.execute("DELETE FROM ai_assessments")
            conn.execute("DELETE FROM adaptive_outcomes")
            conn.execute("DELETE FROM adaptive_predictions")
            conn.execute("DELETE FROM adaptive_profile_history")
            conn.execute("DELETE FROM candles")
            conn.execute("DELETE FROM fast_candles_1m")
            conn.execute("DELETE FROM meta WHERE key IN ('backtest_bar', 'ai_last_result')")
    set_meta("provider", provider)
    set_meta("symbol", SYMBOL)


def _ensure_provider() -> None:
    if _active_provider is None:
        provider = _select_provider()
        _prepare_provider_database(provider)


def _try_runtime_failover() -> bool:
    """Switch venue only after a health check; never mix candles across venues."""
    if MARKET_PROVIDER != "auto":
        return False
    previous = _active_provider
    try:
        provider = _select_provider()
        if provider == previous:
            return False
        _prepare_provider_database(provider)
        fetch_history()
        fetch_recent_klines()
        fetch_fast_history()
        fetch_recent_fast_klines()
        log.warning("Failover de mercado completado: %s → %s", previous, provider)
        return True
    except Exception as exc:
        log.warning("No se pudo completar el failover de mercado: %s", exc)
        return False


def _decode_bybit_klines(rows: list[list[Any]]) -> list[tuple[Any, ...]]:
    decoded = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        open_time = int(row[0])
        decoded.append((
            open_time, float(row[1]), float(row[2]), float(row[3]), float(row[4]),
            float(row[5]), open_time + INTERVAL_MS - 1,
            float(row[6]) if len(row) > 6 else 0.0, 0,
        ))
    return decoded


def _save_decoded_klines(data: list[tuple[Any, ...]]) -> int:
    if not data:
        return 0
    with db_connect() as conn:
        conn.executemany(
            """INSERT INTO candles
               (open_time, open, high, low, close, volume, close_time, quote_volume, trades)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(open_time) DO UPDATE SET
                 open=excluded.open, high=excluded.high, low=excluded.low,
                 close=excluded.close, volume=excluded.volume,
                 close_time=excluded.close_time, quote_volume=excluded.quote_volume,
                 trades=excluded.trades""",
            data,
        )
        cutoff = int(time.time() * 1000) - (HISTORY_CANDLES + 200) * INTERVAL_MS
        conn.execute("DELETE FROM candles WHERE open_time < ?", (cutoff,))
    return len(data)


def _decode_klines(rows: list[list[Any]]) -> list[tuple[Any, ...]]:
    decoded = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 9:
            continue
        decoded.append((
            int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]),
            float(row[5]), int(row[6]), float(row[7]), int(row[8]),
        ))
    return decoded


def save_klines(rows: list[list[Any]]) -> int:
    return _save_decoded_klines(_decode_klines(rows))


def fetch_history() -> int:
    _ensure_provider()
    target = HISTORY_CANDLES
    now_ms = int(time.time() * 1000)
    fetched: list[tuple[Any, ...]] = []
    if _active_provider == "binance":
        start = max(0, now_ms - target * INTERVAL_MS)
        raw_rows: list[list[Any]] = []
        while len(raw_rows) < target:
            limit = min(1000, target - len(raw_rows))
            rows = _request_binance(
                "/api/v3/klines",
                {"symbol": SYMBOL, "interval": INTERVAL, "startTime": start, "limit": limit},
            )
            if not isinstance(rows, list) or not rows:
                break
            raw_rows.extend(rows)
            next_start = int(rows[-1][0]) + INTERVAL_MS
            if next_start <= start or len(rows) < limit:
                break
            start = next_start
        fetched = _decode_klines(raw_rows)
    else:
        end = now_ms
        while len(fetched) < target:
            limit = min(1000, target - len(fetched))
            result = _request_bybit(
                "/v5/market/kline",
                {"category": "spot", "symbol": SYMBOL, "interval": "15", "end": end, "limit": limit},
            )
            rows = result.get("list", []) if isinstance(result, dict) else []
            page = _decode_bybit_klines(rows)
            if not page:
                break
            fetched.extend(page)
            earliest = min(item[0] for item in page)
            if earliest >= end:
                break
            end = earliest - 1
            if len(page) < limit:
                break
    # Bybit responds newest-first; sorting and de-duplicating creates chronological candles.
    unique = {int(row[0]): row for row in fetched}
    saved = _save_decoded_klines([unique[k] for k in sorted(unique)][-target:])
    log.info("Historial inicial: %s velas guardadas para %s (%s)", saved, SYMBOL, _active_provider)
    return saved


def fetch_recent_klines() -> int:
    _ensure_provider()
    if _active_provider == "bybit":
        result = _request_bybit(
            "/v5/market/kline",
            {"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": 4},
        )
        rows = result.get("list", []) if isinstance(result, dict) else []
        return _save_decoded_klines(_decode_bybit_klines(rows))
    rows = _request_binance(
        "/api/v3/klines",
        {"symbol": SYMBOL, "interval": INTERVAL, "limit": 4},
    )
    return save_klines(rows if isinstance(rows, list) else [])


def _decode_fast_binance_klines(rows: list[list[Any]]) -> list[tuple[Any, ...]]:
    decoded: list[tuple[Any, ...]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 11:
            continue
        decoded.append((
            int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]),
            float(row[5]), float(row[7]), int(row[8]), float(row[9]), float(row[10]), int(row[6]),
        ))
    return decoded


def _decode_fast_bybit_klines(rows: list[list[Any]]) -> list[tuple[Any, ...]]:
    decoded: list[tuple[Any, ...]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            continue
        opened = int(row[0])
        decoded.append((
            opened, float(row[1]), float(row[2]), float(row[3]), float(row[4]),
            float(row[5]), float(row[6]), 0, 0.0, 0.0, opened + 59999,
        ))
    return decoded


def _save_fast_klines(rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    with db_connect() as conn:
        conn.executemany(
            """INSERT INTO fast_candles_1m
               (open_time, open, high, low, close, volume, quote_volume, trades,
                taker_buy_base, taker_buy_quote, close_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(open_time) DO UPDATE SET
                 open=excluded.open, high=excluded.high, low=excluded.low,
                 close=excluded.close, volume=excluded.volume,
                 quote_volume=excluded.quote_volume, trades=excluded.trades,
                 taker_buy_base=excluded.taker_buy_base,
                 taker_buy_quote=excluded.taker_buy_quote, close_time=excluded.close_time""",
            rows,
        )
        cutoff = int(time.time() * 1000) - (FAST_MARKET_HISTORY_CANDLES + 20) * 60000
        conn.execute("DELETE FROM fast_candles_1m WHERE open_time < ?", (cutoff,))
    return len(rows)


def load_fast_candles(limit: int = FAST_MARKET_HISTORY_CANDLES) -> list[dict[str, Any]]:
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT open_time, open, high, low, close, volume, quote_volume, trades,
                      taker_buy_base, taker_buy_quote, close_time
               FROM fast_candles_1m ORDER BY open_time DESC LIMIT ?""",
            (max(1, min(3000, int(limit))),),
        ).fetchall()
    rows.reverse()
    return [dict(row) for row in rows]


def fetch_fast_history() -> int:
    """Load up to 3000 one-minute candles for short-horizon TP-first analogies."""
    _ensure_provider()
    target = FAST_MARKET_HISTORY_CANDLES
    now_ms = int(time.time() * 1000)
    fetched: list[tuple[Any, ...]] = []
    if _active_provider == "bybit":
        end = now_ms
        while len(fetched) < target:
            limit = min(1000, target - len(fetched))
            result = _request_bybit(
                "/v5/market/kline",
                {"category": "spot", "symbol": SYMBOL, "interval": "1", "end": end, "limit": limit},
            )
            raw = result.get("list", []) if isinstance(result, dict) else []
            page = _decode_fast_bybit_klines(raw)
            if not page:
                break
            fetched.extend(page)
            earliest = min(item[0] for item in page)
            if earliest >= end or len(page) < limit:
                break
            end = earliest - 1
    else:
        start = max(0, now_ms - target * 60_000)
        while len(fetched) < target:
            limit = min(1000, target - len(fetched))
            raw = _request_binance(
                "/api/v3/klines", {"symbol": SYMBOL, "interval": "1m", "startTime": start, "limit": limit}
            )
            page = _decode_fast_binance_klines(raw if isinstance(raw, list) else [])
            if not page:
                break
            fetched.extend(page)
            next_start = int(page[-1][0]) + 60_000
            if next_start <= start or len(page) < limit:
                break
            start = next_start
    unique = {int(row[0]): row for row in fetched}
    saved = _save_fast_klines([unique[key] for key in sorted(unique)][-target:])
    log.info("Historial rápido: %s velas de 1m guardadas para %s (%s)", saved, SYMBOL, _active_provider)
    return saved


def fetch_cross_market_context() -> dict[str, Any]:
    """Read BTC/ETH 1m momentum as a contextual confirmation for RENDER."""
    _ensure_provider()
    now_ms = int(time.time() * 1000)
    assets: dict[str, Any] = {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        try:
            if _active_provider == "bybit":
                result = _request_bybit(
                    "/v5/market/kline",
                    {"category": "spot", "symbol": symbol, "interval": "1", "limit": 15},
                )
                raw = result.get("list", []) if isinstance(result, dict) else []
                rows = _decode_fast_bybit_klines(raw)
            else:
                raw = _request_binance(
                    "/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": 15}
                )
                rows = _decode_fast_binance_klines(raw if isinstance(raw, list) else [])
            rows = sorted(rows, key=lambda row: int(row[0]))
            closed = [row for row in rows if int(row[10]) <= now_ms and float(row[4]) > 0]
            if len(closed) < 11:
                assets[symbol] = {"available": False, "reason": "Faltan velas 1m cerradas."}
                continue
            closes = [float(row[4]) for row in closed]
            mark = closes[-1]
            returns = {}
            for minutes in (3, 5, 10):
                prior = closes[-(minutes + 1)]
                returns[f"roc_{minutes}m_pct"] = round((mark / prior - 1.0) * 100.0, 5) if prior > 0 else 0.0
            last_close = int(closed[-1][10])
            assets[symbol] = {
                "available": True,
                "last_price": round(mark, 10),
                **returns,
                "last_close_ms": last_close,
                "age_seconds": round(max(0.0, (now_ms - last_close) / 1000.0), 2),
            }
        except Exception as exc:
            assets[symbol] = {"available": False, "reason": str(exc)[:160]}
    available = [row for row in assets.values() if row.get("available")]
    return {
        "available": bool(available),
        "assets": assets,
        "provider": _active_provider,
        "retrieved_at_ms": now_ms,
        "interpretation": "Contexto externo de mercado; solo refuerza o contradice, no sustituye RENDER.",
    }


def fetch_recent_fast_klines() -> int:
    _ensure_provider()
    if _active_provider == "bybit":
        result = _request_bybit(
            "/v5/market/kline",
            {"category": "spot", "symbol": SYMBOL, "interval": "1", "limit": 5},
        )
        raw = result.get("list", []) if isinstance(result, dict) else []
        decoded = _decode_fast_bybit_klines(raw)
    else:
        raw = _request_binance("/api/v3/klines", {"symbol": SYMBOL, "interval": "1m", "limit": 5})
        decoded = _decode_fast_binance_klines(raw if isinstance(raw, list) else [])
    unique = {int(row[0]): row for row in decoded}
    return _save_fast_klines([unique[key] for key in sorted(unique)])


def fetch_fast_microstructure() -> dict[str, Any]:
    """Read-only order book and recent aggressor trades from the active spot venue."""
    retrieved_at = int(time.time() * 1000)
    if _active_provider == "bybit":
        book_raw = _request_bybit(
            "/v5/market/orderbook",
            {"category": "spot", "symbol": SYMBOL, "limit": 50},
        )
        bids = book_raw.get("b", []) if isinstance(book_raw, dict) else []
        asks = book_raw.get("a", []) if isinstance(book_raw, dict) else []
        trade_raw = _request_bybit(
            "/v5/market/recent-trade",
            {"category": "spot", "symbol": SYMBOL, "limit": 60},
        )
        trades_list = trade_raw.get("list", []) if isinstance(trade_raw, dict) else []
        normalized_trades = [
            {"price": float(item.get("price", 0)), "qty": float(item.get("size", 0)),
             "side": str(item.get("side", "")).upper(), "time_ms": int(item.get("time", retrieved_at))}
            for item in trades_list if isinstance(item, dict)
        ]
    else:
        book_raw = _request_binance("/api/v3/depth", {"symbol": SYMBOL, "limit": 20})
        bids = book_raw.get("bids", []) if isinstance(book_raw, dict) else []
        asks = book_raw.get("asks", []) if isinstance(book_raw, dict) else []
        trade_rows = _request_binance("/api/v3/aggTrades", {"symbol": SYMBOL, "limit": 500})
        normalized_trades = []
        for item in trade_rows if isinstance(trade_rows, list) else []:
            # Binance m=true means buyer was maker, hence the taker aggressor was SELL.
            buyer_maker = bool(item.get("m", False))
            normalized_trades.append({
                "price": float(item.get("p", 0)), "qty": float(item.get("q", 0)),
                "side": "SELL" if buyer_maker else "BUY", "time_ms": int(item.get("T", retrieved_at)),
            })
    return {
        "orderbook": {"timestamp_ms": retrieved_at, "bids": bids, "asks": asks},
        "trades": {"timestamp_ms": retrieved_at, "trades": normalized_trades},
        "retrieved_at_ms": retrieved_at,
        "provider": _active_provider,
    }


def fetch_last_price() -> float:
    _ensure_provider()
    if _active_provider == "bybit":
        result = _request_bybit("/v5/market/tickers", {"category": "spot", "symbol": SYMBOL})
        listings = result.get("list", []) if isinstance(result, dict) else []
        if not listings:
            raise RuntimeError(f"Bybit no devolvió el ticker {SYMBOL}.")
        price = float(listings[0]["lastPrice"])
    else:
        data = _request_binance("/api/v3/ticker/price", {"symbol": SYMBOL})
        price = float(data["price"])
    if price <= 0:
        raise RuntimeError("La fuente devolvió un precio no válido.")
    return price


def fetch_24h() -> dict[str, float] | None:
    _ensure_provider()
    if _active_provider == "bybit":
        result = _request_bybit("/v5/market/tickers", {"category": "spot", "symbol": SYMBOL})
        listings = result.get("list", []) if isinstance(result, dict) else []
        if not listings:
            return None
        data = listings[0]
        return {
            "change_pct": float(data.get("price24hPcnt", 0.0)) * 100.0,
            "high": float(data.get("highPrice24h", 0.0)),
            "low": float(data.get("lowPrice24h", 0.0)),
            "volume": float(data.get("turnover24h", 0.0)),
        }
    data = _request_binance("/api/v3/ticker/24hr", {"symbol": SYMBOL})
    return {
        "change_pct": float(data.get("priceChangePercent", 0.0)),
        "high": float(data.get("highPrice", 0.0)),
        "low": float(data.get("lowPrice", 0.0)),
        "volume": float(data.get("quoteVolume", 0.0)),
    }


def load_candles(limit: int = 2500) -> list[dict[str, Any]]:
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT open_time, open, high, low, close, volume, close_time, quote_volume, trades
               FROM candles ORDER BY open_time DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    rows.reverse()
    return [dict(row) for row in rows]


def committee_runs_today(now_ms: int | None = None) -> int:
    now_ms = int(now_ms or time.time() * 1000)
    day_start = now_ms - (now_ms % 86_400_000)
    with db_connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM committee_runs WHERE created_at >= ?",
            (day_start,),
        ).fetchone()
    return int(row["n"] if row else 0)


def record_committee_run(trigger_reason: str, setup_key: str = "", now_ms: int | None = None) -> None:
    now_ms = int(now_ms or time.time() * 1000)
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO committee_runs(created_at, trigger_reason, setup_key) VALUES (?, ?, ?)",
            (now_ms, str(trigger_reason)[:40], str(setup_key)[:100]),
        )
        # Keep a compact audit trail; the daily quota query only needs recent 7 days.
        conn.execute("DELETE FROM committee_runs WHERE created_at < ?", (now_ms - 7 * 86_400_000,))


def set_meta(key: str, value: str) -> None:
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_meta(key: str) -> str | None:
    with db_connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def rebuild_backtest(closed_candles: list[dict[str, Any]]) -> dict[str, Any]:
    samples = backtest(closed_candles, threshold=BASE_THRESHOLD, horizon=4)
    with db_connect() as conn:
        conn.execute("DELETE FROM backtest_samples")
        conn.executemany(
            """INSERT INTO backtest_samples
               (bar_time, side, score, entry, return_pct, mfe_pct, mae_pct, correct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(
                x["bar_time"], x["side"], x["score"], x["entry"],
                x["return_pct"], x["mfe_pct"], x["mae_pct"], x["correct"],
            ) for x in samples],
        )
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('backtest_bar','') ON CONFLICT(key) DO NOTHING"
        )
    if not samples:
        return {"samples": 0, "win_rate": None, "avg_return": None, "profit_factor": None}
    wins = [x["return_pct"] for x in samples if x["return_pct"] > 0]
    losses = [abs(x["return_pct"]) for x in samples if x["return_pct"] < 0]
    gross_win = sum(wins)
    gross_loss = sum(losses)
    return {
        "samples": len(samples),
        "win_rate": round(sum(x["correct"] for x in samples) / len(samples) * 100, 1),
        "avg_return": round(sum(x["return_pct"] for x in samples) / len(samples), 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else (None if not gross_win else 99.0),
    }


def calculate_learning() -> dict[str, Any]:
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT bar_time, correct, return_pct FROM (
                   SELECT bar_time, correct, return_pct FROM backtest_samples
                   UNION ALL
                   SELECT s.bar_time, o.correct, o.return_pct
                   FROM signals s JOIN signal_outcomes o ON o.signal_id=s.id
                   WHERE o.horizon=4
               ) ORDER BY bar_time DESC LIMIT 60"""
        ).fetchall()
    count = len(rows)
    win_rate = (sum(int(r["correct"]) for r in rows) / count * 100.0) if count else None
    avg_return = (sum(float(r["return_pct"]) for r in rows) / count) if count else None
    threshold = BASE_THRESHOLD
    if count >= 20 and win_rate is not None:
        # Conservative adaptation: poor recent accuracy demands stronger evidence;
        # good accuracy relaxes the threshold only slightly. Range stays bounded.
        threshold = max(64.0, min(76.0, BASE_THRESHOLD + (50.0 - win_rate) * 0.18))
    return {
        "samples": count,
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "avg_return": round(avg_return, 4) if avg_return is not None else None,
        "threshold": round(threshold, 1),
        "horizon_bars": 4,
    }


def recent_backtest_examples(limit: int = 16) -> list[dict[str, Any]]:
    """Return recent, already-labelled four-candle backtest examples for LLM context."""
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT bar_time, side, score, return_pct, mfe_pct, mae_pct, correct
               FROM backtest_samples ORDER BY bar_time DESC LIMIT ?""",
            (max(1, min(30, int(limit))),),
        ).fetchall()
    return [
        {
            "bar_time": int(row["bar_time"]),
            "side": str(row["side"]),
            "score": round(float(row["score"]), 2),
            "return_pct": round(float(row["return_pct"]), 5),
            "mfe_pct": round(float(row["mfe_pct"]), 5),
            "mae_pct": round(float(row["mae_pct"]), 5),
            "correct": bool(row["correct"]),
        }
        for row in rows
    ]


def calculate_adaptive_learning() -> dict[str, Any]:
    with db_connect() as conn:
        profile = calculate_adaptive_profile(
            conn,
            opening_fee_pct=SIM_OPEN_FEE_PCT,
            position_size_pct=SIM_POSITION_SIZE_PCT,
        )
        profile["recent_changes"] = recent_profile_history(conn, 6)
    return profile


def log_live_signal(analysis: dict[str, Any], price: float, bar_time: int) -> None:
    side = analysis.get("signal")
    if side not in ("LONG", "SHORT") or price <= 0:
        return
    temporal = analysis.get("prime_long" if side == "LONG" else "prime_short", {})
    score = analysis["long_score"] if side == "LONG" else analysis["short_score"]
    with db_connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO signals
               (bar_time, side, created_at, entry, score, rsi, prime_elapsed, prime_nearest)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bar_time, side, int(time.time() * 1000), price, score, analysis.get("rsi", 50),
                temporal.get("elapsed"), temporal.get("nearest"),
            ),
        )


def store_ai_assessment(ai_result: dict[str, Any], bar_time: int) -> None:
    votes = ai_result.get("votes", [])
    if not votes:
        return
    created_at = int(ai_result.get("updated_at") or time.time() * 1000)
    with db_connect() as conn:
        conn.executemany(
            """INSERT INTO ai_assessments
               (bar_time, created_at, requested_model, served_model, decision, confidence, summary, risk)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(
                int(bar_time), created_at,
                str(vote.get("requested_model", vote.get("model", "unknown"))),
                str(vote.get("model", "unknown")),
                str(vote.get("decision", "WAIT")),
                float(vote.get("confidence", 0) or 0),
                str(vote.get("summary", ""))[:500],
                str(vote.get("risk", ""))[:350],
            ) for vote in votes],
        )


def evaluate_live_signals(closed_candles: list[dict[str, Any]], horizon: int = 4) -> None:
    if len(closed_candles) < horizon + 2:
        return
    index_by_time = {int(c["open_time"]): i for i, c in enumerate(closed_candles)}
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT s.id, s.bar_time, s.side, s.entry FROM signals s
               LEFT JOIN signal_outcomes o ON o.signal_id=s.id AND o.horizon=?
               WHERE o.id IS NULL ORDER BY s.bar_time LIMIT 300""",
            (horizon,),
        ).fetchall()
        pending = []
        for row in rows:
            idx = index_by_time.get(int(row["bar_time"]))
            if idx is None or idx + horizon >= len(closed_candles):
                continue
            future = closed_candles[idx + 1: idx + horizon + 1]
            if len(future) < horizon:
                continue
            entry = float(row["entry"])
            if entry <= 0:
                continue
            side = row["side"]
            if side == "LONG":
                ret = (float(future[-1]["close"]) / entry - 1.0) * 100.0
                mfe = (max(float(c["high"]) for c in future) / entry - 1.0) * 100.0
                mae = (min(float(c["low"]) for c in future) / entry - 1.0) * 100.0
            else:
                ret = (entry / float(future[-1]["close"]) - 1.0) * 100.0
                mfe = (entry / min(float(c["low"]) for c in future) - 1.0) * 100.0
                mae = (entry / max(float(c["high"]) for c in future) - 1.0) * 100.0
            pending.append((int(row["id"]), horizon, ret, mfe, mae, 1 if ret > 0 else 0, int(time.time() * 1000)))
        if pending:
            conn.executemany(
                """INSERT OR IGNORE INTO signal_outcomes
                   (signal_id, horizon, return_pct, mfe_pct, mae_pct, correct, evaluated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                pending,
            )


def recent_signals(limit: int = 12) -> list[dict[str, Any]]:
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT s.bar_time, s.side, s.entry, s.score, s.rsi, s.prime_elapsed,
                      s.prime_nearest, o.return_pct, o.correct
               FROM signals s LEFT JOIN signal_outcomes o ON o.signal_id=s.id AND o.horizon=4
               ORDER BY s.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def chart_candles(limit: int = 150) -> list[dict[str, Any]]:
    return [
        {
            "t": int(c["open_time"]), "o": c["open"], "h": c["high"],
            "l": c["low"], "c": c["close"], "v": c["volume"],
        }
        for c in load_candles(limit)
    ]


def _background_worker() -> None:
    init_db()
    connection = "offline"
    error = None
    last_kline_fetch = 0.0
    last_fast_fetch = 0.0
    last_micro_fetch = 0.0
    last_24h_fetch = 0.0
    market_24h: dict[str, float] | None = None
    fast_microstructure: dict[str, Any] | None = None
    cross_market_context: dict[str, Any] | None = None
    fast_entry_engine = FastEntryEngine()
    elliott_cache_key: tuple[int, int, int, int] | None = None
    elliott_cache: dict[str, Any] = {}
    fast_entry_context: dict[str, Any] = {"status": "WAITING", "watch_candidate": False, "opportunities": {}}
    price: float | None = None
    backtest_summary: dict[str, Any] = {"samples": 0, "win_rate": None, "avg_return": None, "profit_factor": None}
    ai_result: dict[str, Any] = initial_ai_state()
    adaptive_profile: dict[str, Any] = {
        "status": "LEARNING", "learning_ready": False, "sample_count": 0,
        "trend_agreement_min_pct": 60.0, "take_profit_target_pct": SIM_TAKE_PROFIT_PCT,
        "dimension_weights": {"temporal": 1.0, "amplitude": 1.0, "behavior": 1.0},
        "model_reliability": [],
    }
    if api_key_present():
        try:
            saved_ai = get_meta("ai_last_result")
            if saved_ai:
                parsed_ai = json.loads(saved_ai)
                if isinstance(parsed_ai, dict):
                    ai_result = parsed_ai
        except (ValueError, TypeError):
            log.warning("No se pudo restaurar la última evaluación IA almacenada")
    if api_key_present():
        key_fingerprint = hashlib.sha256(os.getenv("OPENROUTER_API_KEY", "").strip().encode("utf-8")).hexdigest()
        old_fingerprint = get_meta("openrouter_key_fingerprint")
        if old_fingerprint != key_fingerprint:
            # A newly entered/replaced key should be tested immediately, not wait out an old cooldown.
            set_meta("openrouter_key_fingerprint", key_fingerprint)
            set_meta("ai_last_request_ms", "0")
            last_ai_run = 0.0
        else:
            try:
                last_ai_run = int(get_meta("ai_last_request_ms") or "0") / 1000.0
            except ValueError:
                last_ai_run = 0.0
    else:
        if get_meta("openrouter_key_fingerprint"):
            set_meta("openrouter_key_fingerprint", "")
            set_meta("ai_last_request_ms", "0")
        last_ai_run = 0.0
    first_attempt = True

    while True:
        try:
            if first_attempt:
                try:
                    previous_provider = get_meta("provider")
                    provider = _select_provider()
                    _prepare_provider_database(provider)
                    if previous_provider and previous_provider != provider:
                        ai_result = initial_ai_state()
                        last_ai_run = 0.0
                        set_meta("ai_last_request_ms", "0")
                    existing = load_candles(HISTORY_CANDLES)
                    if len(existing) < HISTORY_CANDLES:
                        fetch_history()
                    if len(load_fast_candles(FAST_MARKET_HISTORY_CANDLES)) < int(FAST_MARKET_HISTORY_CANDLES * 0.75):
                        fetch_fast_history()
                    # Restore the fitted profile only after the venue/symbol has been
                    # validated, so a provider switch cannot briefly apply stale weights.
                    with db_connect() as startup_learning_conn:
                        adaptive_profile = calculate_adaptive_profile(
                            startup_learning_conn,
                            opening_fee_pct=SIM_OPEN_FEE_PCT,
                            position_size_pct=SIM_POSITION_SIZE_PCT,
                        )
                        adaptive_profile["recent_changes"] = recent_profile_history(startup_learning_conn, 6)
                        save_profile_if_changed(
                            startup_learning_conn, adaptive_profile,
                            reason="restauración de perfil al iniciar",
                        )
                except Exception as exc:
                    log.warning("No se pudo iniciar la fuente de mercado: %s", exc)
                first_attempt = False

            price_fresh = False
            try:
                price = fetch_last_price()
                price_fresh = True
                connection = "connected"
                error = None
            except Exception as exc:
                if _try_runtime_failover():
                    ai_result = initial_ai_state()
                    last_ai_run = 0.0
                    fast_microstructure = None
                    cross_market_context = None
                    last_fast_fetch = 0.0
                    last_micro_fetch = 0.0
                    fast_entry_engine.cache_key = None
                    fast_entry_engine.reference = None
                    elliott_cache_key = None
                    elliott_cache = {}
                    try:
                        price = fetch_last_price()
                        price_fresh = True
                        connection = "connected"
                        error = None
                        last_kline_fetch = time.time()
                    except Exception as retry_exc:
                        connection = "degraded" if load_candles(1) else "offline"
                        error = str(retry_exc)
                else:
                    connection = "degraded" if load_candles(1) else "offline"
                    error = str(exc)

            now = time.time()
            if now - last_kline_fetch >= KLINE_REFRESH_SECONDS:
                try:
                    fetch_recent_klines()
                    last_kline_fetch = now
                except Exception as exc:
                    if _try_runtime_failover():
                        ai_result = initial_ai_state()
                        last_ai_run = 0.0
                        fast_microstructure = None
                        cross_market_context = None
                        last_fast_fetch = 0.0
                        last_micro_fetch = 0.0
                        fast_entry_engine.cache_key = None
                        fast_entry_engine.reference = None
                        elliott_cache_key = None
                        elliott_cache = {}
                        last_kline_fetch = time.time()
                        error = None
                        connection = "connected"
                    else:
                        error = str(exc)
                        connection = "degraded" if load_candles(1) else "offline"

            if now - last_24h_fetch >= 90:
                try:
                    market_24h = fetch_24h()
                    last_24h_fetch = now
                except Exception as exc:
                    last_24h_fetch = now
                    log.debug("24h ticker temporalmente no disponible: %s", exc)

            if now - last_fast_fetch >= FAST_MARKET_REFRESH_SECONDS:
                try:
                    fetch_recent_fast_klines()
                    last_fast_fetch = now
                except Exception as fast_candle_exc:
                    last_fast_fetch = now
                    log.debug("Velas rápidas 1m temporalmente no disponibles: %s", fast_candle_exc)

            if now - last_micro_fetch >= FAST_MARKET_REFRESH_SECONDS:
                try:
                    fast_microstructure = fetch_fast_microstructure()
                except Exception as micro_exc:
                    log.debug("Order book/flujo agresor temporalmente no disponibles: %s", micro_exc)
                try:
                    cross_market_context = fetch_cross_market_context()
                except Exception as cross_exc:
                    log.debug("Contexto BTC/ETH no disponible: %s", cross_exc)
                finally:
                    last_micro_fetch = now

            fast_candles = load_fast_candles(FAST_MARKET_HISTORY_CANDLES)

            candles = load_candles(HISTORY_CANDLES)
            if candles:
                if price is None:
                    price = float(candles[-1]["close"])
                now_ms = int(time.time() * 1000)
                closed = [c for c in candles if int(c.get("close_time", 0)) < now_ms - 1500]
                if len(closed) >= 180:
                    latest_closed_time = int(closed[-1]["open_time"])
                    if get_meta("backtest_bar") != str(latest_closed_time):
                        try:
                            backtest_summary = rebuild_backtest(closed[-1800:])
                            set_meta("backtest_bar", str(latest_closed_time))
                            log.info("Backtest recalculado: %s señales", backtest_summary.get("samples", 0))
                        except Exception as exc:
                            log.exception("Falló el backtest: %s", exc)
                    evaluate_live_signals(closed)
                    try:
                        with db_connect() as adaptive_conn:
                            newly_labelled = evaluate_pending_predictions(adaptive_conn, closed, now_ms)
                            adaptive_profile = calculate_adaptive_profile(
                                adaptive_conn,
                                opening_fee_pct=SIM_OPEN_FEE_PCT,
                                position_size_pct=SIM_POSITION_SIZE_PCT,
                            )
                            adaptive_profile["recent_changes"] = recent_profile_history(adaptive_conn, 6)
                            changed = save_profile_if_changed(
                                adaptive_conn, adaptive_profile,
                                reason=f"walk-forward: {newly_labelled} nuevos resultados etiquetados",
                            )
                        if newly_labelled:
                            log.info("Aprendizaje walk-forward: %s nuevos resultados; perfil=%s", newly_labelled, adaptive_profile.get("status"))
                        if changed:
                            log.info(
                                "Perfil adaptativo actualizado: umbral tendencia %.1f%% · TP %.2f%% · pesos %s",
                                adaptive_profile.get("trend_agreement_min_pct", 60.0),
                                adaptive_profile.get("take_profit_target_pct", SIM_TAKE_PROFIT_PCT),
                                adaptive_profile.get("dimension_weights", {}),
                            )
                    except Exception as learning_exc:
                        log.exception("No se pudo actualizar el aprendizaje adaptativo: %s", learning_exc)
                learning = calculate_learning()
                latest_bar_time = int(candles[-1].get("open_time", 0))
                analysis = analyze(
                    candles[-350:], price, threshold=learning["threshold"],
                    adaptive_weights=adaptive_profile.get("dimension_weights", {}),
                )
                # Elliott count uses the full closed history, unlike the lightweight
                # short-window technical engine. 1h/4h bars are aggregated from 15m.
                closed_1m = [c for c in fast_candles if int(c.get("close_time", 0)) < now_ms - 1000]
                elliott_key = (
                    int(closed[-1].get("open_time", 0)) if closed else 0,
                    int(closed_1m[-1].get("open_time", 0)) if closed_1m else 0,
                    len(closed), len(closed_1m),
                )
                if elliott_key != elliott_cache_key:
                    try:
                        elliott_cache = analyze_multi_degree_elliott(
                            closed[-HISTORY_CANDLES:], closed_1m[-3000:], analysis,
                        )
                        elliott_cache_key = elliott_key
                        log.info(
                            "Conteo Elliott actualizado: 15m=%s velas, pivotes=%s, síntesis=%s",
                            elliott_cache.get("history", {}).get("15m_candles_processed", 0),
                            (elliott_cache.get("timeframes", {}).get("15m", {}) or {}).get("pivot_count_total", 0),
                            (elliott_cache.get("multi_degree_synthesis", {}) or {}).get("direction", "UNDETERMINED"),
                        )
                    except Exception as elliott_exc:
                        log.exception("Falló el conteo Elliott: %s", elliott_exc)
                        elliott_cache = {"status": "ERROR", "error": str(elliott_exc)[:240]}
                        elliott_cache_key = elliott_key
                analysis["elliott_wave"] = elliott_cache or {"status": "WAITING_FOR_HISTORY"}
                analysis["elliott_prime_synthesis"] = (elliott_cache or {}).get("prime_elliott_relation", {})
                # Fast entry is checked against the main 15m structural levels as well
                # as the local 1m order book/trade flow. This prevents buying straight
                # into a nearby 15m resistance or shorting into a nearby 15m support.
                fast_entry_context = fast_entry_engine.analyze(
                    fast_candles, float(price or 0),
                    (fast_microstructure or {}).get("orderbook"),
                    (fast_microstructure or {}).get("trades"),
                    target_pct=float(adaptive_profile.get("take_profit_target_pct", SIM_TAKE_PROFIT_PCT)),
                    adverse_pct=FAST_ENTRY_ADVERSE_PCT,
                    horizon_minutes=FAST_ENTRY_HORIZON_MINUTES,
                    min_samples=FAST_ENTRY_MIN_SAMPLES,
                    min_hit_rate_pct=FAST_ENTRY_MIN_HIT_RATE_PCT,
                    min_wilson_pct=FAST_ENTRY_MIN_WILSON_PCT,
                    min_live_confirmation_pct=FAST_ENTRY_MIN_LIVE_CONFIRMATION_PCT,
                    max_spread_bps=FAST_ENTRY_MAX_SPREAD_BPS,
                    max_minutes_to_target=FAST_ENTRY_MAX_MINUTES_TO_TARGET,
                    opening_fee_pct=SIM_OPEN_FEE_PCT,
                    exposure_pct=SIM_POSITION_SIZE_PCT,
                    cross_market_context=cross_market_context,
                    higher_timeframe_levels={
                        "support": analysis.get("support_31"),
                        "resistance": analysis.get("resistance_31"),
                        "source_interval": "15m",
                    },
                    reference_candles=FAST_ENTRY_REFERENCE_CANDLES,
                    min_directional_edge_gap_pct=FAST_ENTRY_MIN_DIRECTIONAL_EDGE_GAP_PCT,
                )

                # Manage the active paper position on every market poll, not only
                # when a new LLM committee request is due.
                portfolio_event = None
                if price_fresh and connection == "connected" and price and price > 0:
                    try:
                        with db_connect() as portfolio_conn:
                            portfolio_event = manage_take_profit(
                                portfolio_conn,
                                float(price),
                                target_profit_pct=float(adaptive_profile.get("take_profit_target_pct", SIM_TAKE_PROFIT_PCT)),
                            )
                        if portfolio_event:
                            log.info("Cierre simulado automático: %s", portfolio_event)
                    except Exception as portfolio_exc:
                        log.exception("Falló la gestión de Take Profit simulado: %s", portfolio_exc)

                # Scheduled reviews are deliberately conservative for free model quotas.
                # A fresh high-quality fast-entry setup can request an earlier review while
                # the local 1m/order-book engine keeps updating every 20 seconds.
                normal_committee_due = should_run_committee(
                    api_key_ready=api_key_present(),
                    price_fresh=price_fresh,
                    connection=connection,
                    now_seconds=now,
                    last_run_seconds=last_ai_run,
                    interval_seconds=OPENROUTER_INTERVAL_SECONDS,
                    portfolio_event=portfolio_event,
                )
                with db_connect() as pre_cycle_conn:
                    pre_cycle_portfolio = portfolio_snapshot(
                        pre_cycle_conn, float(price or 0),
                        take_profit_target_pct=float(adaptive_profile.get("take_profit_target_pct", SIM_TAKE_PROFIT_PCT)),
                        opening_fee_rate_pct=SIM_OPEN_FEE_PCT,
                        position_size_pct=SIM_POSITION_SIZE_PCT,
                    )
                is_flat = not bool(pre_cycle_portfolio.get("open_position"))
                fast_side = fast_entry_context.get("candidate_side")
                fast_bucket = int(fast_entry_context.get("last_closed_candle_ms", 0) or 0) // 300_000
                fast_setup_key = f"{fast_side}:{fast_bucket}" if fast_side in {"LONG", "SHORT"} and fast_entry_context.get("watch_candidate") else ""
                previous_fast_key = get_meta("last_fast_setup_key") or ""
                fast_trigger_due = bool(
                    api_key_present() and price_fresh and connection == "connected" and is_flat
                    and fast_setup_key and fast_setup_key != previous_fast_key
                    and now - last_ai_run >= FAST_SETUP_COOLDOWN_SECONDS
                )
                committee_due = normal_committee_due or fast_trigger_due
                if committee_due:
                    runs_today = committee_runs_today(int(now * 1000))
                    if runs_today >= OPENROUTER_MAX_COMMITTEES_PER_DAY:
                        ai_result = dict(ai_result or initial_ai_state())
                        ai_result.update({
                            "status": "budget_limited", "decision": "WAIT", "confidence": 0,
                            "risk": f"Límite diario de {OPENROUTER_MAX_COMMITTEES_PER_DAY} comités alcanzado. El motor local sigue vigilando; no se reutiliza la decisión anterior.",
                            "fast_entry_watch": fast_entry_context.get("candidate_side"),
                        })
                        set_meta("ai_last_result", json.dumps(ai_result, ensure_ascii=False, separators=(",", ":")))
                        log.warning("Comité OpenRouter omitido: límite diario %s alcanzado", OPENROUTER_MAX_COMMITTEES_PER_DAY)
                    else:
                        trigger_reason = "TAKE_PROFIT_REENTRY" if portfolio_event and portfolio_event.get("event") == "TAKE_PROFIT_CLOSED" else ("FAST_SETUP" if fast_trigger_due and not normal_committee_due else "SCHEDULED")
                        last_ai_run = now  # Persist before request so restarts/failures cannot cause a retry storm.
                        set_meta("ai_last_request_ms", str(int(now * 1000)))
                        record_committee_run(trigger_reason, fast_setup_key if fast_trigger_due else "", int(now * 1000))
                        if fast_trigger_due:
                            set_meta("last_fast_setup_key", fast_setup_key)
                        try:
                            with db_connect() as portfolio_conn:
                                portfolio_context = portfolio_snapshot(
                                    portfolio_conn, float(price or 0), history_limit=20,
                                    take_profit_target_pct=float(adaptive_profile.get("take_profit_target_pct", SIM_TAKE_PROFIT_PCT)),
                                    opening_fee_rate_pct=SIM_OPEN_FEE_PCT,
                                    position_size_pct=SIM_POSITION_SIZE_PCT,
                                )
                            with db_connect() as context_conn:
                                prior_ai_memory = recent_prediction_memory(context_conn, limit=12)
                            snapshot = build_snapshot(
                                candles, float(price or 0), analysis, SYMBOL, INTERVAL,
                                portfolio=portfolio_context,
                                learning_summary=learning,
                                backtest_summary=backtest_summary,
                                recent_backtest=recent_backtest_examples(20),
                                recent_signals=recent_signals(20),
                                prior_ai_memory=prior_ai_memory,
                                market_24h=market_24h,
                                market_provider=_active_provider,
                                fast_entry_context=fast_entry_context,
                                fast_candles=fast_candles,
                            )
                            snapshot["adaptive_learning"] = adaptive_profile
                            snapshot["context_manifest"]["adaptive_learning_profile_sent"] = True
                            snapshot["context_manifest"]["adaptive_profile_fields_sent"] = sorted(adaptive_profile.keys())
                            snapshot["context_manifest"]["adaptive_profile_history_rows_sent"] = len(adaptive_profile.get("recent_changes", []) or [])
                            snapshot.setdefault("trend_persistence", {})["required_agreement_pct"] = adaptive_profile.get("trend_agreement_min_pct", 60.0)
                            snapshot.setdefault("decision_policy", {})["continuity_min_agreement_pct"] = adaptive_profile.get("trend_agreement_min_pct", 60.0)
                            snapshot["decision_policy"]["take_profit_target_pct"] = adaptive_profile.get("take_profit_target_pct", SIM_TAKE_PROFIT_PCT)
                            ai_result = evaluate_snapshot(snapshot)
                            if ai_result.get("status") == "ok":
                                with db_connect() as adaptive_conn:
                                    prediction_id = record_ai_prediction(adaptive_conn, ai_result, snapshot)
                                ai_result["adaptive_prediction_id"] = prediction_id
                                if has_actionable_consensus(ai_result):
                                    # This result was obtained AFTER the TP close and from a
                                    # snapshot whose portfolio section is already FLAT.
                                    # Apply it immediately instead of deferring until the
                                    # next hourly committee cycle.
                                    with db_connect() as portfolio_conn:
                                        position_event = apply_llm_decision(
                                            portfolio_conn,
                                            decision=str(ai_result["decision"]),
                                            current_price=float(price or 0),
                                            confidence=float(ai_result.get("confidence", 0) or 0),
                                            reason=str(ai_result.get("policy_reason", ai_result.get("summary", ""))),
                                            fee_rate_pct=SIM_OPEN_FEE_PCT,
                                            exposure_pct=SIM_POSITION_SIZE_PCT,
                                        )
                                    if portfolio_event and portfolio_event.get("event") == "TAKE_PROFIT_CLOSED":
                                        position_event["reopened_after_take_profit"] = True
                                        position_event["closed_trade_event"] = portfolio_event
                                        ai_result["reentry_after_take_profit"] = True
                                        ai_result["closed_by_take_profit"] = portfolio_event
                                    ai_result["position_event"] = position_event
                                elif portfolio_event and portfolio_event.get("event") == "TAKE_PROFIT_CLOSED":
                                    # No valid directional consensus: remain FLAT. The next
                                    # scheduled committee may enter; never reuse the old vote.
                                    ai_result["reentry_after_take_profit"] = False
                            if portfolio_event and portfolio_event.get("event") == "TAKE_PROFIT_CLOSED":
                                ai_result["closed_by_take_profit"] = portfolio_event
                                ai_result.setdefault("reentry_after_take_profit", False)
                            set_meta("ai_last_result", json.dumps(ai_result, ensure_ascii=False, separators=(",", ":")))
                            if ai_result.get("status") == "ok":
                                store_ai_assessment(ai_result, int(analysis.get("last_candle_time") or latest_bar_time))
                            log.info(
                                "OpenRouter: status=%s decision=%s confidence=%s models=%s",
                                ai_result.get("status"), ai_result.get("decision"),
                                ai_result.get("confidence"), ai_result.get("models"),
                            )
                        except Exception as ai_exc:
                            log.exception("Falló el análisis OpenRouter: %s", ai_exc)
                            ai_result = {
                                "status": "error", "decision": "WAIT", "confidence": 0,
                                "consensus_pct": 0, "votes": [], "summary": "No se pudo completar la evaluación de OpenRouter.",
                                "risk": str(ai_exc)[:250], "models": [],
                                "updated_at": int(time.time() * 1000), "bar_time": latest_bar_time,
                            }
                            set_meta("ai_last_result", json.dumps(ai_result, ensure_ascii=False, separators=(",", ":")))
                elif not api_key_present():
                    ai_result = initial_ai_state()

                effective_analysis = dict(analysis)
                effective_analysis["algorithm_signal"] = analysis.get("signal", "NEUTRAL")
                ai_age = (
                    max(0, int(time.time() * 1000) - int(ai_result.get("updated_at") or 0)) / 1000
                    if ai_result.get("updated_at") else float("inf")
                )
                if (
                    ai_result.get("status") == "ok"
                    and ai_result.get("decision") in {"LONG", "SHORT"}
                    and ai_age <= OPENROUTER_INTERVAL_SECONDS + 90
                ):
                    effective_analysis["signal"] = ai_result["decision"]
                    effective_analysis["candidate"] = ai_result["decision"]
                    basis = str(ai_result.get("decision_basis", ""))
                    if basis == "EXCEPTION_LLM":
                        effective_analysis["phase"] = (
                            "EXCEPCIÓN DE MECHA / SOPORTE-RESISTENCIA · " + ai_result["decision"]
                        )
                    elif basis == "TREND_CONTINUITY_NEMOTRON":
                        effective_analysis["phase"] = (
                            "CONTINUIDAD 3/5/8 VALIDADA POR NEMOTRON · " + ai_result["decision"]
                        )
                    else:
                        effective_analysis["phase"] = (
                            "INCERTIDUMBRE DIRECCIONAL · POLÍTICA SIMULADA SHORT"
                        ) if ai_result["decision"] == "SHORT" else "COMITÉ LLM · " + ai_result["decision"]
                    effective_analysis["reasons"] = list(analysis.get("reasons", [])) + [
                        str(ai_result.get("policy_reason", "Dirección decidida por el comité LLM.")),
                        "La posición simulada se abre, mantiene o invierte según el nuevo consenso LLM.",
                    ]
                else:
                    effective_analysis["signal"] = "NEUTRAL"
                    effective_analysis["phase"] = "ESPERANDO DOS RESPUESTAS LLM VÁLIDAS Y RECIENTES"
                    effective_analysis["reasons"] = list(analysis.get("reasons", [])) + [
                        "Sin quórum LLM reciente no se abre ni se invierte una posición simulada."
                    ]
                with db_connect() as portfolio_conn:
                    portfolio_data = portfolio_snapshot(
                        portfolio_conn, float(price or 0),
                        take_profit_target_pct=float(adaptive_profile.get("take_profit_target_pct", SIM_TAKE_PROFIT_PCT)),
                        opening_fee_rate_pct=SIM_OPEN_FEE_PCT,
                        position_size_pct=SIM_POSITION_SIZE_PCT,
                    )
                log_live_signal(effective_analysis, float(price or 0), latest_bar_time)
                chart = [
                    {
                        "t": int(c["open_time"]), "o": c["open"], "h": c["high"],
                        "l": c["low"], "c": c["close"], "v": c["volume"],
                    }
                    for c in candles[-150:]
                ]
                committee_runs_current = committee_runs_today(int(time.time() * 1000))
                payload = {
                    "ready": True,
                    "connection": connection,
                    "error": error,
                    "symbol": SYMBOL,
                    "interval": INTERVAL,
                    "price": price,
                    "change_24h": market_24h,
                    "updated_at": int(time.time() * 1000),
                    "candle_count": len(candles),
                    "analysis": effective_analysis,
                    "algorithm_analysis": analysis,
                    "ai": ai_result,
                    "portfolio": portfolio_data,
                    "learning": learning,
                    "adaptive_learning": adaptive_profile,
                    "fast_entry": fast_entry_context,
                    "elliott_wave": elliott_cache,
                    "elliott_prime_synthesis": (elliott_cache or {}).get("prime_elliott_relation", {}),
                    "elliott_history": (elliott_cache or {}).get("history", {}),
                    "fast_candle_count": len(fast_candles),
                    "fast_market_provider": _active_provider,
                    "cross_market": cross_market_context or {"available": False, "assets": {}},
                    "committee_runs_today": committee_runs_current,
                    "committee_daily_limit": OPENROUTER_MAX_COMMITTEES_PER_DAY,
                    "committee_cycle": {
                        "interval_seconds": OPENROUTER_INTERVAL_SECONDS,
                        "interval_minutes": round(OPENROUTER_INTERVAL_SECONDS / 60.0, 1),
                        "runs_today": committee_runs_current,
                        "daily_limit": OPENROUTER_MAX_COMMITTEES_PER_DAY,
                        "remaining_today": max(0, OPENROUTER_MAX_COMMITTEES_PER_DAY - committee_runs_current),
                        "fast_entry_gate_mode": "ENFORCED" if FAST_ENTRY_ENFORCE_GATE else "ADVISORY",
                        "execution_rule": "Dos respuestas LLM válidas y dirección final LONG/SHORT abren o mantienen/revierten; el filtro TP-first informa, pero no bloquea por defecto.",
                    },
                    "portfolio_event": portfolio_event or (ai_result.get("position_event") if isinstance(ai_result, dict) else None),
                    "backtest": backtest_summary,
                    "candles": chart,
                    "signals": recent_signals(),
                    "source": f"{(_active_provider or 'Market').title()} Spot REST · datos públicos",
                }
                with _state_lock:
                    _state.update(payload)
            else:
                with _state_lock:
                    _state.update({
                        "ready": False, "connection": connection, "error": error or "Esperando datos de mercado…",
                        "symbol": SYMBOL, "interval": INTERVAL,
                    })
        except Exception as exc:
            log.exception("Error en ciclo del motor: %s", exc)
            with _state_lock:
                _state["connection"] = "degraded"
                _state["error"] = str(exc)
        time.sleep(POLL_SECONDS)


def start_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        init_db()
        _worker_started = True
        thread = threading.Thread(target=_background_worker, name="prima15-market", daemon=True)
        thread.start()


@app.before_request
def ensure_worker() -> None:
    start_worker()


@app.route("/")
def index():
    return render_template("index.html", symbol=SYMBOL)


@app.route("/api/state")
def api_state():
    with _state_lock:
        payload = dict(_state)
    return jsonify(payload)


@app.route("/api/health")
def health():
    with _state_lock:
        payload = {
            "ok": bool(_state.get("ready")),
            "version": APP_VERSION,
            "connection": _state.get("connection"),
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "database": DB_PATH,
            "openrouter_status": (_state.get("ai") or {}).get("status", "unknown"),
            "llm_quorum_required_for_simulated_positions": True,
            "simulated_wallet": True,
            "simulated_open_fee_pct": SIM_OPEN_FEE_PCT,
            "adaptive_learning": True,
            "elliott_wave_analysis": True,
            "elliott_history_candles_15m": HISTORY_CANDLES,
            "adaptive_take_profit_range_pct": [0.20, 0.30],
        }
    return jsonify(payload)


@app.route("/api/portfolio")
def api_portfolio():
    try:
        price = float((_state.get("price") or 0))
    except (TypeError, ValueError):
        price = 0.0
    adaptive = calculate_adaptive_learning()
    with db_connect() as conn:
        payload = portfolio_snapshot(
            conn, price,
            take_profit_target_pct=float(adaptive.get("take_profit_target_pct", SIM_TAKE_PROFIT_PCT)),
            opening_fee_rate_pct=SIM_OPEN_FEE_PCT,
            position_size_pct=SIM_POSITION_SIZE_PCT,
        )
    return jsonify(payload)


@app.route("/api/learning")
def api_learning():
    return jsonify({
        "technical": calculate_learning(),
        "adaptive": calculate_adaptive_learning(),
    })


@app.route("/api/history")
def api_history():
    try:
        limit = max(20, min(int(request.args.get("limit", 300)), HISTORY_CANDLES))
    except ValueError:
        limit = 300
    return jsonify({"symbol": SYMBOL, "interval": INTERVAL, "candles": chart_candles(limit)})


if __name__ == "__main__":
    start_worker()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False, threaded=True)
