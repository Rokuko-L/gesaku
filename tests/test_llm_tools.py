"""Offline tests for core.llm.call_llm_tools and MockLLM tool-loop support."""

import os
import unittest

from core import llm
from core.mock_llm import MockLLM


class TestNormalizeTools(unittest.TestCase):
    def test_anthropic_from_function_shape(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "search_canon",
                "description": "Search canon",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }]
        out = llm._normalize_tools_for_dialect("anthropic", tools)
        self.assertEqual(out[0]["name"], "search_canon")
        self.assertIn("input_schema", out[0])

    def test_openai_from_native_shape(self):
        tools = [{
            "name": "read_chapter",
            "description": "Read chapter",
            "input_schema": {"type": "object", "properties": {}},
        }]
        out = llm._normalize_tools_for_dialect("openai", tools)
        self.assertEqual(out[0]["type"], "function")
        self.assertEqual(out[0]["function"]["name"], "read_chapter")


class TestParseToolTurn(unittest.TestCase):
    def test_anthropic_tool_use(self):
        data = {
            "content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "toolu_1", "name": "search_canon", "input": {"q": "seal"}},
            ],
            "stop_reason": "tool_use",
        }
        text, calls, stop = llm._parse_tool_turn(data, "anthropic")
        self.assertEqual(text, "checking")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "search_canon")
        self.assertEqual(stop, "tool_use")

    def test_openai_tool_calls(self):
        data = {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {
                            "name": "search_canon",
                            "arguments": '{"q": "seal"}',
                        },
                    }],
                },
            }]
        }
        text, calls, stop = llm._parse_tool_turn(data, "openai")
        self.assertEqual(calls[0]["name"], "search_canon")
        self.assertEqual(calls[0]["input"], {"q": "seal"})
        self.assertEqual(stop, "tool_calls")


class TestMockToolLoop(unittest.TestCase):
    def test_natural_end(self):
        mock = MockLLM()
        executed = []

        def ex(name, inp):
            executed.append((name, inp))
            return "ok"

        with mock.install_tools([{"text": "done", "tool_calls": None}]) as tool_mock:
            result = llm.call_llm_tools(
                [{"role": "user", "content": "hi"}],
                [{"name": "noop", "description": "d", "input_schema": {"type": "object"}}],
                ex,
                budget=12,
            )
        self.assertEqual(result.agent_stop, "end_turn")
        self.assertEqual(result.tool_calls_used, 0)
        self.assertEqual(result.text, "done")

    def test_budget_exhausted(self):
        mock = MockLLM()
        executed = []

        def ex(name, inp):
            executed.append(name)
            return "ok"

        script = [{
            "text": "",
            "tool_calls": [
                {"id": "1", "name": "search_canon", "input": {"q": "a"}},
                {"id": "2", "name": "search_canon", "input": {"q": "b"}},
                {"id": "3", "name": "read_chapter", "input": {"n": 3}},
            ],
        }]
        with mock.install_tools(script) as tool_mock:
            result = llm.call_llm_tools(
                [{"role": "user", "content": "hi"}],
                [{"name": "search_canon", "description": "d", "input_schema": {"type": "object"}}],
                ex,
                budget=2,
            )
        self.assertEqual(result.agent_stop, "budget_exhausted")
        self.assertEqual(result.tool_calls_used, 2)
        self.assertEqual(len(executed), 2)
        self.assertEqual(result.budget, 2)
        self.assertGreaterEqual(len(result.trace), 2)

    def test_trace_records_tools(self):
        mock = MockLLM()
        script = [{
            "text": "",
            "tool_calls": [{"id": "1", "name": "search_canon", "input": {"q": "seal"}}],
        }]
        with mock.install_tools(script):
            result = llm.call_llm_tools(
                [{"role": "user", "content": "x"}],
                [{"name": "search_canon", "description": "d", "input_schema": {"type": "object"}}],
                lambda n, i: "hit",
                budget=5,
            )
        # Script executes tools then would need another turn for end — mock
        # ends after executing if budget remains and script exhausted.
        # Our mock processes one turn's tools then exits the for-loop.
        self.assertGreaterEqual(result.tool_calls_used, 1)
        self.assertEqual(result.trace[0]["tool"], "search_canon")

    def test_default_budget_is_twelve(self):
        self.assertEqual(llm.DEFAULT_TOOL_BUDGET, 12)


