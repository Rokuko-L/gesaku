"""JSON-repair parser tests (core.llm.parse_json_response).

Offline and LLM-free: each case feeds a malformed judge payload and asserts the
healed parse. Exposed as a TestCase so CI discovers it (the script-style
`main()` below stays for running the file directly).
"""
from core import llm
import sys
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


def run_test(name, raw_input, expected_dict):
    try:
        parsed = llm.parse_json_response(raw_input)
        # Check keys and structure
        for k, v in expected_dict.items():
            assert parsed.get(k) == v, f"Key '{k}' mismatch: expected {v}, got {parsed.get(k)}"
        print(f"SUCCESS: {name}")
        return True
    except Exception as e:
        print(f"FAILED: {name}")
        print(f"   Input: {raw_input}")
        print(f"   Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("Running JSON repair parser unit tests...\n")
    success = True
    for name, raw_input, expected in CASES:
        success &= run_test(name, raw_input, expected)

    print("\n-------------------------------------------")
    if success:
        print("ALL TESTS PASSED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    main()
