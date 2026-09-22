"""Offline tests for core.llm.call_llm_tools and MockLLM tool-loop support."""

import json
import os
import unittest

from core import llm
from core import llm_tools
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
        out = llm_tools._normalize_tools_for_dialect("anthropic", tools)
        self.assertEqual(out[0]["name"], "search_canon")
        self.assertIn("input_schema", out[0])

    def test_openai_from_native_shape(self):
        tools = [{
            "name": "read_chapter",
            "description": "Read chapter",
            "input_schema": {"type": "object", "properties": {}},
        }]
        out = llm_tools._normalize_tools_for_dialect("openai", tools)
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
        text, calls, stop = llm_tools._parse_tool_turn(data, "anthropic")
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
        text, calls, stop = llm_tools._parse_tool_turn(data, "openai")
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

    def test_anthropic_sse_input_json_delta_accumulates(self):
        import httpx
        frames = [
            ("content_block_delta", {"delta": {"type": "text_delta", "text": "hi"}}),
            ("content_block_start", {"content_block": {"type": "tool_use", "id": "toolu_1", "name": "search_canon", "input": {}}}),
            ("content_block_delta", {"delta": {"type": "input_json_delta", "partial_json": '{"query"'}}),
            ("content_block_delta", {"delta": {"type": "input_json_delta", "partial_json": ': "seal"}'}}),
            ("message_delta", {"delta": {"stop_reason": "tool_use"}}),
        ]
        sse = "".join(
            f"data: {json.dumps({'type': t, **payload})}\n\n" for t, payload in frames
        )
        n = {"i": 0}

        def handler(request):
            n["i"] += 1
            if n["i"] == 1:
                return httpx.Response(200, content=sse.encode("utf-8"),
                                      headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
            })

        self._install(handler)
        executed = []

        def ex(name, inp):
            executed.append((name, inp))
            return "ok"

        result = llm.call_llm_tools(
            [{"role": "user", "content": "x"}],
            [{"name": "search_canon", "description": "d", "input_schema": {"type": "object"}}],
            ex,
            budget=3,
        )
        self.assertEqual(executed, [("search_canon", {"query": "seal"})])
        self.assertEqual(result.agent_stop, "end_turn")
        # result.text is the final turn's text, not the earlier fragment.
        self.assertIn("done", result.text)

    def test_openai_sse_tool_call_fragments_merge(self):
        # Unit-level: the provider env drives the wire dialect, so exercise the
        # OpenAI parser (and the shared from-response wrapper) directly.
        import httpx
        frames = [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1",
                                                    "function": {"name": "search_canon", "arguments": '{"query"'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "seal"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        sse = "".join(f"data: {json.dumps(f)}\n\n" for f in frames)
        text, calls, stop, saw = llm_tools._parse_sse_tool_turn(sse, "openai")
        self.assertTrue(saw)
        self.assertEqual(stop, "tool_calls")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], {"id": "call_1", "name": "search_canon", "input": {"query": "seal"}})

        resp = httpx.Response(200, content=sse.encode("utf-8"),
                              headers={"content-type": "text/event-stream"})
        _text, calls2, _stop, ok, _head = llm_tools._parse_tool_turn_from_response(resp, "openai")
        self.assertTrue(ok)
        self.assertEqual(calls2, calls)

    def test_harvest_payload_has_no_consecutive_user_turns(self):
        import json as _json
        import httpx
        payloads = []

        def handler(request):
            payloads.append(_json.loads(request.content.decode("utf-8")))
            if len(payloads) == 1:
                return httpx.Response(200, json={
                    "content": [{"type": "tool_use", "id": "t1", "name": "search_canon", "input": {"q": "a"}}],
                    "stop_reason": "tool_use",
                })
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": '{"findings":[]}'}],
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
        # The harvest POST is the second request; its transcript must alternate.
        harvest_msgs = payloads[1]["messages"]
        roles = [m["role"] for m in harvest_msgs]
        self.assertEqual(roles, ["user", "assistant", "user"])
        # ...and the budget note is merged into that final user turn, not a new one.
        final = harvest_msgs[-1]["content"]
        self.assertTrue(any("BUDGET EXHAUSTED" in (b.get("text", "") if isinstance(b, dict) else b)
                            for b in (final if isinstance(final, list) else [final])))
        # No tools key on a tool-free harvest POST.
        self.assertNotIn("tools", payloads[1])

    def test_anthropic_sse_fragments_reach_executor(self):
        import httpx
        frames = [
            ("content_block_start", {"content_block": {"type": "tool_use", "id": "toolu_1", "name": "read_chapter", "input": {}}}),
            ("content_block_delta", {"delta": {"type": "input_json_delta", "partial_json": '{"chapter": 7}'}}),
            ("message_delta", {"delta": {"stop_reason": "tool_use"}}),
        ]
        sse = "".join(
            f"data: {json.dumps({'type': t, **payload})}\n\n" for t, payload in frames
        )
        n = {"i": 0}

        def handler(request):
            n["i"] += 1
            if n["i"] == 1:
                return httpx.Response(200, content=sse.encode("utf-8"),
                                      headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
            })

        self._install(handler)
        executed = []

        def ex(name, inp):
            executed.append((name, inp))
            return "ok"

        llm.call_llm_tools(
            [{"role": "user", "content": "x"}],
            [{"name": "read_chapter", "description": "d", "input_schema": {"type": "object"}}],
            ex,
            budget=3,
        )
        self.assertEqual(executed, [("read_chapter", {"chapter": 7})])

    def test_budget_zero_skips_tools_and_harvests(self):
        import httpx
        payloads = []

        def handler(request):
            payloads.append(json.loads(request.content.decode("utf-8")))
            if len(payloads) == 1:
                return httpx.Response(200, json={
                    "content": [{"type": "tool_use", "id": "t1", "name": "search_canon", "input": {}}],
                    "stop_reason": "tool_use",
                })
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": '{"findings":[]}'}],
                "stop_reason": "end_turn",
            })

        self._install(handler)
        executed = []
        result = llm.call_llm_tools(
            [{"role": "user", "content": "x"}],
            [{"name": "search_canon", "description": "d", "input_schema": {"type": "object"}}],
            lambda n, i: executed.append(n) or "ok",
            budget=0,
        )
        self.assertEqual(result.tool_calls_used, 0)
        self.assertEqual(executed, [])
        self.assertEqual(result.agent_stop, "budget_exhausted")
        # The harvest POST costs no tool budget, so it still runs: exactly one
        # tool-free request, with the budget note merged into the user turn.
        self.assertEqual(len(payloads), 2)
        harvest = payloads[1]
        self.assertNotIn("tools", harvest)
        final = harvest["messages"][-1]
        texts = [b.get("text", "") for b in final["content"]] if isinstance(final["content"], list) else [final["content"]]
        self.assertTrue(any("BUDGET EXHAUSTED" in t for t in texts))

    def test_executor_error_is_recorded_not_fatal(self):
        import httpx
        n = {"i": 0}

        def handler(request):
            n["i"] += 1
            if n["i"] == 1:
                return httpx.Response(200, json={
                    "content": [{"type": "tool_use", "id": "t1", "name": "bad_tool", "input": {}}],
                    "stop_reason": "tool_use",
                })
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
            })

        self._install(handler)

        def ex(name, inp):
            raise RuntimeError("boom")

        result = llm.call_llm_tools(
            [{"role": "user", "content": "x"}],
            [{"name": "bad_tool", "description": "d", "input_schema": {"type": "object"}}],
            ex,
            budget=3,
        )
        self.assertEqual(result.agent_stop, "end_turn")
        entry = result.trace[0]
        self.assertFalse(entry["ok"])
        self.assertIn("boom", entry["error"])

    def test_on_tool_result_callback_failure_is_soft(self):
        import httpx
        n = {"i": 0}

        def handler(request):
            n["i"] += 1
            if n["i"] == 1:
                return httpx.Response(200, json={
                    "content": [{"type": "tool_use", "id": "t1", "name": "search_canon", "input": {}}],
                    "stop_reason": "tool_use",
                })
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
            })

        self._install(handler)

        def bad_cb(entry, out):
            raise RuntimeError("callback boom")

        result = llm.call_llm_tools(
            [{"role": "user", "content": "x"}],
            [{"name": "search_canon", "description": "d", "input_schema": {"type": "object"}}],
            lambda n, i: "ok",
            budget=3,
            on_tool_result=bad_cb,
        )
        self.assertEqual(result.agent_stop, "end_turn")
        self.assertEqual(result.tool_calls_used, 1)

    def test_anthropic_sse_two_tools_keep_their_own_args(self):
        # Interleaved argument deltas must not cross-contaminate: each tool call
        # gets exactly its own input. A single-tool test cannot catch a merge bug.
        frames = [
            ("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "toolu_a", "name": "search_canon", "input": {}}}),
            ("content_block_start", {"index": 1, "content_block": {"type": "tool_use", "id": "toolu_b", "name": "read_chapter", "input": {}}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"query"'}}),
            ("content_block_delta", {"index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"chapter"'}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": ': "seal"}'}}),
            ("content_block_delta", {"index": 1, "delta": {"type": "input_json_delta", "partial_json": ": 7}"}}),
            ("message_delta", {"delta": {"stop_reason": "tool_use"}}),
        ]
        sse = "".join(f"data: {json.dumps({'type': t, **payload})}\n\n" for t, payload in frames)
        _text, calls, stop, saw = llm_tools._parse_sse_tool_turn(sse, "anthropic")
        self.assertTrue(saw)
        self.assertEqual(stop, "tool_use")
        self.assertEqual(calls, [
            {"id": "toolu_a", "name": "search_canon", "input": {"query": "seal"}},
            {"id": "toolu_b", "name": "read_chapter", "input": {"chapter": 7}},
        ])

    def test_openai_sse_two_tool_calls_keep_their_own_args(self):
        frames = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "search_canon", "arguments": '{"query"'}},
                {"index": 1, "id": "call_2", "function": {"name": "read_chapter", "arguments": '{"chapter"'}},
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": ': "seal"}'}},
                {"index": 1, "function": {"arguments": ": 7}"}},
            ]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        sse = "".join(f"data: {json.dumps(f)}\n\n" for f in frames)
        _text, calls, stop, saw = llm_tools._parse_sse_tool_turn(sse, "openai")
        self.assertTrue(saw)
        self.assertEqual(stop, "tool_calls")
        self.assertEqual(calls, [
            {"id": "call_1", "name": "search_canon", "input": {"query": "seal"}},
            {"id": "call_2", "name": "read_chapter", "input": {"chapter": 7}},
        ])

    def test_openai_indexless_delta_continues_current_call(self):
        # A gateway that omits `index` after the first frame must not split one
        # call into two (the second with an empty name).
        frames = [
            {"choices": [{"delta": {"tool_calls": [{"id": "call_1", "function": {"name": "search_canon", "arguments": '{"query"'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"function": {"arguments": ': "seal"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        sse = "".join(f"data: {json.dumps(f)}\n\n" for f in frames)
        _text, calls, stop, saw = llm_tools._parse_sse_tool_turn(sse, "openai")
        self.assertTrue(saw)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "search_canon")
        self.assertEqual(calls[0]["input"], {"query": "seal"})

    def test_anthropic_indexless_second_start_does_not_drop_first_call(self):
        # Two tool_use blocks, neither frame carrying `index`: both calls must
        # survive, each with the arguments that follow it.
        frames = [
            ("content_block_start", {"content_block": {"type": "tool_use", "id": "toolu_a", "name": "search_canon", "input": {}}}),
            ("content_block_delta", {"delta": {"type": "input_json_delta", "partial_json": '{"query": "seal"}'}}),
            ("content_block_start", {"content_block": {"type": "tool_use", "id": "toolu_b", "name": "read_chapter", "input": {}}}),
            ("content_block_delta", {"delta": {"type": "input_json_delta", "partial_json": '{"chapter": 7}'}}),
            ("message_delta", {"delta": {"stop_reason": "tool_use"}}),
        ]
        sse = "".join(f"data: {json.dumps({'type': t, **payload})}\n\n" for t, payload in frames)
        _text, calls, stop, saw = llm_tools._parse_sse_tool_turn(sse, "anthropic")
        self.assertTrue(saw)
        names = [c["name"] for c in calls]
        self.assertIn("search_canon", names)
        self.assertIn("read_chapter", names)
        # The second call must keep its own arguments (not the first's).
        by_name = {c["name"]: c["input"] for c in calls}
        self.assertIn("chapter", by_name.get("read_chapter", {}))

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
