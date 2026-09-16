#!/usr/bin/env python3
"""EPUB export — the builder is pure-python, so this whole path is offline.

Validates structure rather than existence: a `.epub` that opens is not the same
as a readable book. Checks the zip layout readers require (`mimetype` first and
STORED), package metadata, that every manifest/spine/nav/NCX reference resolves,
and that each chapter is well-formed XHTML with prose in it.
"""
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import paths
from typeset import build_epub as epub

XHTML = "{http://www.w3.org/1999/xhtml}"
OPF = "{http://www.idpf.org/2007/opf}"
DC = "{http://purl.org/dc/elements/1.1/}"
CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"


def validate(epub_path: Path) -> str:
    """Structural validation; raises AssertionError with the first problem."""
    with zipfile.ZipFile(epub_path) as zf:
        infos = zf.infolist()
        names = zf.namelist()

        assert infos[0].filename == "mimetype", "mimetype must be the first entry"
        assert infos[0].compress_type == zipfile.ZIP_STORED, "mimetype must be STORED"
        assert zf.read("mimetype").decode() == "application/epub+zip"

        container = ET.fromstring(zf.read("META-INF/container.xml"))
        opf_path = container.find(f".//{CONTAINER}rootfile").get("full-path")
        assert opf_path in names, f"container points at missing {opf_path}"

        opf = ET.fromstring(zf.read(opf_path))
        for tag in (f"{DC}identifier", f"{DC}title", f"{DC}language"):
            assert opf.find(f".//{tag}") is not None, f"missing {tag}"
        modified = [m for m in opf.findall(f".//{OPF}meta")
                    if m.get("property") == "dcterms:modified"]
        assert modified, "EPUB 3 requires dcterms:modified"

        base = Path(opf_path).parent

        def zpath(href: str) -> str:
            return f"{base.as_posix()}/{href}" if str(base) != "." else href

        manifest = {i.get("id"): i.get("href")
                    for i in opf.findall(f".//{OPF}item")}
        for href in manifest.values():
            assert zpath(href) in names, f"manifest href missing from zip: {href}"

        spine = [r.get("idref") for r in opf.findall(f".//{OPF}itemref")]
        assert spine, "empty spine"
        for idref in spine:
            assert idref in manifest, f"spine idref not in manifest: {idref}"

        nav = ET.fromstring(zf.read(zpath(manifest["nav"])))
        nav_links = [a.get("href") for a in nav.iter(f"{XHTML}a")]
        assert nav_links, "navigation document has no links"
        for href in nav_links:
            assert zpath(href) in names, f"nav link missing: {href}"

        ncx = ET.fromstring(zf.read(zpath(manifest["ncx"])))
        for src in ncx.iter("{http://www.daisy.org/z3986/2005/ncx/}content"):
            assert zpath(src.get("src")) in names, f"ncx target missing: {src.get('src')}"

        words = 0
        for item_id, href in manifest.items():
            if not href.endswith(".xhtml") or item_id in ("nav", "cover"):
                continue
            raw = zf.read(zpath(href))
            doc = ET.fromstring(raw)  # must be well-formed XML
            text = "".join(doc.itertext())
            assert "\ufffd" not in text, f"{href} has replacement characters"
            assert len(text.split()) >= 50, f"{href} looks empty"
            words += len(text.split())
        return f"{len(spine)} spine items, {len(nav_links)} nav links, {words} words"


class EpubExportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gesaku_epub_")
        self._saved_root = paths._root_dir
        self._saved_proj = paths._project_name
        root = Path(self._tmp.name)
        (root / "pyproject.toml").write_text("[tool.gesaku]", encoding="utf-8")
        (root / ".env").write_text("GESAKU_PROVIDER=openai\n", encoding="utf-8")
        paths._root_dir = root
        paths._project_name = None
        paths.set_project_name("epub_probe")
        paths.get_project_dir().mkdir(parents=True, exist_ok=True)
        self.chapters = paths.get_chapters_dir()
        self.chapters.mkdir(parents=True, exist_ok=True)
        (root / "projects" / "registry.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        paths._root_dir = self._saved_root
        paths._project_name = self._saved_proj
        self._tmp.cleanup()

    def _write_chapter(self, n: int, title: str, paragraphs: int = 6) -> None:
        body = "\n\n".join(
            f"Paragraph {i} of chapter {n}. " + "word " * 40
            for i in range(paragraphs))
        (self.chapters / f"ch_{n:02d}.md").write_text(
            f"# Chapter {n}: {title}\n\n{body}\n", encoding="utf-8")

    def test_builds_a_structurally_valid_epub(self):
        for n in (1, 2, 3):
            self._write_chapter(n, f"Title Number {n}")
        dest = paths.get_typeset_dir() / "novel.epub"

        count, title = epub.build_epub(dest)

        self.assertEqual(3, count)
        self.assertTrue(dest.exists())
        summary = validate(dest)
        # cover + 3 chapters
        self.assertIn("4 spine items", summary)
        self.assertIn("3 nav links", summary)

    def test_chapter_heading_drops_the_chapter_label(self):
        """Match the PDF: a 'Chapter N: Subtitle' first line renders as the
        subtitle, not the whole label."""
        self._write_chapter(1, "The Actual Title")
        dest = paths.get_typeset_dir() / "novel.epub"
        epub.build_epub(dest)

        with zipfile.ZipFile(dest) as zf:
            doc = ET.fromstring(zf.read("OEBPS/ch01.xhtml"))
        heading = doc.find(f".//{XHTML}h1").text
        self.assertEqual("The Actual Title", heading)

    def test_markup_is_escaped_and_emphasis_preserved(self):
        (self.chapters / "ch_01.md").write_text(
            "# Chapter 1: Symbols\n\n"
            "A <tag> & an ampersand, with **bold** and *italic* text. "
            + "word " * 40 + "\n",
            encoding="utf-8")
        dest = paths.get_typeset_dir() / "novel.epub"
        epub.build_epub(dest)

        with zipfile.ZipFile(dest) as zf:
            raw = zf.read("OEBPS/ch01.xhtml").decode("utf-8")
        self.assertNotIn("<tag>", raw, "raw markup must be escaped")
        self.assertIn("&lt;tag&gt;", raw)
        self.assertIn("&amp;", raw)
        self.assertIn("<strong>bold</strong>", raw)
        self.assertIn("<em>italic</em>", raw)

    def test_no_chapters_is_an_error_not_an_empty_book(self):
        dest = paths.get_typeset_dir() / "novel.epub"
        with self.assertRaises(RuntimeError):
            epub.build_epub(dest)
        self.assertFalse(dest.exists(), "a failed build must not leave a stub file")

    def test_identifier_is_stable_across_rebuilds(self):
        """Re-exporting the same novel must not mint a new book identity."""
        self._write_chapter(1, "Same Book")
        dest = paths.get_typeset_dir() / "novel.epub"
        epub.build_epub(dest)
        with zipfile.ZipFile(dest) as zf:
            first = ET.fromstring(zf.read("OEBPS/content.opf")).findtext(f".//{DC}identifier")
        epub.build_epub(dest)
        with zipfile.ZipFile(dest) as zf:
            second = ET.fromstring(zf.read("OEBPS/content.opf")).findtext(f".//{DC}identifier")
        self.assertEqual(first, second)


class EpubWiringTest(unittest.TestCase):
    """The export phase and CLI must actually reach the builder."""

    def test_export_phase_builds_the_epub(self):
        import inspect
        from pipeline.phases import export as export_mod

        src = inspect.getsource(export_mod)
        self.assertIn("typeset/build_epub.py", src,
                      "run_export must invoke the EPUB builder")
        self.assertIn("skip_epub", inspect.signature(export_mod.run_export).parameters,
                      "run_export must expose the skip_epub option")

    def test_cli_has_the_no_epub_flag(self):
        import inspect
        import run_pipeline

        src = inspect.getsource(run_pipeline.main)
        self.assertIn('"--no-epub"', src)
        self.assertIn("skip_epub=args.no_epub",
                      inspect.getsource(run_pipeline.run_pipeline),
                      "the flag must be threaded into run_export")


if __name__ == "__main__":
    unittest.main()
