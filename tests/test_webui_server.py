#!/usr/bin/env python3
"""Smoke tests for the webui FastAPI bridge (webui/server.py).

Import-level test: the module pulls in fastapi, core.paths and the fixture
generators, so a bad import or syntax error fails here. The pure helpers are
checked on their owning modules (deps.py, routes/settings.py) against the
contract's shapes.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webui"))

SERVER_PATH = ROOT / "webui" / "server.py"
spec = importlib.util.spec_from_file_location("webui_server", SERVER_PATH)
webui_server = importlib.util.module_from_spec(spec)

SETTINGS_PATH = ROOT / "webui" / "routes" / "settings.py"
_settings_spec = importlib.util.spec_from_file_location("webui_settings", SETTINGS_PATH)
webui_settings = importlib.util.module_from_spec(_settings_spec)


def setUpModule():
    spec.loader.exec_module(webui_server)
    _settings_spec.loader.exec_module(webui_settings)


class ServerHelpersTest(unittest.TestCase):
    def test_norm_phase_maps_completed_runs_to_export(self):
        from deps import norm_phase
        self.assertEqual(norm_phase({"phase": "complete_no_pdf"}), "export")
        self.assertEqual(norm_phase({"phase": "complete"}), "export")

    def test_norm_phase_done_focus_is_idle(self):
        from deps import norm_phase
        self.assertEqual(norm_phase({"current_focus": "done"}), "idle")
        self.assertEqual(norm_phase({"phase": "drafting"}), "drafting")
        self.assertEqual(norm_phase({}), "idle")

    def test_mask_never_exposes_full_key(self):
        self.assertEqual(webui_settings._mask(None), "")
        self.assertEqual(webui_settings._mask(""), "")
        masked = webui_settings._mask("sk-ant-0123456789abcdef")
        self.assertNotIn("0123456789abcdef", masked)
        self.assertTrue(masked.startswith("sk-ant-"))


class LlmEventViewTest(unittest.TestCase):
    """The SSE feed and /api/llm-events must expose the same camelCase shape.

    They disagreed once: the feed forwarded the raw snake_case jsonl record, so
    live rows rendered blank while the same call looked right after a reload.
    """

    def test_maps_snake_case_to_camel_case(self):
        from deps import llm_event_view
        view = llm_event_view({
            "ts": "t", "model_key": "writer", "model": "m", "ok": True,
            "attempt": 1, "tokens_in": 10, "tokens_out": 20, "duration_ms": 30,
            "stop_reason": "end_turn", "prompt_chars": 40, "response_chars": 50,
            "prompt_head": "head", "error": "boom",
        })
        self.assertEqual(view["modelKey"], "writer")
        self.assertEqual(view["tokensIn"], 10)
        self.assertEqual(view["tokensOut"], 20)
        self.assertEqual(view["durationMs"], 30)
        self.assertEqual(view["promptHead"], "head")
        # A failed call must carry its reason into the API shape.
        self.assertEqual(view["error"], "boom")

    def test_both_producers_route_through_the_mapper(self):
        """Guard the actual regression: the SSE feed must MAP each record, not
        forward the raw jsonl dict. Asserting the import alone would pass with
        the call site reverted to `json.loads(line)`."""
        import inspect
        from routes import stream

        stream_src = inspect.getsource(stream)
        self.assertIn("llm_event_view(json.loads(line))", stream_src,
                      "the SSE llm frame must be built through llm_event_view")
        self.assertNotIn('_sse("llm", json.loads(line))', stream_src,
                         "the raw snake_case record must not be sent to the UI")

        server_src = SERVER_PATH.read_text(encoding="utf-8")
        self.assertIn("llm_event_view(json.loads(line))", server_src,
                      "/api/llm-events must map through llm_event_view")


if __name__ == "__main__":
    unittest.main()
