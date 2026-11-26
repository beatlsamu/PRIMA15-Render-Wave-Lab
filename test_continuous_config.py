"""Regression tests for the fast repeated-committee defaults and .env migration."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest


class ContinuousConfigTests(unittest.TestCase):
    def _read_config(self, interval: str | None) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("OPENROUTER_INTERVAL_SECONDS", None)
        env.pop("FAST_ENTRY_ENFORCE_GATE", None)
        if interval is not None:
            env["OPENROUTER_INTERVAL_SECONDS"] = interval
        script = (
            "import config, json; "
            "print(json.dumps({'interval': config.OPENROUTER_INTERVAL_SECONDS, "
            "'fast_gate': config.FAST_ENTRY_ENFORCE_GATE}))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script], cwd=os.path.dirname(__file__),
            env=env, text=True, capture_output=True, check=True,
        )
        import json
        return json.loads(completed.stdout.strip())

    def test_default_committee_interval_is_five_minutes(self):
        values = self._read_config(None)
        self.assertEqual(values["interval"], 300)
        self.assertFalse(values["fast_gate"])

    def test_legacy_hourly_env_is_migrated_to_five_minutes(self):
        self.assertEqual(self._read_config("3600")["interval"], 300)
        self.assertEqual(self._read_config("7200")["interval"], 300)

    def test_custom_interval_is_preserved_but_never_less_than_five_minutes(self):
        self.assertEqual(self._read_config("600")["interval"], 600)
        self.assertEqual(self._read_config("120")["interval"], 300)


if __name__ == "__main__":
    unittest.main()
