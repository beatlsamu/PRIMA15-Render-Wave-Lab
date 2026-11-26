import json
import time
import unittest

from ai_analyzer import (
    aggregate_votes, api_key_present, apply_ai_gate, parse_model_content,
    evaluate_snapshot,
)


def valid_evidence_assessments():
    from ai_analyzer import EVIDENCE_OPTIONS
    return {
        section: {"reading": options[0], "basis": f"Evidencia sintética de prueba para {section}: campos presentes en el snapshot."}
        for section, options in EVIDENCE_OPTIONS.items()
    }


def valid_model_json(decision="WAIT", confidence=65, summary="Prueba", risk="Riesgo de prueba"):
    return json.dumps({
        "decision": decision,
        "confidence": confidence,
        "summary": summary,
        "risk": risk,
        "evidence_assessments": valid_evidence_assessments(),
    }, ensure_ascii=False)


class TestOpenRouterParsing(unittest.TestCase):
    def test_example_placeholder_is_not_treated_as_a_real_key(self):
        import os
        original = os.environ.get("OPENROUTER_API_KEY")
        try:
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-PEGA_TU_CLAVE_AQUI"
            self.assertFalse(api_key_present())
        finally:
            if original is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = original

    def test_parse_json(self):
        result = parse_model_content('```json\n{"decision":"long","confidence":73,"summary":"Piso posible","risk":"Esperar cierre"}\n```')
        self.assertEqual(result["decision"], "LONG")
        self.assertEqual(result["confidence"], 73.0)

    def test_bad_decision_becomes_wait(self):
        result = parse_model_content('{"decision":"BUY NOW","confidence":120}')
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(result["confidence"], 100.0)

    def test_majority_vote(self):
        result = aggregate_votes([
            {"decision": "LONG", "confidence": 80, "summary": "A", "risk": "R"},
            {"decision": "LONG", "confidence": 70, "summary": "B", "risk": "S"},
            {"decision": "WAIT", "confidence": 50, "summary": "C", "risk": "T"},
        ], min_confidence=62)
        self.assertEqual(result["decision"], "LONG")
        self.assertEqual(result["consensus_pct"], 66.7)

    def test_tie_returns_wait(self):
        result = aggregate_votes([
            {"decision": "LONG", "confidence": 90},
            {"decision": "SHORT", "confidence": 90},
        ])
        self.assertEqual(result["decision"], "WAIT")

    def test_wait_vote_prevents_minority_direction(self):
        result = aggregate_votes([
            {"decision": "LONG", "confidence": 90},
            {"decision": "WAIT", "confidence": 90},
            {"decision": "SHORT", "confidence": 90},
        ])
        self.assertEqual(result["decision"], "WAIT")


class TestAIGate(unittest.TestCase):
    def setUp(self):
        self.analysis = {
            "signal": "LONG", "candidate": "LONG", "phase": "GIRO ALCISTA DETECTADO",
            "current_price": 100.0, "atr_pct": 1.0, "reasons": [],
        }

    def test_agreement_confirms(self):
        ai = {"status": "ok", "decision": "LONG", "confidence": 75,
              "updated_at": int(time.time() * 1000), "price": 100.0}
        result = apply_ai_gate(self.analysis, ai, min_confidence=62, max_age_seconds=1800)
        self.assertEqual(result["signal"], "LONG")
        self.assertIn("OPENROUTER CONFIRMA", result["phase"])

    def test_disagreement_blocks(self):
        ai = {"status": "ok", "decision": "SHORT", "confidence": 80,
              "updated_at": int(time.time() * 1000), "price": 100.0}
        result = apply_ai_gate(self.analysis, ai, min_confidence=62, max_age_seconds=1800)
        self.assertEqual(result["signal"], "NEUTRAL")

    def test_unconfigured_ai_blocks(self):
        result = apply_ai_gate(self.analysis, {"status": "not_configured"}, required=True)
        self.assertEqual(result["signal"], "NEUTRAL")

    def test_price_drift_blocks(self):
        ai = {"status": "ok", "decision": "LONG", "confidence": 90,
              "updated_at": int(time.time() * 1000), "price": 100.0}
        analysis = dict(self.analysis, current_price=103.0)
        result = apply_ai_gate(analysis, ai, min_confidence=62, max_age_seconds=1800)
        self.assertEqual(result["signal"], "NEUTRAL")


