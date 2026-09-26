"""JSON-repair parser tests (core.llm.parse_json_response) + encoding healing.

Offline and LLM-free: each case feeds a malformed judge payload and asserts the
healed parse. Exposed as a TestCase so CI discovers it (the script-style
`main()` below stays for running the file directly).
"""
from core import llm
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).resolve().parents[1]))

# (name, raw_input, expected_subset)
CASES = [
    (
        "Clean JSON",
        '{"key": "value", "num": 42}',
        {"key": "value", "num": 42},
    ),
    (
        "Unescaped quotes inside value string",
        '{"feedback": "He said "No way!" and ran.", "score": 8}',
        {"feedback": 'He said "No way!" and ran.', "score": 8},
    ),
    (
        "Missing comma across newline",
        '{"a": 1\n "b": "hello"}',
        {"a": 1, "b": "hello"},
    ),
    (
        "Missing comma on same line",
        '{"a": 1 "b": "hello"}',
        {"a": 1, "b": "hello"},
    ),
    (
        "Trailing commas in object and list",
        '{"a": [1, 2,],}',
        {"a": [1, 2]},
    ),
    (
        "Truncated JSON (cut off string)",
        '{"key": "value", "feedback": "This is truncated',
        {"key": "value", "feedback": "This is truncated"},
    ),
    (
        "Truncated JSON (cut off container)",
        '{"key": "value", "items": [1, 2',
        {"key": "value", "items": [1, 2]},
    ),
    (
        "Edge case: fake key-colon inside escaped dialogue",
        '{"feedback": "The dialogue goes: \\"next: a new beginning.\\" and then...", "score": 5}',
        {"feedback": 'The dialogue goes: "next: a new beginning." and then...', "score": 5},
    ),
    (
        "Edge case: unescaped dialogue quote-colon",
        '{"feedback": "He said "next: start" and laughed", "score": 6}',
        {"feedback": 'He said "next: start" and laughed', "score": 6},
    ),
    (
        "Hybrid complex repair",
        """
    {
      "weakest_moment": "He said "Don't look back!" and bolted."
      "score": 9,
      "revisions": [
        "fix dialogue",
        "tighten prose",
      ]
    }
    """,
        {
            "weakest_moment": "He said \"Don't look back!\" and bolted.",
            "score": 9,
            "revisions": ["fix dialogue", "tighten prose"],
        },
    ),
]


class JsonRepairTest(unittest.TestCase):
    def test_repair_cases(self):
        for name, raw_input, expected in CASES:
            with self.subTest(case=name):
                parsed = llm.parse_json_response(raw_input)
                for k, v in expected.items():
                    self.assertEqual(v, parsed.get(k), f"key '{k}' mismatch")


class EncodingHealingTest(unittest.TestCase):
    """A UTF-16 source document must not kill a run (evaluate.load_file)."""

    def test_utf16_file_is_healed_to_utf8(self):
        import pipeline.evaluate as evaluate
        text = "Hello, this is a UTF-16 encoded text to test self-healing."
        with tempfile.TemporaryDirectory(prefix="gesaku_enc_") as tmp:
            test_file = Path(tmp) / "utf16_dummy.md"
            test_file.write_bytes(text.encode("utf-16"))
            loaded = evaluate.load_file(test_file)
            self.assertEqual(text, loaded.lstrip("﻿"))
            healed = test_file.read_text(encoding="utf-8")
            self.assertEqual(text, healed.lstrip("﻿"))

    def test_missing_file_returns_empty(self):
        import pipeline.evaluate as evaluate
        with tempfile.TemporaryDirectory(prefix="gesaku_enc_") as tmp:
            self.assertEqual("", evaluate.load_file(Path(tmp) / "nope.md"))


if __name__ == "__main__":
    unittest.main()
