"""The export/PDF em-dash treatment, pinned.

Three production bugs lived here: a blanket U+2014 -> ", " that turned a
dialogue interrupt into a comma; a lookaround pattern that left `Wait ,  no`;
and a spaced-only pattern that swallowed the unspaced ones anyway.
"""

import re
import unittest

from core import prose

# The exact pipeline used by pipeline/phases/export.py::_export_clean.
# The lookbehind anchors on the space but cannot consume it, so a ` ,` survives
# one step and the comma cleanup owns it.
_EM_DASH_RE = re.compile(r"(?<=\s)\u2014[ \t]*(?=\S)")
_DOUBLE_SPACE_RE = re.compile(r" {2,}")
_DOUBLE_COMMA_RE = re.compile(r" ?,\s*,")
_SPACE_COMMA_RE = re.compile(r"(?<=\S)\s+,")


def export_clean(text: str) -> str:
    text = _EM_DASH_RE.sub(', ', text)
    text = _DOUBLE_SPACE_RE.sub(" ", text)
    text = _DOUBLE_COMMA_RE.sub(",", text)
    return _SPACE_COMMA_RE.sub(",", text)


class EmDashTest(unittest.TestCase):
    def test_a_spaced_dash_becomes_a_tight_comma(self):
        self.assertEqual("Wait, no, stay.", export_clean("Wait \u2014 no, stay."))
        self.assertEqual("a, b", export_clean("a \u2014 b"))
        self.assertEqual("two, dashes, here.", export_clean("two \u2014 dashes \u2014 here."))

    def test_no_space_before_comma_survives(self):
        for source in ("Wait \u2014 no", "a \u2014 b", "x \u2014  y"):
            out = export_clean(source)
            self.assertNotIn(" ,", out, f"{source!r} -> {out!r}")
            self.assertNotIn("  ", out, f"{source!r} -> {out!r}")

    def test_an_interrupt_keeps_its_dash(self):
        """No space before the dash means the sentence is being cut off."""
        for source in ("Catch me if you\u2014", "and then\u2014nothing."):
            self.assertEqual(source, export_clean(source))

    def test_a_dash_after_an_existing_comma_does_not_double_it(self):
        self.assertEqual("he paused, then ran.",
                         export_clean("he paused, \u2014 then ran."))

    def test_the_pdf_path_has_no_raw_dash_left(self):
        """build_tex must emit --- for an interrupt: pdflatex+T1 has no U+2014."""
        import pathlib
        source = pathlib.Path("typeset/build_tex.py").read_text(encoding="utf-8")
        self.assertIn("'---'", source)
        self.assertNotIn("s.replace('\u2014', ', ')", source)


if __name__ == "__main__":
    unittest.main()
