from core import llm
import sys
import json
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).resolve().parents[1]))

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

    # 1. Clean JSON
    success &= run_test(
        "Clean JSON",
        '{"key": "value", "num": 42}',
        {"key": "value", "num": 42}
    )

    # 2. Unescaped quotes inside strings
    success &= run_test(
        "Unescaped quotes inside value string",
        '{"feedback": "He said "No way!" and ran.", "score": 8}',
        {"feedback": 'He said "No way!" and ran.', "score": 8}
    )

    # 3. Missing commas (newline)
    success &= run_test(
        "Missing comma across newline",
        '{"a": 1\n "b": "hello"}',
        {"a": 1, "b": "hello"}
    )

    # 4. Missing commas (same line)
    success &= run_test(
        "Missing comma on same line",
        '{"a": 1 "b": "hello"}',
        {"a": 1, "b": "hello"}
    )

    # 5. Trailing commas
    success &= run_test(
        "Trailing commas in object and list",
        '{"a": [1, 2,],}',
        {"a": [1, 2]}
    )

    # 6. Truncated JSON
    success &= run_test(
        "Truncated JSON (cut off string)",
        '{"key": "value", "feedback": "This is truncated',
        {"key": "value", "feedback": "This is truncated"}
    )
    success &= run_test(
        "Truncated JSON (cut off container)",
        '{"key": "value", "items": [1, 2',
        {"key": "value", "items": [1, 2]}
    )

    # 7. Edge Case: Fake key-colon pattern in string value
    # When quotes are escaped, the boundary check should avoid adding commas inside dialogue
    success &= run_test(
        "Edge case: fake key-colon inside escaped dialogue",
        '{"feedback": "The dialogue goes: \\"next: a new beginning.\\" and then...", "score": 5}',
        {"feedback": 'The dialogue goes: "next: a new beginning." and then...', "score": 5}
    )

    # 8. Edge Case: unescaped nested dialogue (needs both quote escape + boundary check)
    success &= run_test(
        "Edge case: unescaped dialogue quote-colon",
        '{"feedback": "He said "next: start" and laughed", "score": 6}',
        {"feedback": 'He said "next: start" and laughed', "score": 6}
    )

    # 9. Hybrid complex case
    hybrid_input = """
    {
      "weakest_moment": "He said "Don't look back!" and bolted."
      "score": 9,
      "revisions": [
        "fix dialogue",
        "tighten prose",
      ]
    }
    """ # missing comma after first key, trailing comma in array
    success &= run_test(
        "Hybrid complex repair",
        hybrid_input,
        {
            "weakest_moment": "He said \"Don't look back!\" and bolted.",
            "score": 9,
            "revisions": ["fix dialogue", "tighten prose"]
        }
    )

    print("\n-------------------------------------------")
    if success:
        print("ALL TESTS PASSED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED.")
        sys.exit(1)

if __name__ == "__main__":
    main()
