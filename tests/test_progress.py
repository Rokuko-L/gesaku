"""Progress events and live subprocess streaming.

Both exist because a run was unobservable for an hour: `run_tool` used
capture_output, so a 10-minute generation wrote nothing until it exited, and
the only visible line was the `RUN:` that started it.
"""
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.progress import PREFIX, emit, parse_line, stage_timer


def _captured(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


class EmitTest(unittest.TestCase):
    def test_a_line_is_prefixed_and_parses_back(self):
        out = _captured(emit, "stage_start", stage="gen_outline", chapter=3)
        self.assertTrue(out.startswith(PREFIX))
        obj = parse_line(out.strip())
        self.assertEqual(obj["event"], "stage_start")
        self.assertEqual(obj["stage"], "gen_outline")
        self.assertEqual(obj["chapter"], 3)
        self.assertIn("ts", obj)

    def test_none_fields_are_dropped(self):
        out = _captured(emit, "retry", attempt=1, detail=None)
        obj = parse_line(out.strip())
        self.assertNotIn("detail", obj)

    def test_a_non_progress_line_parses_to_none(self):
        self.assertIsNone(parse_line("  [00:00:00] RUN: something"))
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line(PREFIX + " not json"))

    def test_emit_never_raises_on_unserialisable_values(self):
        # Progress is observability, not control flow. A closed stdout or a
        # value json cannot encode must not abort a run.
        class Boom:
            def __repr__(self):
                raise RuntimeError("nope")
        self.assertIsInstance(_captured(emit, "note", detail=Boom()), str)

    def test_pipeline_and_core_emit_the_same_format(self):
        # foundation/ cannot import pipeline/, so core owns the format and the
        # pipeline delegates. If these ever diverge the webui silently loses
        # half its events.
        from pipeline import pipeline_infra
        out = _captured(pipeline_infra.emit, "note", detail="x")
        self.assertTrue(out.startswith(PREFIX))
        self.assertEqual(parse_line(out.strip())["detail"], "x")


class StageTimerTest(unittest.TestCase):
    def test_start_and_done_bracket_the_stage_with_elapsed(self):
        out = _captured(self._run)
        events = [parse_line(l) for l in out.splitlines() if parse_line(l)]
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds, ["stage_start", "stage_done"])
        self.assertEqual(events[0]["stage"], "gen_outline")
        self.assertGreaterEqual(events[1]["elapsed_s"], 0)

    def _run(self):
        with stage_timer("gen_outline", timeout_s=300):
            time.sleep(0.01)

    def test_an_exception_emits_stage_error_and_propagates(self):
        with self.assertRaises(ValueError):
            with stage_timer("boom"):
                raise ValueError("kaboom")
        # The event is emitted inside __exit__ before the raise continues.

    def test_stage_error_is_recorded(self):
        out = _captured(self._boom)
        obj = [parse_line(l) for l in out.splitlines() if parse_line(l)][-1]
        self.assertEqual(obj["event"], "stage_error")
        self.assertEqual(obj["stage"], "boom")
        self.assertIn("kaboom", obj["detail"])

    def _boom(self):
        try:
            with stage_timer("boom"):
                raise ValueError("kaboom")
        except ValueError:
            pass


class StreamSubprocessTest(unittest.TestCase):
    """run_tool streams; the timeout contract must survive the change."""

    def _run(self, code, timeout=30):
        from pipeline import pipeline_infra as infra
        return infra.run_tool(f'"{sys.executable}" -c "{code}"', timeout=timeout)

    def test_output_is_visible_before_the_process_exits(self):
        # The whole point: a child that prints then sleeps must show its line
        # while it is still running, not after.
        seen = []
        result = {}

        class Cap:
            def write(self, s):
                seen.append(s)
                return len(s)

            def flush(self):
                pass

        def go():
            from pipeline import pipeline_infra as infra
            real = sys.stdout
            sys.stdout = Cap()
            try:
                result["r"] = infra.run_tool(
                    f'"{sys.executable}" -c "import time; '
                    f'print(\'early\', flush=True); time.sleep(3); print(\'late\')"',
                    timeout=30)
            finally:
                sys.stdout = real

        t = threading.Thread(target=go)
        t.start()
        time.sleep(1.5)
        early_seen = any("early" in s for s in seen)
        t.join(25)
        self.assertTrue(early_seen,
                        "child output was not mirrored before exit")
        self.assertIn("early", result["r"].stdout)
        self.assertIn("late", result["r"].stdout)

    def test_a_silent_child_still_times_out(self):
        # Iterating proc.stdout directly blocks until a line arrives, so a
        # silent child would hang the parent forever. The reader thread plus a
        # queue deadline is what prevents that.
        t0 = time.monotonic()
        r = self._run("import time; time.sleep(30)", timeout=1)
        self.assertEqual(r.returncode, -1)
        self.assertEqual(r.stderr, "TIMEOUT")
        self.assertLess(time.monotonic() - t0, 15)

    def test_a_verbose_child_times_out_too(self):
        r = self._run(
            "import time\nfor i in range(500):\n    print(i, flush=True)\n"
            "    time.sleep(0.2)",
            timeout=1)
        self.assertEqual(r.returncode, -1)
        self.assertEqual(r.stderr, "TIMEOUT")

    def test_nonzero_exit_is_preserved(self):
        r = self._run("import sys; print('x'); sys.exit(3)", timeout=30)
        self.assertEqual(r.returncode, 3)
        self.assertIn("x", r.stdout)

    def test_run_tool_still_collects_stdout_for_parsers(self):
        # Several callers parse stdout from the result, so streaming must not
        # cost them that.
        r = self._run("print('overall_score: 7.5')")
        self.assertIn("overall_score: 7.5", r.stdout)
        from pipeline import pipeline_infra as infra
        self.assertEqual(infra.parse_score(r.stdout, "overall_score"), 7.5)


if __name__ == "__main__":
    unittest.main()
