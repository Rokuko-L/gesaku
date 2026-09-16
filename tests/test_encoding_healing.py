"""Encoding self-healing for evaluate.load_file.

A UTF-16 (or otherwise non-UTF-8) source document must not kill a run: the
loader decodes it, rewrites it as clean UTF-8, and returns the text. Offline,
no LLM. Exposed as a TestCase so CI runs it.
"""
import sys
import tempfile
import unittest
from pathlib import Path

# Add parent dir to path
sys.path.append(str(Path(__file__).resolve().parents[1]))
import pipeline.evaluate as evaluate


class EncodingHealingTest(unittest.TestCase):
    def test_utf16_file_is_healed_to_utf8(self):
        text = "Hello, this is a UTF-16 encoded text to test self-healing."
        with tempfile.TemporaryDirectory(prefix="gesaku_enc_") as tmp:
            test_file = Path(tmp) / "utf16_dummy.md"
            test_file.write_bytes(text.encode("utf-16"))

            loaded = evaluate.load_file(test_file)
            self.assertEqual(text, loaded.lstrip("\ufeff"))

            # The point of self-healing: the file must now read as UTF-8.
            healed = test_file.read_text(encoding="utf-8")
            self.assertEqual(text, healed.lstrip("\ufeff"))

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory(prefix="gesaku_enc_") as tmp:
            self.assertEqual("", evaluate.load_file(Path(tmp) / "nope.md"))


def main():
    text = "Hello, this is a UTF-16 encoded text to test self-healing."
    with tempfile.TemporaryDirectory(prefix="gesaku_enc_") as tmp:
        test_file = Path(tmp) / "utf16_dummy.md"
        test_file.write_bytes(text.encode("utf-16"))
        print(f"Created UTF-16 file: {test_file}")

        loaded_text = evaluate.load_file(test_file)
        print(f"Loaded text: '{loaded_text}'")

        try:
            new_text = test_file.read_text(encoding="utf-8")
            print(f"File successfully read as UTF-8: '{new_text}'")
            print("SUCCESS: Encoding healed correctly!"
                  if new_text.lstrip("\ufeff") == text
                  else "FAILURE: Content mismatch after healing.")
        except UnicodeDecodeError as e:
            print(f"FAILURE: File still cannot be read as UTF-8: {e}")


if __name__ == "__main__":
    main()
