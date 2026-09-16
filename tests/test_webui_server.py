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


class LogStreamHelpersTest(unittest.TestCase):
    """Log tailing: line levels and the backfill offset.

    Both exist because of observed console bugs: everything was tagged `[llm]`
    (mis-labelling stdout as model calls), and the pane was empty for a
    *finished* run because the stream started reading at EOF.
    """

    def test_line_level_classifies_pipeline_output(self):
        from routes.stream import _line_level
        self.assertEqual("banner", _line_level("=" * 60))
        self.assertEqual("banner", _line_level("  ------------------  "))
        self.assertEqual("step", _line_level("  [12:34:56] Generating world bible..."))
        self.assertEqual("warn", _line_level("  WARNING: outline plant hygiene failed"))
        self.assertEqual("warn", _line_level("Traceback (most recent call last):"))
        self.assertEqual("warn", _line_level("  FATAL ERROR in revision: boom"))
        self.assertEqual("raw", _line_level("just some model output"))
        # Not an LLM call, and must not be labelled as one.
        self.assertNotEqual("raw", _line_level("=" * 10))

    def test_banner_title_after_a_separator(self):
        """banner() prints sep / title / sep, so the line after a separator is
        a banner title — otherwise phase headers read as raw output."""
        from routes.stream import _line_level
        self.assertEqual("banner", _line_level("PHASE 3: REVISION", prev_was_separator=True))
        self.assertEqual("raw", _line_level("PHASE 3: REVISION"))
        # Structured output sitting under a closing rule is not a title.
        self.assertEqual("raw", _line_level(
            "State: phase=revision, foundation_score=7.0", prev_was_separator=True))
        # A step line right after a separator stays a step.
        self.assertEqual("step", _line_level("  [12:34:56] step", prev_was_separator=True))
        # And a separator is still a separator.
        self.assertEqual("banner", _line_level("=" * 60, prev_was_separator=True))

    def test_tail_seed_starts_at_a_line_boundary(self):
        import tempfile
        from pathlib import Path as _P
        from routes.stream import _tail_seed

        with tempfile.TemporaryDirectory() as tmp:
            f = _P(tmp) / "run.log"
            # write_bytes: write_text would translate \n to \r\n on Windows and
            # make the boundary assertion platform-dependent.
            f.write_bytes("".join(f"line {i:04d}\n" for i in range(1000)).encode())
            size = f.stat().st_size
            offset = _tail_seed(f, max_bytes=200)
            self.assertGreater(offset, 0)
            self.assertLess(offset, size)
            with open(f, "rb") as fh:
                fh.seek(offset)
                first = fh.readline().decode()
            # We must land exactly on a line start, never mid-line.
            self.assertRegex(first, r"^line \d{4}\n$")

    def test_tail_seed_returns_zero_for_a_short_log(self):
        import tempfile
        from pathlib import Path as _P
        from routes.stream import _tail_seed

        with tempfile.TemporaryDirectory() as tmp:
            f = _P(tmp) / "small.log"
            f.write_text("one\ntwo\n", encoding="utf-8")
            self.assertEqual(0, _tail_seed(f, max_bytes=65536))

    def test_tail_seed_survives_a_missing_file(self):
        from routes.stream import _tail_seed
        self.assertEqual(0, _tail_seed(SERVER_PATH.parent / "does-not-exist.log"))


if __name__ == "__main__":
    unittest.main()
