#!/usr/bin/env python3
"""Build an EPUB 3 e-book from the project's chapter files.

Stdlib only (zipfile + uuid): no ebook toolchain, no new dependency, and it runs
offline — the EPUB is the one deliverable that does not need tectonic or LaTeX.

    uv run python typeset/build_epub.py            # writes typeset/novel.epub

Run from the repo root (the export phase invokes it the same way it invokes
typeset/build_tex.py).
"""
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import _utf8  # noqa: F401  (forces UTF-8 stdout/locale)
from core import paths

import html
import re


def _chapter_num(path: Path) -> int:
    m = re.search(r"ch_(\d+)\.md", path.name)
    return int(m.group(1)) if m else 10 ** 9


def md_to_xhtml(body: str) -> str:
    """Chapter markdown -> XHTML paragraphs.

    Deliberately narrow: paragraphs, **bold**, *italic*, `---` scene breaks.
    Everything is escaped; the only markup emitted is from this function.
    """
    out = []
    for raw in body.split("\n"):
        s = raw.strip()
        if not s:
            continue
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", s):
            out.append('<hr class="scenebreak"/>')
            continue
        # Em dash reads better as a comma break, matching the PDF's treatment.
        s = s.replace("\u2014", ", ").replace("\u2013", "-")
        s = html.escape(s, quote=False)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
        out.append(f"<p>{s}</p>")
    return "\n      ".join(out)


def _author() -> str:
    """Mirror novel_tex: the operator's configured git name, else a neutral one."""
    try:
        import subprocess
        name = subprocess.check_output(
            ["git", "config", "user.name"], text=True, encoding="utf-8",
            errors="replace").strip()
        if name:
            return name
    except Exception:
        pass
    return "Anonymous"


def _xhtml(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">\n'
        f'<head>\n  <meta charset="utf-8"/>\n  <title>{html.escape(title)}</title>\n'
        '  <link rel="stylesheet" type="text/css" href="style.css"/>\n'
        '</head>\n<body>\n  <section>\n      '
        f'{body}\n  </section>\n</body>\n</html>\n'
    )


CSS = """\
body { font-family: Georgia, "Times New Roman", serif; line-height: 1.6;
       margin: 0 5%; text-align: justify; }
h1 { font-size: 1.5em; margin: 2em 0 1.5em; text-align: left; page-break-before: always; }
p { margin: 0 0 0.6em; text-indent: 1.4em; }
p:first-of-type, h1 + p { text-indent: 0; }
hr.scenebreak { border: 0; margin: 1.4em 0; text-align: center; }
hr.scenebreak:after { content: "* * *"; letter-spacing: 0.4em; }
.titlepage { text-align: center; margin-top: 25%; }
.titlepage h1 { font-size: 2.1em; text-align: center; page-break-before: avoid; }
.titlepage p { text-indent: 0; font-style: italic; margin-top: 1.5em; }
"""

CONTAINER = """\
<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def build_epub(dest: Path) -> tuple[int, str]:
    """Write the EPUB. Returns (chapter_count, title)."""
    chapters_dir = paths.get_chapters_dir()
    title = paths.get_novel_title() or "Untitled"
    author = _author()

    chapter_files = sorted(chapters_dir.glob("ch_*.md"), key=_chapter_num)
    chapters = []
    for path in chapter_files:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            print(f"WARNING: {path.name} is empty — skipped")
            continue
        lines = text.split("\n")
        first = lines[0].strip()
        m = (re.match(r"^#{1,6}\s*(.+?)\s*$", first)
             or re.match(r"^\*\*(.+?)\*\*\s*$", first))
        n = _chapter_num(path)
        if m:
            heading = m.group(1).strip().strip("*").strip()
            body = "\n".join(lines[1:]).strip()
        else:
            heading = f"Chapter {n}"
            body = "\n".join(lines).strip()
        # Match the PDF: the visible heading is the subtitle when the title
        # line is "Chapter N: Subtitle".
        if ": " in heading:
            _label, subtitle = heading.split(": ", 1)
            if subtitle.strip():
                heading = subtitle.strip()
        chapters.append((n, heading, body))

    if not chapters:
        raise RuntimeError("no non-empty chapter files — nothing to bind into an EPUB")

    # Deterministic id: re-running export must not mint a new book identity.
    book_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL, f"gesaku:{paths.get_project_name()}:{title}"))
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
        '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
    ]
    spine = ['<itemref idref="cover"/>']
    nav_items, ncx_items = [], []
    files: list[tuple[str, bytes]] = []

    for idx, (n, heading, body) in enumerate(chapters, 1):
        name = f"ch{idx:02d}.xhtml"
        item_id = f"ch{idx:02d}"
        manifest.append(f'<item id="{item_id}" href="{name}" '
                        f'media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{item_id}"/>')
        nav_items.append(
            f'<li><a href="{name}">{html.escape(heading)}</a></li>')
        ncx_items.append(
            f'<navPoint id="{item_id}" playOrder="{idx}">'
            f'<navLabel><text>{html.escape(heading)}</text></navLabel>'
            f'<content src="{name}"/></navPoint>')
        files.append((f"OEBPS/{name}", _xhtml(
            heading, f"<h1>{html.escape(heading)}</h1>\n      {md_to_xhtml(body)}").encode("utf-8")))

    nav = _xhtml("Contents", '<nav epub:type="toc" id="toc">\n'
                 "<h1>Contents</h1>\n<ol>\n"
                 + "\n".join(nav_items) + "\n</ol>\n</nav>")
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f'<head><meta name="dtb:uid" content="urn:uuid:{book_id}"/></head>\n'
        f'<docTitle><text>{html.escape(title)}</text></docTitle>\n'
        "<navMap>\n" + "\n".join(ncx_items) + "\n</navMap>\n</ncx>\n"
    )
    cover = _xhtml(title,
                   f'<div class="titlepage">\n<h1>{html.escape(title)}</h1>\n'
                   f"<p>{html.escape(author)}</p>\n</div>")

    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="pub-id">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'  <dc:identifier id="pub-id">urn:uuid:{book_id}</dc:identifier>\n'
        f'  <dc:title>{html.escape(title)}</dc:title>\n'
        f'  <dc:creator>{html.escape(author)}</dc:creator>\n'
        '  <dc:language>en</dc:language>\n'
        f'  <meta property="dcterms:modified">{modified}</meta>\n'
        '</metadata>\n<manifest>\n  ' + "\n  ".join(manifest) + "\n</manifest>\n"
        "<spine toc=\"ncx\">\n  " + "\n  ".join(spine) + "\n</spine>\n</package>\n"
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    # `mimetype` must be the FIRST entry and STORED (uncompressed), or readers
    # refuse the file — the rest is deflated normally.
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                    compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/nav.xhtml", nav)
        zf.writestr("OEBPS/toc.ncx", ncx)
        zf.writestr("OEBPS/cover.xhtml", cover)
        zf.writestr("OEBPS/style.css", CSS)
        for name, data in files:
            zf.writestr(name, data)

    return len(chapters), title


def main() -> int:
    dest = paths.get_typeset_dir() / "novel.epub"
    try:
        count, title = build_epub(dest)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Wrote EPUB: {dest} ({count} chapters, {dest.stat().st_size // 1024} KB)")
    print(f"  title: {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
