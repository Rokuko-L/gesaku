#!/usr/bin/env python3
"""Offline tests for LLM call telemetry (core/llm.py _emit_llm_event).

The GUI needs per-call token/duration/prompt data without touching the
network. call_llm now appends one JSONL event per API attempt to the
active project's llm_events.jsonl. These tests drive the real function via
httpx.MockTransport and assert the event contract.

Run: uv run python -m unittest tests.test_llm_telemetry
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import llm, paths


def api_response(status=200, body=None):
    if body is None:
        body = {
            "content": [{"type": "text", "text": "generated"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 111, "output_tokens": 222},
        }
    return httpx.Response(
        status, json=body,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))


def sse_body(events):
    return "".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


# What a real gateway sends: placeholder zeros up front, real totals last.
SSE_ANTHROPIC = sse_body([
    {"type": "message_start",
     "message": {"usage": {"input_tokens": 0, "output_tokens": 0}}},
    {"type": "content_block_delta",
     "delta": {"type": "text_delta", "text": "generated"}},
    {"type": "message_delta",
     "delta": {"stop_reason": "end_turn"},
     "usage": {"input_tokens": 0, "output_tokens": 0}},
    {"type": "message_delta",
     "delta": {},
     "usage": {"input_tokens": 2023, "output_tokens": 32}},
])


def sse_response(body):
    return httpx.Response(
        200, text=body, headers={"content-type": "text/event-stream"},
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))


class _FakeResp:
    """Minimal stand-in so the pure extractors can be driven directly."""

    def __init__(self, text, content_type="application/json"):
        self.text = text
        self.headers = {"content-type": content_type}


class UsageExtractionTest(unittest.TestCase):
    """Gateways stream even when streaming was not requested.

    The usage and stop_reason then live inside the SSE frames. Reading
    `json.loads(resp.text)` sees the stream, fails to parse, and logs the call
    with null tokens — which is how 663 recorded calls ended up with no usage.
    """

    def test_usage_scan_takes_the_last_nonzero_block(self):
        self.assertEqual((2023, 32), llm._usage_from_sse(SSE_ANTHROPIC))

    def test_usage_scan_falls_back_to_a_zero_block(self):
        zeros = sse_body([
            {"type": "message_start",
             "message": {"usage": {"input_tokens": 0, "output_tokens": 0}}},
        ])
        self.assertEqual((0, 0), llm._usage_from_sse(zeros))

    def test_usage_scan_survives_an_empty_stream(self):
        self.assertEqual((None, None), llm._usage_from_sse(""))

    def test_usage_pair_accepts_both_dialects(self):
        self.assertEqual((5, 7), llm._usage_pair(
            {"prompt_tokens": 5, "completion_tokens": 7}))
        self.assertEqual((5, 7), llm._usage_pair(
            {"input_tokens": 5, "output_tokens": 7}))
        self.assertEqual((None, None), llm._usage_pair(None))

    def test_response_telemetry_reads_a_stream(self):
        tin, tout, stop = llm._response_telemetry(
            _FakeResp(SSE_ANTHROPIC, "text/event-stream"), "anthropic")
        self.assertEqual((2023, 32, "end_turn"), (tin, tout, stop))

    def test_response_telemetry_reads_a_plain_json_body(self):
        body = json.dumps({"content": [{"type": "text", "text": "x"}],
                           "stop_reason": "end_turn",
                           "usage": {"prompt_tokens": 9, "completion_tokens": 11}})
        self.assertEqual((9, 11, "end_turn"),
                         llm._response_telemetry(_FakeResp(body), "openai"))

    def test_streamed_call_records_real_usage(self):
        """End to end through call_llm: the event carries the final totals."""
        tmp = Path(tempfile.mkdtemp(prefix="gesaku_sse_"))
        (tmp / "projects").mkdir()
        orig = paths._root_dir
        paths._root_dir = tmp
        try:
            paths.set_project_name("sse")
            client = httpx.Client(
                transport=httpx.MockTransport(lambda req: sse_response(SSE_ANTHROPIC)))
            with mock.patch.object(llm, "get_client", return_value=client), \
                 mock.patch("time.sleep"):
                out = llm.call_llm("hello")
            self.assertEqual("generated", out)
            ev = json.loads(
                paths.get_llm_events_path().read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(2023, ev["tokens_in"])
            self.assertEqual(32, ev["tokens_out"])
            self.assertEqual("end_turn", ev["stop_reason"])
        finally:
            paths._root_dir = orig
            os.environ.pop("GESAKU_PROJECT", None)


class LLMTelemetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gesaku_telem_"))
        (self.tmp / "projects").mkdir()
        self._orig_root = paths._root_dir
        paths._root_dir = self.tmp
        paths.set_project_name("telem")

    def tearDown(self):
        paths._root_dir = self._orig_root
        # do not leak project env into other suites
        os.environ.pop("GESAKU_PROJECT", None)

    def _events(self):
        path = paths.get_llm_events_path()
        self.assertTrue(path.exists(), "llm_events.jsonl was not written")
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]

    def _run_with_transport(self, handler, *args, **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with mock.patch.object(llm, "get_client", return_value=client), \
             mock.patch("time.sleep"):
            return llm.call_llm(*args, **kwargs)

    def test_success_event_contract(self):
        out = self._run_with_transport(lambda req: api_response(), "hello world")
        self.assertEqual(out, "generated")
        events = self._events()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertTrue(ev["ok"])
        self.assertEqual(ev["attempt"], 1)
        self.assertEqual(ev["tokens_in"], 111)
        self.assertEqual(ev["tokens_out"], 222)
        self.assertEqual(ev["stop_reason"], "end_turn")
        self.assertEqual(ev["prompt_chars"], len("hello world"))
        self.assertTrue(ev["prompt_head"].startswith("hello"))
        self.assertGreaterEqual(ev["duration_ms"], 0)
        self.assertIn("model", ev)
        self.assertIn("model_key", ev)

    def test_retry_logs_failure_then_success(self):
        responses = [api_response(500, {"error": "boom"}), api_response()]
        self._run_with_transport(
            lambda req: responses.pop(0), "retry me")
        events = self._events()
        self.assertEqual(len(events), 2)
        self.assertFalse(events[0]["ok"])
        self.assertEqual(events[0]["attempt"], 1)
        self.assertIn("500", events[0]["error"])
        self.assertTrue(events[1]["ok"])
        self.assertEqual(events[1]["attempt"], 2)

    def test_auth_error_fails_fast_single_event(self):
        with self.assertRaises(httpx.HTTPStatusError):
            self._run_with_transport(
                lambda req: api_response(401, {"error": "bad key"}), "no key")
        events = self._events()
        self.assertEqual(len(events), 1)  # no retry storm
        self.assertFalse(events[0]["ok"])

    def test_events_are_project_scoped(self):
        self._run_with_transport(lambda req: api_response(), "first")
        paths.set_project_name("telem2")
        self._run_with_transport(lambda req: api_response(), "second")

        paths.set_project_name("telem")
        self.assertEqual(len(self._events()), 1)
        paths.set_project_name("telem2")
        self.assertEqual(len(self._events()), 1)


if __name__ == "__main__":
    unittest.main()