class TestModelFailover(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "symbol": "RENDERUSDT", "price_now": 2.5,
            "technical_engine": {"last_candle_time": 123456, "bottom_bounce_confirmed": True},
        }

    def test_empty_primary_uses_first_backup_and_keeps_quorum(self):
        from unittest.mock import patch
        backup_vote = {
            "decision": "SHORT", "confidence": 78, "summary": "Sin rebote inferior",
            "risk": "Continuar evaluando", "requested_model": "backup-a",
            "served_model": "backup-a",
        }
        second_vote = dict(backup_vote, requested_model="backup-b", served_model="backup-b")
        with patch("ai_analyzer.configured_models", return_value=["openrouter/free"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=["backup-a", "backup-b"]), \
             patch("ai_analyzer._request_model", side_effect=[ValueError("El modelo devolvió una respuesta vacía."), backup_vote, second_vote]) as request:
            result = evaluate_snapshot(self.snapshot)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["decision"], "SHORT")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["fallback_used_models"], ["backup-a", "backup-b"])
        self.assertEqual(result["votes"][0]["source"], "respaldo")
        self.assertEqual(request.call_count, 3)
        self.assertIn("respuesta vacía", result["errors"][0])

    def test_all_models_fail_and_signal_remains_blocked(self):
        from unittest.mock import patch
        with patch("ai_analyzer.configured_models", return_value=["openrouter/free"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=["backup-a", "backup-b"]), \
             patch("ai_analyzer._request_model", side_effect=RuntimeError("modelo no disponible")) as request:
            result = evaluate_snapshot(self.snapshot)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(result["valid_models"], 0)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(len(result["fallback_attempts"]), 3)

    def test_invalid_api_key_does_not_waste_calls_on_backups(self):
        from unittest.mock import patch
        with patch("ai_analyzer.configured_models", return_value=["openrouter/free"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=["backup-a", "backup-b"]), \
             patch("ai_analyzer._request_model", side_effect=RuntimeError("OpenRouter HTTP 401: Invalid API key")) as request:
            result = evaluate_snapshot(self.snapshot)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(request.call_count, 1)

    def test_fallback_replaces_missing_vote_not_extra_vote(self):
        from unittest.mock import patch
        primary_vote = {
            "decision": "LONG", "confidence": 80, "summary": "A", "risk": "R",
            "requested_model": "primary-a", "served_model": "primary-a",
        }
        backup_vote = {
            "decision": "LONG", "confidence": 75, "summary": "B", "risk": "S",
            "requested_model": "backup-a", "served_model": "backup-a",
        }
        snapshot = dict(self.snapshot, trend_persistence={"direction": "UP", "agreement_pct": 66.7})
        with patch("ai_analyzer.configured_models", return_value=["primary-a", "primary-b"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=["backup-a", "backup-b"]), \
             patch("ai_analyzer._request_model", side_effect=[primary_vote, RuntimeError("caído"), backup_vote]):
            result = evaluate_snapshot(snapshot)

        self.assertEqual(result["required_votes"], 2)
        self.assertEqual(result["valid_models"], 2)
        self.assertEqual(len(result["votes"]), 2)
        self.assertEqual(result["decision"], "LONG")


if __name__ == "__main__":
    unittest.main()


class TestOpenRouterEndpointAdapters(unittest.TestCase):
    class FakeResponse:
        def __init__(self, status_code=200, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    def setUp(self):
        import os
        self.original_key = os.environ.get("OPENROUTER_API_KEY")
        self.original_tokens = os.environ.get("OPENROUTER_MAX_TOKENS")
        os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test-key"
        os.environ["OPENROUTER_MAX_TOKENS"] = "1400"

    def tearDown(self):
        import os
        if self.original_key is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = self.original_key
        if self.original_tokens is None:
            os.environ.pop("OPENROUTER_MAX_TOKENS", None)
        else:
            os.environ["OPENROUTER_MAX_TOKENS"] = self.original_tokens

    def test_perplexity_decider_uses_decisions_api(self):
        from unittest.mock import patch
        from ai_analyzer import _request_model
        model = "perplexity/pplx-decider-flash-v1.3:free"
        from ai_analyzer import EVIDENCE_OPTIONS
        answers = {"market_direction": {
            "type": "choice", "choice": "LONG",
            "probabilities": {"LONG": 0.81, "SHORT": 0.08, "WAIT": 0.11},
        }}
        for section, options in EVIDENCE_OPTIONS.items():
            answers[f"evidence_{section}"] = {
                "type": "choice", "choice": options[0],
                "probabilities": {options[0]: 0.8, options[-1]: 0.2},
            }
        response = self.FakeResponse(payload={
            "model": model, "answers": answers,
            "usage": {"input_tokens": 100, "output_tokens": 0},
        })
        snapshot = {"price_now": 2.5, "wave_dimensions": {"temporal": {"scales": [7, 11]}},
                    "recent_15m_candles": [{"t": 1, "c": 2.5}],
                    "adaptive_learning": {"trend_agreement_min_pct": 60}}
        with patch("ai_analyzer.requests.post", return_value=response) as post:
            result = _request_model(model, snapshot)
        self.assertEqual(json.loads(post.call_args.kwargs["json"]["state"][0]), snapshot)
        self.assertEqual(result["decision"], "LONG")
        self.assertEqual(result["confidence"], 81.0)
        self.assertIn("/api/alpha/decisions", post.call_args.args[0])
        questions = post.call_args.kwargs["json"]["questions"]
        self.assertIn("market_direction", questions)
        self.assertEqual(len(questions), 1 + len(EVIDENCE_OPTIONS))
        self.assertEqual(result["evidence_coverage_pct"], 100.0)
        self.assertEqual(result["assessment_mode"], "typed_probabilities")

    def test_recursive_flint_uses_messages_api(self):
        from unittest.mock import patch
        from ai_analyzer import _request_model
        model = "recursive/flint-v1.0:free"
        response = self.FakeResponse(payload={
            "model": model,
            "content": [{"type": "text", "text": valid_model_json("SHORT", 74, "Techo probable", "Esperar confirmación")}],
            "usage": {"input_tokens": 120, "output_tokens": 40},
        })
        with patch("ai_analyzer.requests.post", return_value=response) as post:
            result = _request_model(model, {"price_now": 2.5})
        self.assertEqual(result["decision"], "SHORT")
        self.assertIn("/api/v1/messages", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["headers"]["anthropic-version"], "2023-06-01")

    def test_reasoning_budget_empty_response_has_diagnostic(self):
        from unittest.mock import patch
        from ai_analyzer import _request_model
        model = "nvidia/nemotron-3.5-lightning:free"
        response = self.FakeResponse(payload={
            "model": model,
            "choices": [{"finish_reason": "length", "message": {"content": "", "reasoning": "..."}}],
            "usage": {"completion_tokens": 1400, "completion_tokens_details": {"reasoning_tokens": 1398}},
        })
        with patch("ai_analyzer.requests.post", return_value=response) as post:
            with self.assertRaisesRegex(RuntimeError, "tokens se consumieron en razonamiento"):
                _request_model(model, {"price_now": 2.5})
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], 1400)

    def test_chat_model_extracts_json_from_text_blocks(self):
        from unittest.mock import patch
        from ai_analyzer import _request_model
        model = "baidu/ernie-5.5-flash-preview:free"
        response = self.FakeResponse(payload={
            "model": model,
            "choices": [{"finish_reason": "stop", "message": {
                "content": [{"type": "text", "text": valid_model_json("WAIT", 55, "Sin giro", "Rango lateral")}]
            }}],
            "usage": {},
        })
        snapshot = {
            "price_now": 2.5,
            "recent_15m_candles": [{"t": 1, "o": 2.4, "h": 2.6, "l": 2.3, "c": 2.5}],
            "wave_dimensions": {"temporal": {"scales": [{"prime": 7}]}, "amplitude": {"legs": []}, "behavior": {"analogs": {}}},
            "adaptive_learning": {"trend_agreement_min_pct": 60},
            "context_manifest": {"schema_version": "prima15.llm-context.v2"},
        }
        with patch("ai_analyzer.requests.post", return_value=response) as post:
            result = _request_model(model, snapshot)
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(result["assessment_mode"], "textual_basis")
        sent = json.loads(post.call_args.kwargs["json"]["messages"][-1]["content"])
        self.assertEqual(sent, snapshot)
        self.assertEqual(result["evidence_coverage_pct"], 100.0)


    def test_nvidia_chat_caps_reasoning_budget(self):
        from unittest.mock import patch
        from ai_analyzer import _request_model
        model = "nvidia/nemotron-3.5-lightning:free"
        response = self.FakeResponse(payload={
            "model": model,
            "choices": [{"finish_reason": "stop", "message": {
                "content": valid_model_json("LONG", 70, "Giro", "Volatilidad")
            }}],
            "usage": {},
        })
        with patch("ai_analyzer.requests.post", return_value=response) as post:
            result = _request_model(model, {"price_now": 2.5})
        self.assertEqual(result["decision"], "LONG")
        self.assertEqual(post.call_args.kwargs["json"]["reasoning"]["max_tokens"], 600)


class TestModelListConfiguration(unittest.TestCase):
    def test_snapshot_sends_every_engine_dimension_and_auditable_context(self):
        from ai_analyzer import build_snapshot
        from engine import analyze
        from test_engine import synthetic_candles
        candles = synthetic_candles(400)
        engine_window = candles[-350:]
        analysis = analyze(engine_window, engine_window[-1]["close"])
        portfolio = {"wallet_balance_pct": 99.9, "history": [{"id": i} for i in range(25)]}
        snapshot = build_snapshot(
            candles, candles[-1]["close"], analysis, "RENDERUSDT", "15m",
            portfolio=portfolio, learning_summary={"samples": 42},
            backtest_summary={"samples": 12}, recent_backtest=[{"id": i} for i in range(25)],
            recent_signals=[{"id": i} for i in range(25)],
            prior_ai_memory=[{"id": i} for i in range(20)],
            market_24h={"priceChangePercent": 1.2}, market_provider="binance",
        )
        self.assertTrue({"temporal", "amplitude", "behavior"}.issubset(snapshot["wave_dimensions"]))
        self.assertTrue(snapshot["technical_engine"]["wick_diagnostics"]["scale_readings"])
        self.assertEqual(len(snapshot["recent_15m_candles"]), 350)
        self.assertEqual(snapshot["recent_15m_candles"][0]["t"], engine_window[0]["open_time"])
        self.assertEqual(snapshot["recent_15m_candles"][-1]["t"], engine_window[-1]["open_time"])
        self.assertEqual(len(snapshot["wave_dimensions"]["temporal"]["scales"]), len(analysis["wave_dimensions"]["temporal"]["scales"]))
        self.assertEqual(len(snapshot["technical_engine"]["wick_diagnostics"]["scale_readings"]), len(analysis["wick_analysis"]["scale_readings"]))
        self.assertEqual(len(snapshot["wave_dimensions"]["amplitude"]["legs"]), len(analysis["wave_dimensions"]["amplitude"]["legs"]))
        self.assertEqual(snapshot["technical_engine"]["pivots"], analysis["pivots"])
        self.assertIn("long_reasons", snapshot["technical_engine"])
        self.assertIn("short_reasons", snapshot["technical_engine"])
        self.assertEqual(len(snapshot["simulation_wallet"]["history"]), 20)
        self.assertEqual(len(snapshot["backtest"]["recent_examples"]), 20)
        self.assertEqual(len(snapshot["recent_technical_signals"]), 20)
        self.assertEqual(len(snapshot["prior_ai_memory"]), 12)
        self.assertEqual(snapshot["context_manifest"]["required_evidence_count"], 20)
        self.assertIn("elliott", snapshot["context_manifest"]["required_evidence_families"])
        self.assertIn("elliott_prime", snapshot["context_manifest"]["required_evidence_families"])
        self.assertEqual(snapshot["context_manifest"]["auxiliary_row_limits"]["backtest_examples"], 20)
        self.assertEqual(snapshot["context_manifest"]["auxiliary_row_limits"]["recent_signals"], 20)
        self.assertEqual(snapshot["context_manifest"]["auxiliary_row_limits"]["prior_predictions_with_outcomes"], 12)
        self.assertEqual(snapshot["context_manifest"]["raw_candles_sent"], 350)
        self.assertIn("adaptive_learning", snapshot["context_manifest"]["included_sections"])
        self.assertIn("NO bloquea", snapshot["decision_policy"]["fast_entry_requirement"])
        self.assertIn("produce WAIT", snapshot["decision_policy"]["valid_uncertainty_default"])

    def test_snapshot_sends_elliott_multidegree_and_prime_relation_to_models(self):
        from ai_analyzer import build_snapshot, EVIDENCE_OPTIONS
        from engine import analyze
        from test_engine import synthetic_candles
        candles = synthetic_candles(420)
        result = analyze(candles[-350:], candles[-1]["close"])
        elliott = {
            "version": "elliott-1.1",
            "history": {"15m_candles_processed": 5000, "15m_days": 52.1},
            "timeframes": {
                name: {"candle_count": n, "history_days": days, "pivot_count_total": 12,
                       "count": {"pattern": "STANDARD_IMPULSE_5_WAVES", "direction": "UP", "valid": True,
                                 "prime_time_overlay": {"fit_pct": 80.0}}}
                for name, n, days in [("1m", 3000, 2.1), ("15m", 5000, 52.1), ("1h", 1250, 52.1), ("4h", 312, 52.0)]
            },
            "multi_degree_synthesis": {"direction": "UP"},
            "prime_elliott_relation": {"state": "ELLIOTT_IMPULSE_AND_PRIME_CONFLUENCE"},
        }
        result["elliott_wave"] = elliott
        result["elliott_prime_synthesis"] = elliott["prime_elliott_relation"]
        snapshot = build_snapshot(candles, candles[-1]["close"], result, "RENDERUSDT", "15m")
        self.assertEqual(snapshot["elliott_wave"]["history"]["15m_candles_processed"], 5000)
        self.assertEqual(set(snapshot["elliott_wave"]["timeframes"]), {"1m", "15m", "1h", "4h"})
        self.assertEqual(snapshot["prime_elliott_synthesis"]["state"], "ELLIOTT_IMPULSE_AND_PRIME_CONFLUENCE")
        self.assertEqual(snapshot["context_manifest"]["elliott_15m_history_processed"], 5000)
        self.assertTrue(snapshot["context_manifest"]["elliott_prime_relation_sent"])
        self.assertIn("elliott", EVIDENCE_OPTIONS)
        self.assertIn("elliott_prime", EVIDENCE_OPTIONS)
        self.assertIn("elliott_wave", snapshot["context_manifest"]["included_sections"])

    def test_snapshot_sends_btc_eth_context_as_an_evidence_family(self):
        from ai_analyzer import build_snapshot, EVIDENCE_OPTIONS
        from engine import analyze
        from test_engine import synthetic_candles
        candles = synthetic_candles(200)
        result = analyze(candles[-150:], candles[-1]["close"])
        cross = {"available": True, "assets": {
            "BTCUSDT": {"available": True, "roc_5m_pct": 0.12, "age_seconds": 4},
            "ETHUSDT": {"available": True, "roc_5m_pct": 0.08, "age_seconds": 5},
        }}
        snapshot = build_snapshot(
            candles, candles[-1]["close"], result, "RENDERUSDT", "15m",
            fast_entry_context={"status": "READY", "cross_market": cross, "opportunities": {}},
        )
        self.assertEqual(snapshot["cross_market"]["assets"]["BTCUSDT"]["roc_5m_pct"], 0.12)
        self.assertIn("cross_market", EVIDENCE_OPTIONS)
        self.assertIn("cross_market", snapshot["context_manifest"]["included_sections"])

    def test_fast_entry_is_advisory_and_does_not_block_consensus_by_default(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        votes = [
            {"decision": "WAIT", "confidence": 70, "summary": "Sin rechazo", "risk": "R", "served_model": "perplexity", "requested_model": "perplexity"},
            {"decision": "SHORT", "confidence": 72, "summary": "Tendencia bajista", "risk": "R", "served_model": "nemotron", "requested_model": "nemotron"},
        ]
        snapshot = {
            "price_now": 2.5, "technical_engine": {},
            "trend_persistence": {"direction": "DOWN", "agreement_pct": 100},
            "fast_entry": {"status": "READY", "opportunities": {"SHORT": {"eligible": False, "blockers": ["TP-first insuficiente"]}}},
        }
        with patch("ai_analyzer.configured_models", return_value=["perplexity", "nemotron"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=[]), \
             patch("ai_analyzer._request_model", side_effect=votes):
            result = evaluate_snapshot(snapshot)
        self.assertEqual(result["pre_fast_gate_decision"], "SHORT")
        self.assertEqual(result["decision"], "SHORT")
        self.assertFalse(result["fast_entry_gate_passed"])
        self.assertEqual(result["fast_entry_gate_mode"], "ADVISORY")
        self.assertTrue(result["fast_entry_warnings"])
        from cycle_policy import has_actionable_consensus
        self.assertTrue(has_actionable_consensus(result))

    def test_fast_entry_quality_gate_can_still_be_enforced_explicitly(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        votes = [
            {"decision": "WAIT", "confidence": 70, "summary": "Sin rechazo", "risk": "R", "served_model": "perplexity", "requested_model": "perplexity"},
            {"decision": "SHORT", "confidence": 72, "summary": "Tendencia bajista", "risk": "R", "served_model": "nemotron", "requested_model": "nemotron"},
        ]
        snapshot = {
            "price_now": 2.5, "technical_engine": {},
            "trend_persistence": {"direction": "DOWN", "agreement_pct": 100},
            "fast_entry": {"status": "READY", "opportunities": {"SHORT": {"eligible": False, "blockers": ["TP-first insuficiente"]}}},
        }
        with patch("ai_analyzer.FAST_ENTRY_ENFORCE_GATE", True), \
             patch("ai_analyzer.configured_models", return_value=["perplexity", "nemotron"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=[]), \
             patch("ai_analyzer._request_model", side_effect=votes):
            result = evaluate_snapshot(snapshot)
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(result["fast_entry_gate_mode"], "ENFORCED")

    def test_fast_entry_quality_gate_allows_model_consensus_with_tp_edge(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        votes = [
            {"decision": "WAIT", "confidence": 70, "summary": "Sin rechazo", "risk": "R", "served_model": "perplexity", "requested_model": "perplexity"},
            {"decision": "SHORT", "confidence": 72, "summary": "Tendencia bajista", "risk": "R", "served_model": "nemotron", "requested_model": "nemotron"},
        ]
        snapshot = {
            "price_now": 2.5, "technical_engine": {},
            "trend_persistence": {"direction": "DOWN", "agreement_pct": 100},
            "fast_entry": {"status": "READY", "opportunities": {"SHORT": {"eligible": True, "historical": {"tp_first_rate_pct": 68, "wilson_lower_80_pct": 55}, "live_confirmation_pct": 67}}},
        }
        with patch("ai_analyzer.configured_models", return_value=["perplexity", "nemotron"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=[]), \
             patch("ai_analyzer._request_model", side_effect=votes):
            result = evaluate_snapshot(snapshot)
        self.assertEqual(result["decision"], "SHORT")
        self.assertTrue(result["fast_entry_gate_passed"])

    def test_missing_evidence_assessment_invalidates_a_model_vote(self):
        from ai_analyzer import _validate_evidence_coverage
        from ai_analyzer import EVIDENCE_OPTIONS
        incomplete = {key: {"reading": options[0], "basis": "observado"} for key, options in EVIDENCE_OPTIONS.items()}
        incomplete.pop("adaptive_profile")
        with self.assertRaisesRegex(RuntimeError, "adaptive_profile"):
            _validate_evidence_coverage(incomplete, "test-model")

    def test_each_vote_is_tied_to_same_audited_snapshot_hash(self):
        import hashlib
        import json
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        evidence = valid_evidence_assessments()
        votes = [
            {"decision": "WAIT", "confidence": 70, "summary": "Sin excepción", "risk": "R",
             "served_model": "perplexity", "requested_model": "perplexity", "evidence_assessments": evidence},
            {"decision": "LONG", "confidence": 76, "summary": "Continuidad alcista", "risk": "R",
             "served_model": "nemotron", "requested_model": "nemotron", "evidence_assessments": evidence},
        ]
        snapshot = {
            "symbol": "RENDERUSDT", "price_now": 2.5,
            "technical_engine": {"last_candle_time": 123},
            "trend_persistence": {"direction": "UP", "agreement_pct": 66.7},
            "recent_15m_candles": [{"t": 123, "o": 2.4, "h": 2.6, "l": 2.3, "c": 2.5}],
            "context_manifest": {"schema_version": "prima15.llm-context.v2", "raw_candles_sent": 1},
        }
        expected = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        with patch("ai_analyzer.configured_models", return_value=["perplexity", "nemotron"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=[]), \
             patch("ai_analyzer._request_model", side_effect=votes) as request:
            result = evaluate_snapshot(snapshot)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["context_audit"]["payload_sha256"], expected)
        self.assertTrue(all(v["context_sha256"] == expected for v in result["votes"]))
        self.assertTrue(result["context_audit"]["same_snapshot_sent_to_each_model"])

    def test_legacy_default_routes_to_user_curated_models(self):
        import os
        from unittest.mock import patch
        from ai_analyzer import configured_models, configured_fallback_models
        with patch.dict(os.environ, {
            "OPENROUTER_MODELS": "openrouter/free",
            "OPENROUTER_FALLBACK_MODELS": "nvidia/nemotron-3-ultra-550b-a55b:free,baidu/ernie-5.5-flash-preview:free,inclusionai/ling-3.2-flash:free",
        }, clear=False):
            primary = configured_models()
            backups = configured_fallback_models()
        self.assertEqual(primary[0], "perplexity/pplx-decider-flash-v1.3:free")
        self.assertIn("recursive/flint-v1.0:free", backups)
        self.assertNotIn("inclusionai/ling-3.2-flash:free", backups)

    def test_http_429_skips_to_next_backup(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        backup_vote = {
            "decision": "WAIT", "confidence": 65, "summary": "Esperar",
            "risk": "No hay confirmación", "requested_model": "backup-b",
            "served_model": "backup-b",
        }
        with patch("ai_analyzer.configured_models", return_value=["primary-a"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=["backup-a", "backup-b"]), \
             patch("ai_analyzer._request_model", side_effect=[
                 RuntimeError("modelo principal no disponible"),
                 RuntimeError("OpenRouter HTTP 429: Provider returned error"),
                 backup_vote,
             ]) as request:
            result = evaluate_snapshot({"price_now": 2.5, "technical_engine": {}})
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(result["valid_models"], 1)
        self.assertEqual(result["fallback_used_models"], ["backup-b"])
        self.assertEqual(request.call_count, 3)

    def test_primary_roles_and_backup_keep_their_semantic_slot(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        votes = [
            {"decision": "WAIT", "confidence": 70, "summary": "Sin rechazo claro", "risk": "R", "served_model": "nemotron", "requested_model": "nemotron"},
            {"decision": "SHORT", "confidence": 75, "summary": "Continuidad bajista", "risk": "R", "served_model": "backup", "requested_model": "backup"},
        ]
        with patch("ai_analyzer.configured_models", return_value=["perplexity", "nemotron"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=["backup"]), \
             patch("ai_analyzer._request_model", side_effect=[RuntimeError("fallo Perplexity"), votes[0], votes[1]]) as request:
            result = evaluate_snapshot({"price_now": 2.5, "technical_engine": {"bottom_bounce_confirmed": False}})
        calls = request.call_args_list
        self.assertEqual(calls[0].args[2], "exception")
        self.assertEqual(calls[1].args[2], "continuation")
        self.assertEqual(calls[2].args[2], "exception")
        self.assertEqual(result["valid_models"], 2)
        self.assertEqual(result["decision"], "SHORT")

    def test_two_primary_votes_decide_without_spending_a_third_request(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        votes = [
            {"decision": "LONG", "confidence": 80, "summary": "A", "risk": "R", "served_model": "a", "requested_model": "a"},
            {"decision": "LONG", "confidence": 78, "summary": "B", "risk": "R", "served_model": "b", "requested_model": "b"},
            {"decision": "SHORT", "confidence": 75, "summary": "C", "risk": "R", "served_model": "backup-c", "requested_model": "backup-c"},
        ]
        snapshot = {"price_now": 2.5, "technical_engine": {},
                    "trend_persistence": {"direction": "UP", "agreement_pct": 66.7}}
        with patch("ai_analyzer.configured_models", return_value=["a", "b", "c"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=["backup-c"]), \
             patch("ai_analyzer._request_model", side_effect=[votes[0], votes[1], RuntimeError("HTTP 429"), votes[2]]) as request:
            result = evaluate_snapshot(snapshot)
        self.assertEqual(result["required_votes"], 2)
        self.assertEqual(result["valid_models"], 2)
        self.assertFalse(result["fallback_used"])
        self.assertEqual(result["decision"], "LONG")
        self.assertEqual(request.call_count, 2)

    def test_continuity_uses_nemotron_role_when_no_rejection_exists(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        votes = [
            {"decision": "SHORT", "confidence": 78, "summary": "Perplexity no ve un piso", "risk": "R", "served_model": "perplexity", "requested_model": "perplexity"},
            {"decision": "LONG", "confidence": 72, "summary": "Nemotron detecta continuidad alcista", "risk": "R", "served_model": "nemotron", "requested_model": "nemotron"},
        ]
        snapshot = {
            "price_now": 2.5, "technical_engine": {},
            "trend_persistence": {"direction": "UP", "agreement_pct": 66.7},
        }
        with patch("ai_analyzer.configured_models", return_value=["perplexity", "nemotron"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=[]), \
             patch("ai_analyzer._request_model", side_effect=votes):
            result = evaluate_snapshot(snapshot)
        self.assertEqual(result["decision"], "LONG")
        self.assertEqual(result["decision_basis"], "TREND_CONTINUITY_NEMOTRON")
        self.assertEqual(result["exception_vote"], "SHORT")
        self.assertEqual(result["continuation_vote"], "LONG")

    def test_lower_rejection_gives_priority_to_first_llm(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        votes = [
            {"decision": "LONG", "confidence": 76, "summary": "Perplexity detecta rechazo inferior", "risk": "R", "served_model": "perplexity", "requested_model": "perplexity"},
            {"decision": "SHORT", "confidence": 85, "summary": "Nemotron aún ve presión bajista", "risk": "R", "served_model": "nemotron", "requested_model": "nemotron"},
        ]
        snapshot = {
            "price_now": 2.5,
            "technical_engine": {"wick_diagnostics": {"lower": {"rejection_confirmed": True}, "upper": {"rejection_confirmed": False}}},
            "trend_persistence": {"direction": "DOWN", "agreement_pct": 100.0},
        }
        with patch("ai_analyzer.configured_models", return_value=["perplexity", "nemotron"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=[]), \
             patch("ai_analyzer._request_model", side_effect=votes):
            result = evaluate_snapshot(snapshot)
        self.assertEqual(result["decision"], "LONG")
        self.assertEqual(result["decision_basis"], "EXCEPTION_LLM")
        self.assertTrue(result["lower_rejection_confirmed"])

    def test_upper_rejection_gives_priority_to_first_llm(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        votes = [
            {"decision": "SHORT", "confidence": 74, "summary": "Perplexity detecta rechazo superior", "risk": "R", "served_model": "perplexity", "requested_model": "perplexity"},
            {"decision": "LONG", "confidence": 82, "summary": "Nemotron ve continuidad alcista", "risk": "R", "served_model": "nemotron", "requested_model": "nemotron"},
        ]
        snapshot = {
            "price_now": 2.5,
            "technical_engine": {"wick_diagnostics": {"lower": {"rejection_confirmed": False}, "upper": {"rejection_confirmed": True}}},
            "trend_persistence": {"direction": "UP", "agreement_pct": 100.0},
        }
        with patch("ai_analyzer.configured_models", return_value=["perplexity", "nemotron"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=[]), \
             patch("ai_analyzer._request_model", side_effect=votes):
            result = evaluate_snapshot(snapshot)
        self.assertEqual(result["decision"], "SHORT")
        self.assertEqual(result["decision_basis"], "EXCEPTION_LLM")

    def test_trend_agreement_must_be_strictly_above_60_percent(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        votes = [
            {"decision": "WAIT", "confidence": 70, "summary": "Sin anomalía", "risk": "R", "served_model": "perplexity", "requested_model": "perplexity"},
            {"decision": "LONG", "confidence": 80, "summary": "Continuidad alcista", "risk": "R", "served_model": "nemotron", "requested_model": "nemotron"},
        ]
        snapshot = {"price_now": 2.5, "technical_engine": {},
                    "trend_persistence": {"direction": "UP", "agreement_pct": 60.0}}
        with patch("ai_analyzer.configured_models", return_value=["perplexity", "nemotron"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=[]), \
             patch("ai_analyzer._request_model", side_effect=votes):
            result = evaluate_snapshot(snapshot)
        self.assertEqual(result["decision"], "SHORT")
        self.assertEqual(result["decision_basis"], "TREND_UNCONFIRMED_DEFAULT_SHORT")


    def test_adaptive_trend_threshold_can_tighten_continuity_requirement(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        votes = [
            {"decision": "WAIT", "confidence": 70, "summary": "Sin anomalía", "risk": "R", "served_model": "perplexity", "requested_model": "perplexity"},
            {"decision": "LONG", "confidence": 80, "summary": "Continuidad alcista", "risk": "R", "served_model": "nemotron", "requested_model": "nemotron"},
        ]
        snapshot = {
            "price_now": 2.5,
            "technical_engine": {},
            "trend_persistence": {"direction": "UP", "agreement_pct": 66.7},
            "adaptive_learning": {"trend_agreement_min_pct": 68.0},
        }
        with patch("ai_analyzer.configured_models", return_value=["perplexity", "nemotron"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=[]), \
             patch("ai_analyzer._request_model", side_effect=votes):
            result = evaluate_snapshot(snapshot)
        self.assertEqual(result["decision"], "SHORT")
        self.assertEqual(result["required_trend_agreement_pct"], 68.0)
        self.assertEqual(result["decision_basis"], "TREND_UNCONFIRMED_DEFAULT_SHORT")

    def test_split_committee_resolves_to_short(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        votes = [
            {"decision": "LONG", "confidence": 82, "summary": "A", "risk": "R", "served_model": "a", "requested_model": "a"},
            {"decision": "SHORT", "confidence": 77, "summary": "B", "risk": "R", "served_model": "b", "requested_model": "b"},
        ]
        with patch("ai_analyzer.configured_models", return_value=["a", "b"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=[]), \
             patch("ai_analyzer._request_model", side_effect=votes):
            result = evaluate_snapshot({"price_now": 2.5, "technical_engine": {"bottom_bounce_confirmed": True}})
        self.assertEqual(result["committee_decision"], "WAIT")
        self.assertEqual(result["decision"], "SHORT")
        self.assertEqual(result["consensus_pct"], 50.0)

    def test_two_consecutive_429s_break_the_fallback_chain(self):
        from unittest.mock import patch
        from ai_analyzer import evaluate_snapshot
        with patch("ai_analyzer.configured_models", return_value=["primary"]), \
             patch("ai_analyzer.configured_fallback_models", return_value=["backup-a", "backup-b", "backup-c"]), \
             patch("ai_analyzer._request_model", side_effect=[
                 RuntimeError("modelo principal no disponible"),
                 RuntimeError("OpenRouter HTTP 429: rate limit"),
                 RuntimeError("OpenRouter HTTP 429: rate limit"),
             ]) as request:
            result = evaluate_snapshot({"price_now": 2.5, "technical_engine": {}})
        self.assertEqual(result["decision"], "WAIT")
        self.assertEqual(request.call_count, 3)
        self.assertIn("dos respuestas HTTP 429 consecutivas", result["risk"])
