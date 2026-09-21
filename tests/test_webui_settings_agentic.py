"""Offline tests for webui settings agentic knobs."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webui"))

SETTINGS_PATH = ROOT / "webui" / "routes" / "settings.py"
_spec = importlib.util.spec_from_file_location("webui_settings_agentic", SETTINGS_PATH)
settings_mod = importlib.util.module_from_spec(_spec)


def setUpModule():
    _spec.loader.exec_module(settings_mod)


class TestAgenticSettings(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.env_path = Path(self._tmpdir.name) / ".env"
        self._orig_env_path = settings_mod._env_path
        settings_mod._env_path = lambda: self.env_path
        self._saved = {
            k: os.environ.pop(k)
            for k in (
                "GESAKU_RETRIEVAL_MODE",
                "GESAKU_JUDGE_TOOL_BUDGET",
                "GESAKU_REQUIRE_TOOLS",
                "ANTHROPIC_BASE_URL",
                "ANTHROPIC_API_KEY",
                "GESAKU_WRITER_MODEL",
                "GESAKU_JUDGE_MODEL",
                "GESAKU_REVIEW_MODEL",
                "GESAKU_GENRE",
                "GESAKU_CHAPTERS",
            )
            if k in os.environ
        }

    def tearDown(self):
        settings_mod._env_path = self._orig_env_path
        self._tmpdir.cleanup()
        # Restore only keys that were originally present — do not destroy
        # pre-existing env after update().
        for k in (
            "GESAKU_RETRIEVAL_MODE",
            "GESAKU_JUDGE_TOOL_BUDGET",
            "GESAKU_REQUIRE_TOOLS",
        ):
            os.environ.pop(k, None)
        for k, v in self._saved.items():
            os.environ[k] = v

    def test_get_defaults(self):
        payload = settings_mod.settings()
        self.assertEqual(payload["agentic"]["retrievalMode"], "dump")
        self.assertEqual(payload["agentic"]["judgeToolBudget"], 12)
        self.assertFalse(payload["agentic"]["requireTools"])

    def test_get_reads_env(self):
        os.environ["GESAKU_RETRIEVAL_MODE"] = "scoped"
        os.environ["GESAKU_JUDGE_TOOL_BUDGET"] = "8"
        os.environ["GESAKU_REQUIRE_TOOLS"] = "1"
        payload = settings_mod.settings()
        self.assertEqual(payload["agentic"]["retrievalMode"], "scoped")
        self.assertEqual(payload["agentic"]["judgeToolBudget"], 8)
        self.assertTrue(payload["agentic"]["requireTools"])

    def test_post_writes_env_file(self):
        body = settings_mod.SettingsPayload(
            agentic={"retrievalMode": "scoped", "judgeToolBudget": 6, "requireTools": True}
        )
        settings_mod.settings_commit(body)
        text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("GESAKU_RETRIEVAL_MODE=scoped", text)
        self.assertIn("GESAKU_JUDGE_TOOL_BUDGET=6", text)
        self.assertIn("GESAKU_REQUIRE_TOOLS=1", text)
        self.assertEqual(os.environ.get("GESAKU_RETRIEVAL_MODE"), "scoped")

    def test_post_rejects_bad_mode(self):
        from fastapi import HTTPException
        body = settings_mod.SettingsPayload(agentic={"retrievalMode": "openagent"})
        with self.assertRaises(HTTPException):
            settings_mod.settings_commit(body)

    def test_post_rejects_bad_budget(self):
        from fastapi import HTTPException
        body = settings_mod.SettingsPayload(agentic={"judgeToolBudget": 9999})
        with self.assertRaises(HTTPException):
            settings_mod.settings_commit(body)


    def test_post_rejects_nonnumeric_budget(self):
        from fastapi import HTTPException
        body = settings_mod.SettingsPayload(agentic={"judgeToolBudget": "12.5"})
        with self.assertRaises(HTTPException) as ctx:
            settings_mod.settings_commit(body)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_post_budget_boundaries(self):
        settings_mod.settings_commit(
            settings_mod.SettingsPayload(agentic={"judgeToolBudget": 0})
        )
        self.assertEqual(os.environ.get("GESAKU_JUDGE_TOOL_BUDGET"), "0")
        settings_mod.settings_commit(
            settings_mod.SettingsPayload(agentic={"judgeToolBudget": 200})
        )
        self.assertEqual(os.environ.get("GESAKU_JUDGE_TOOL_BUDGET"), "200")
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            settings_mod.settings_commit(
                settings_mod.SettingsPayload(agentic={"judgeToolBudget": -1})
            )

    def test_post_require_tools_false_writes_zero(self):
        settings_mod.settings_commit(
            settings_mod.SettingsPayload(agentic={"requireTools": False})
        )
        self.assertEqual(os.environ.get("GESAKU_REQUIRE_TOOLS"), "0")

    def test_get_clamps_out_of_range_budget_from_env(self):
        os.environ["GESAKU_JUDGE_TOOL_BUDGET"] = "9999"
        payload = settings_mod.settings()
        self.assertEqual(payload["agentic"]["judgeToolBudget"], 200)
        os.environ["GESAKU_JUDGE_TOOL_BUDGET"] = "-5"
        payload = settings_mod.settings()
        self.assertEqual(payload["agentic"]["judgeToolBudget"], 0)

    def test_get_sanitizes_invalid_mode_from_env(self):
        os.environ["GESAKU_RETRIEVAL_MODE"] = "openagent"
        payload = settings_mod.settings()
        self.assertEqual(payload["agentic"]["retrievalMode"], "dump")


if __name__ == "__main__":
    unittest.main()
