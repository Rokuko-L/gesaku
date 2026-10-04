#!/usr/bin/env python3
"""Wire-format tests for the multi-provider LLM client.

These drive call_llm() against httpx.MockTransport so the ASSERTIONS see
the exact HTTP request each provider dialect produces (URL path, auth
headers, payload keys) and the exact normalization of each response
shape — including TruncationError parity across dialects.

Run: uv run python -m unittest tests.test_provider_llm
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import llm
from core import llm_base


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# Keys that resolve_provider / _build_request consult. Ambient shell env AND
# load_dotenv() (triggered the moment any earlier discover module imports
# core.paths / core.validation) can populate these — treat both as pollution.
_LLM_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "GESAKU_PROVIDER",
    "GESAKU_WRITER_PROVIDER",
    "GESAKU_JUDGE_PROVIDER",
    "GESAKU_REVIEW_PROVIDER",
    "GESAKU_WRITER_MODEL",
    "GESAKU_JUDGE_MODEL",
    "GESAKU_REVIEW_MODEL",
    "GESAKU_EXTRA_HEADERS",
)


def _pinned_provider_env(provider, **overrides):
    """Env overlay that pins the writer dialect so ambient keys cannot flip it.

    Prefer this over relying on OPENAI_* / ANTHROPIC_* key inference:
    discover-order load_dotenv() injects ANTHROPIC_API_KEY from the repo
    .env, which silently turns OpenAI wire-format tests into Anthropic ones.
    """
    env = {"GESAKU_WRITER_PROVIDER": provider}
    env.update(overrides)
    return env


class EnvIsolationTestBase(unittest.TestCase):
    """Snapshot/restore LLM env + llm_base._client around every test.

    Minimal discover-order guard: a case that forgets to restore (or a
    module that mutates env at import time) cannot leak into the next case.
    """

    def setUp(self):
        super().setUp()
        self._saved_llm_env = {k: os.environ.get(k) for k in _LLM_ENV_KEYS}
        self._saved_client = llm_base._client
        self.addCleanup(self._restore_isolation)

    def _restore_isolation(self):
        for key, value in self._saved_llm_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        llm.set_client(self._saved_client)


class ProviderResolutionTest(EnvIsolationTestBase):
    def test_default_is_anthropic(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GESAKU_PROVIDER", None)
            os.environ.pop("OPENAI_API_KEY", None)
            self.assertEqual(llm.resolve_provider("writer"), "anthropic")

    def test_global_override_applies_to_all_roles(self):
        with patch.dict(os.environ, {"GESAKU_PROVIDER": "openai"}):
            for role in llm.ROLES:
                self.assertEqual(llm.resolve_provider(role), "openai")

    def test_role_override_beats_global(self):
        env = {"GESAKU_PROVIDER": "openai", "GESAKU_JUDGE_PROVIDER": "anthropic"}
        with patch.dict(os.environ, env):
            self.assertEqual(llm.resolve_provider("judge"), "anthropic")
            self.assertEqual(llm.resolve_provider("writer"), "openai")

    def test_key_inference_openai_only(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-x"}, clear=False):
            os.environ.pop("GESAKU_PROVIDER", None)
            os.environ["ANTHROPIC_API_KEY"] = ""
            self.assertEqual(llm.resolve_provider("judge"), "openai")
            os.environ["ANTHROPIC_API_KEY"] = "sk-ant"
            self.assertEqual(llm.resolve_provider("judge"), "anthropic")

    def test_invalid_provider_raises_actionable_error(self):
        with patch.dict(os.environ, {"GESAKU_PROVIDER": "mistral"}):
            with self.assertRaises(llm.ProviderError) as ctx:
                llm.resolve_provider("writer")
            self.assertIn("GESAKU_PROVIDER", str(ctx.exception))

    def test_role_model_env_var_respected(self):
        with patch.dict(os.environ, {"GESAKU_JUDGE_MODEL": "some/gateway-model"}):
            self.assertEqual(llm._resolve_model("anthropic", "judge"), "some/gateway-model")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GESAKU_JUDGE_MODEL", None)
            self.assertEqual(
                llm._resolve_model("openai", "judge"), llm.DEFAULT_MODELS["openai"]["judge"])


class AnthropicWireFormatTest(EnvIsolationTestBase):
    """call_llm over the anthropic dialect must emit the same wire format as before."""

    def test_request_shape_and_auth(self):
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
            })
        env = _pinned_provider_env(
            "anthropic",
            ANTHROPIC_API_KEY="sk-ant-test",
            ANTHROPIC_BASE_URL="https://gateway.example",
        )
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GESAKU_PROVIDER", None)
            original = llm.get_client()
            llm.set_client(_mock_client(handler))
            try:
                out = llm.call_llm("PROMPT", system="SYS", model_key="writer", beta_context=True)
            finally:
                llm.set_client(original)
        self.assertEqual(out, "ok")
        self.assertTrue(captured["url"].startswith("https://gateway.example/v1/messages"))
        self.assertEqual(captured["headers"]["x-api-key"], "sk-ant-test")
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(captured["headers"]["anthropic-beta"], "context-1m-2025-08-07")
        self.assertEqual(captured["body"]["system"], "SYS")
        self.assertEqual(captured["body"]["messages"], [{"role": "user", "content": "PROMPT"}])
        self.assertIn("max_tokens", captured["body"])

    def test_no_key_header_when_keyless(self):
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json={"content": [{"type": "text", "text": "x"}]})
        env = _pinned_provider_env(
            "anthropic",
            ANTHROPIC_API_KEY="",
            ANTHROPIC_BASE_URL="http://localhost:8787",
        )
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GESAKU_PROVIDER", None)
            original = llm.get_client()
            llm.set_client(_mock_client(handler))
            try:
                llm.call_llm("p", model_key="writer")
            finally:
                llm.set_client(original)
        self.assertNotIn("x-api-key", captured["headers"])


class OpenAIWireFormatTest(EnvIsolationTestBase):
    def test_request_shape_system_message_and_bearer(self):
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
            })
        # gpt-4o (non-reasoning) exercises the max_tokens+temperature path;
        # the reasoning path has its own test below.
        # Pin openai: ambient ANTHROPIC_API_KEY (shell or load_dotenv from
        # core.paths) would otherwise flip inference to the anthropic dialect.
        env = _pinned_provider_env(
            "openai",
            OPENAI_API_KEY="sk-oai",
            OPENAI_BASE_URL="https://openrouter.example/api/v1",
            GESAKU_WRITER_MODEL="gpt-4o",
        )
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GESAKU_PROVIDER", None)
            original = llm.get_client()
            llm.set_client(_mock_client(handler))
            try:
                out = llm.call_llm("PROMPT", system="SYS", model_key="writer")
            finally:
                llm.set_client(original)
        self.assertEqual(out, "hi")
        # base URL keeps its /v1 prefix; path appends chat/completions
        self.assertTrue(captured["url"].startswith("https://openrouter.example/api/v1/chat/completions"))
        self.assertEqual(captured["headers"]["authorization"], "Bearer sk-oai")
        self.assertNotIn("x-api-key", captured["headers"])
        roles = [m["role"] for m in captured["body"]["messages"]]
        self.assertEqual(roles, ["system", "user"])
        self.assertEqual(captured["body"]["messages"][0]["content"], "SYS")
        self.assertIn("max_tokens", captured["body"])
        self.assertIn("temperature", captured["body"])

    def test_reasoning_model_gets_max_completion_tokens_no_temperature(self):
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]})
        env = _pinned_provider_env(
            "openai",
            OPENAI_API_KEY="sk",
            GESAKU_WRITER_MODEL="gpt-5.2",
        )
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GESAKU_PROVIDER", None)
            original = llm.get_client()
            llm.set_client(_mock_client(handler))
            try:
                llm.call_llm("p", model_key="writer")
            finally:
                llm.set_client(original)
        self.assertIn("max_completion_tokens", captured["body"])
        self.assertNotIn("max_tokens", captured["body"])
        self.assertNotIn("temperature", captured["body"])

    def test_truncation_parity_finish_reason_length(self):
        """finish_reason 'length' must trip TruncationError exactly like stop_reason max_tokens."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]})
        env = _pinned_provider_env("openai", OPENAI_API_KEY="sk")
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GESAKU_PROVIDER", None)
            original = llm.get_client()
            llm.set_client(_mock_client(handler))
            try:
                with self.assertRaises(llm.TruncationError):
                    llm.call_llm("p", model_key="writer")
            finally:
                llm.set_client(original)

    def test_sse_stream_body_parsed(self):
        """Gateways sometimes stream even when we didn't ask; chunks must parse."""
        sse = (
            'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        env = _pinned_provider_env("openai", OPENAI_API_KEY="sk")
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GESAKU_PROVIDER", None)
            original = llm.get_client()
            llm.set_client(_mock_client(handler))
            try:
                out = llm.call_llm("p", model_key="writer")
            finally:
                llm.set_client(original)
        self.assertEqual(out, "Hello")


class ExtraHeadersTest(EnvIsolationTestBase):
    def test_extra_headers_merged_into_request(self):
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
        env = _pinned_provider_env(
            "openai",
            OPENAI_API_KEY="sk",
            GESAKU_EXTRA_HEADERS=json.dumps(
                {"X-Title": "gesaku", "HTTP-Referer": "https://example.com"}),
        )
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GESAKU_PROVIDER", None)
            original = llm.get_client()
            llm.set_client(_mock_client(handler))
            try:
                llm.call_llm("p", model_key="writer")
            finally:
                llm.set_client(original)
        self.assertEqual(captured["headers"]["x-title"], "gesaku")
        self.assertEqual(captured["headers"]["http-referer"], "https://example.com")

    def test_invalid_extra_headers_json_raises(self):
        with patch.dict(os.environ, {"GESAKU_EXTRA_HEADERS": "{not json"}):
            with self.assertRaises(llm.ProviderError):
                llm._load_extra_headers()


_EMPTY_SSE = (
    'event: message_start\n'
    'data: {"type":"message_start","message":{"id":"x","role":"assistant",'
    '"content":[],"usage":{"input_tokens":0,"output_tokens":0}}}\n\n'
)
_TEXT_SSE = (
    'event: content_block_start\n'
    'data: {"type":"content_block_start","index":0,'
    '"content_block":{"type":"text","text":""}}\n\n'
    'event: content_block_delta\n'
    'data: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"hello"}}\n\n'
    'event: message_delta\n'
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    '"usage":{"input_tokens":5,"output_tokens":2}}\n\n'
)


class EmptyResponseRetryTest(EnvIsolationTestBase):
    """A 200 whose body never opened a text block is a retryable transport fault.

    Live combo gateways do this: the upstream streams thinking deltas and then
    closes without ever emitting text. Recorded as a success, the empty string
    fails the caller's validation and can kill a whole pipeline step.
    """

    def _run(self, handler):
        env = _pinned_provider_env("anthropic", ANTHROPIC_API_KEY="k")
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GESAKU_PROVIDER", None)
            original = llm.get_client()
            llm.set_client(_mock_client(handler))
            with patch("time.sleep"):  # keep the backoff out of the test
                try:
                    return llm.call_llm("p", model_key="writer", timeout=5)
                finally:
                    llm.set_client(original)

    def test_empty_then_good_is_retried(self):
        calls = []
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            body = _EMPTY_SSE if len(calls) == 1 else _TEXT_SSE
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)
        self.assertEqual(self._run(handler), "hello")
        self.assertEqual(len(calls), 2)

    def test_all_empty_raises_after_retries(self):
        calls = []
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=_EMPTY_SSE)
        with self.assertRaises(llm.EmptyResponseError):
            self._run(handler)
        self.assertEqual(len(calls), llm.LLM_MAX_TRANSPORT_ATTEMPTS)

    def test_empty_with_end_turn_is_also_retried(self):
        # Live combos also return an empty body WITH a normal stop_reason; that
        # is still a no-op generation, not a successful empty answer.
        calls = []
        empty_but_done = (
            'event: message_delta\n'
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
            '"usage":{"input_tokens":5,"output_tokens":0}}\n\n'
        )
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            body = empty_but_done if len(calls) == 1 else _TEXT_SSE
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)
        self.assertEqual(self._run(handler), "hello")
        self.assertEqual(len(calls), 2)

    def test_truncated_empty_keeps_truncation_semantics(self):
        # A max_tokens stop is NOT an empty-response fault: it must still raise
        # TruncationError so callers can retry with a bigger budget.
        sse = (
            'event: message_delta\n'
            'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"},'
            '"usage":{"input_tokens":5,"output_tokens":0}}\n\n'
        )
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse)
        with self.assertRaises(llm.TruncationError):
            self._run(handler)


if __name__ == "__main__":
    unittest.main()