class TestJudgeToolBudget(unittest.TestCase):
    def test_named_default(self):
        import os
        from pipeline import pipeline_infra
        os.environ.pop("GESAKU_JUDGE_TOOL_BUDGET", None)
        self.assertEqual(pipeline_infra.JUDGE_TOOL_BUDGET, 12)
        self.assertEqual(pipeline_infra.judge_tool_budget(), 12)
        # Single committed default: core and pipeline tables must match.
        self.assertEqual(llm.DEFAULT_TOOL_BUDGET, pipeline_infra.JUDGE_TOOL_BUDGET)
        os.environ["GESAKU_JUDGE_TOOL_BUDGET"] = "3"
        try:
            self.assertEqual(pipeline_infra.judge_tool_budget(), 3)
        finally:
            os.environ.pop("GESAKU_JUDGE_TOOL_BUDGET", None)


class TestToolLoopWire(unittest.TestCase):
    """httpx.MockTransport cases for real call_llm_tools wire behavior."""

    def setUp(self):
        self._orig_provider = os.environ.get("GESAKU_PROVIDER")
        self._orig_model = os.environ.get("GESAKU_WRITER_MODEL")
        self._orig_judge_model = os.environ.get("GESAKU_JUDGE_MODEL")
        os.environ["GESAKU_PROVIDER"] = "anthropic"
        os.environ["GESAKU_JUDGE_MODEL"] = "test-model"
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        for k, v in (
            ("GESAKU_PROVIDER", self._orig_provider),
            ("GESAKU_WRITER_MODEL", self._orig_model),
            ("GESAKU_JUDGE_MODEL", self._orig_judge_model),
        ):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        llm.set_client(None)

    def _install(self, handler):
        import httpx
        llm.set_client(httpx.Client(transport=httpx.MockTransport(handler)))

    def test_tool_payload_and_accumulation(self):
        import json
        import httpx
        seen_payloads = []

        def handler(request):
            body = json.loads(request.content.decode("utf-8"))
            seen_payloads.append(body)
            if len(seen_payloads) == 1:
                return httpx.Response(200, json={
                    "content": [{
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "search_canon",
                        "input": {"query": "seal"},
                    }],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                })
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": '{"findings":[],"leads_exhausted":true}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 8},
            })

        self._install(handler)
        executed = []

        def ex(name, inp):
            executed.append((name, inp))
            return "canon hit"

        result = llm.call_llm_tools(
            [{"role": "user", "content": "start"}],
            [{"name": "search_canon", "description": "d",
              "input_schema": {"type": "object", "properties": {}}}],
            ex,
            budget=5,
        )
        self.assertEqual(result.agent_stop, "end_turn")
        self.assertEqual(executed, [("search_canon", {"query": "seal"})])
        self.assertIn("tools", seen_payloads[0])
        self.assertEqual(seen_payloads[0]["tools"][0]["name"], "search_canon")
        # Second turn must include assistant tool_use + tool_result
        second = seen_payloads[1]["messages"]
        roles = [m["role"] for m in second]
        self.assertIn("assistant", roles)
        self.assertIn("user", roles)
        self.assertIn("leads_exhausted", result.text)

    def test_sse_tool_use_not_silent_end_turn(self):
        import httpx
        n = {"i": 0}

        def handler(request):
            n["i"] += 1
            if n["i"] == 1:
                sse = (
                    'event: message\n'
                    'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}\n\n'
                    'data: {"type":"content_block_start","content_block":{"type":"tool_use","id":"t1","name":"noop_tool","input":{}}}\n\n'
                    'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}\n\n'
                )
                return httpx.Response(
                    200,
                    content=sse.encode("utf-8"),
                    headers={"content-type": "text/event-stream"},
                )
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
            })

        self._install(handler)
        calls = []

        def ex(name, inp):
            calls.append(name)
            return "ok"

        result = llm.call_llm_tools(
            [{"role": "user", "content": "x"}],
            [{"name": "noop_tool", "description": "d", "input_schema": {"type": "object"}}],
            ex,
            budget=3,
        )
        self.assertEqual(result.agent_stop, "end_turn")
        self.assertGreaterEqual(len(calls), 1)
        self.assertIn("done", result.text)

    def test_unparseable_body_is_error_not_end_turn(self):
        import httpx

        def handler(request):
            return httpx.Response(200, text="not-json-at-all")

        self._install(handler)
        result = llm.call_llm_tools(
            [{"role": "user", "content": "x"}],
            [{"name": "noop_tool", "description": "d", "input_schema": {"type": "object"}}],
            lambda n, i: "ok",
            budget=3,
        )
        self.assertEqual(result.agent_stop, "error")
        self.assertTrue(any(t.get("error", "").startswith("unparseable") for t in result.trace))

    def test_stripped_tools_is_error(self):
        import httpx

        def handler(request):
            return httpx.Response(200, json={
                "content": [],
                "stop_reason": "tool_use",
            })

        self._install(handler)
        result = llm.call_llm_tools(
            [{"role": "user", "content": "x"}],
            [{"name": "noop_tool", "description": "d", "input_schema": {"type": "object"}}],
            lambda n, i: "ok",
            budget=3,
        )
        self.assertEqual(result.agent_stop, "error")

    def test_budget_exhausted_harvests_final_text(self):
        import json
        import httpx
        n = {"i": 0}

        def handler(request):
            n["i"] += 1
            if n["i"] == 1:
                return httpx.Response(200, json={
                    "content": [{
                        "type": "tool_use", "id": "t1",
                        "name": "search_canon", "input": {"q": "a"},
                    }],
                    "stop_reason": "tool_use",
                })
            # Request 2 is the post-budget harvest POST (tools stripped).
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": '{"findings":[],"leads_exhausted":true}'}],
                "stop_reason": "end_turn",
            })

        self._install(handler)
        result = llm.call_llm_tools(
            [{"role": "user", "content": "x"}],
            [{"name": "search_canon", "description": "d", "input_schema": {"type": "object"}}],
            lambda n, i: "ok",
            budget=1,
        )
        self.assertEqual(result.agent_stop, "budget_exhausted")
        self.assertEqual(result.tool_calls_used, 1)
        self.assertIn("leads_exhausted", result.text)

    def test_transport_retry_does_not_double_spend(self):
        import json
        import httpx
        n = {"i": 0}

        def handler(request):
            n["i"] += 1
            if n["i"] <= 2:
                return httpx.Response(503, text="busy")
            if n["i"] == 3:
                return httpx.Response(200, json={
                    "content": [{
                        "type": "tool_use", "id": "t1",
                        "name": "search_canon", "input": {"q": "a"},
                    }],
                    "stop_reason": "tool_use",
                })
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
            })

        self._install(handler)
        executed = []

        def ex(name, inp):
            executed.append(name)
            return "ok"

        result = llm.call_llm_tools(
            [{"role": "user", "content": "x"}],
            [{"name": "search_canon", "description": "d", "input_schema": {"type": "object"}}],
            ex,
            budget=5,
            timeout=2,
        )
        self.assertEqual(result.agent_stop, "end_turn")
        self.assertEqual(len(executed), 1)
        self.assertEqual(result.tool_calls_used, 1)

    def test_preflight_stripped_gateway(self):
        import httpx
        from pipeline import preflight

        def handler(request):
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "no tools"}],
                "stop_reason": "end_turn",
            })

        self._install(handler)
        ok, msg = preflight.probe_tool_path()
        self.assertFalse(ok)
        self.assertIn("strip", msg.lower())


if __name__ == "__main__":
    unittest.main()
