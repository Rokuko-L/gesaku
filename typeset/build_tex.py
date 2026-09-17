#!/usr/bin/env python3
"""Build LaTeX source from chapter files."""
import sys
from pathlib import Path

# Add project root to sys.path to find _utf8 and core modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import paths
from core import prose

from core import _utf8
import re
import os
from pathlib import Path

CHAPTERS_DIR = paths.get_chapters_dir()
OUT_DIR = paths.get_typeset_dir()

def latex_escape(t):
    # Single-pass character escaping. Multi-pass .replace() chains re-scan their
    # own output (e.g. escaping '{' after '\\' corrupts the braces inside the
    # generated '\\textbackslash{}'), so escape every special character here.
    out = []
    for c in t:
        if c == '\\':
            out.append('\\textbackslash{}')
        elif c == '{':
            out.append('\\textbraceleft{}')
        elif c == '}':
            out.append('\\textbraceright{}')
        elif c == '&':
            out.append('\\&')
        elif c == '%':
            out.append('\\%')
        elif c == '$':
            out.append('\\$')
        elif c == '#':
            out.append('\\#')
        elif c == '_':
            out.append('\\_')
        elif c == '^':
            out.append('\\textasciicircum{}')
        elif c == '~':
            out.append('\\textasciitilde{}')
        else:
            out.append(c)
    return ''.join(out)

def md_to_latex(body):
    result = []
    for line in body.split('\n'):
        s = line.strip()
        if s == '---':
            result.append('\n\\scenebreak\n')
        elif s == '':
            result.append('')
        else:
            s = latex_escape(s)
            # Bold before italic (order matters for regex)
            s = re.sub(r'\*\*([^*]+)\*\*', r'\\textbf{\1}', s)
            s = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'\\textit{\1}', s)
            # Em dash: replace with comma-space (reads more naturally than a raw dash)
            s = s.replace('\u2014', ', ')
            s = s.replace('\u2013', '--')
            s = s.replace('\u201c', '``')
            s = s.replace('\u201d', "''")
            s = s.replace('\u2018', '`')
            s = s.replace('\u2019', "'")
            s = s.replace('\u2026', '\\ldots{}')
            # Convert straight ASCII quotes to LaTeX open/close
            # " at start of line or after space/punctuation = open (``)
            # " elsewhere = close ('')
            s = re.sub(r'(?<=\s)"(?=\w)', '``', s)    # space then "word
            s = re.sub(r'^"(?=\w)', '``', s)            # line-start "word
            s = re.sub(r'(?<=\w)"(?=[\s.,;:!?\-])', "''", s)  # word" then punct/space
            s = re.sub(r'(?<=\w)"$', "''", s)           # word" at line-end
            s = re.sub(r'(?<=[.?!])"', "''", s)         # punctuation" 
            # Catch any remaining straight quotes (open if after space, close otherwise)
            s = re.sub(r'(?<=\s)"', '``', s)
            s = re.sub(r'"(?=\s)', "''", s)
            s = re.sub(r'^"', '``', s)
            result.append(s)
    return '\n'.join(result)

def make_drop_cap(latex_body):
    """Extract first paragraph and wrap first letter in lettrine."""
    lines = latex_body.split('\n')
    first_para = []
    rest_start = 0
    found = False
    
    for i, l in enumerate(lines):
        if not found and l.strip():
            found = True
        if found:
            if l.strip() == '' or l.strip().startswith('\\scenebreak'):
                rest_start = i
                break
            first_para.append(l)
        else:
            rest_start = i + 1
    
    if not first_para:
        return latex_body
    
    para_text = ' '.join(first_para)
    rest = '\n'.join(lines[rest_start:])
    
    if len(para_text) < 2:
        return latex_body
    
    first_letter = para_text[0]
    after_first = para_text[1:]

    # Skip drop cap if first character is not a plain ASCII letter
    # (e.g. quote marks, backslash, digits — these all break \lettrine)
    if not first_letter.isalpha() or not first_letter.isascii():
        return latex_body

    # Find the rest of the first word to put in the lettrine second arg
    space_idx = after_first.find(' ')
    if space_idx > 0:
        word_rest = after_first[:space_idx]
        para_rest = after_first[space_idx:]
    else:
        word_rest = after_first
        para_rest = ""

    # Skip drop cap if word_rest contains LaTeX special characters
    UNSAFE = set('\\{}$&#^_~%')
    if any(c in UNSAFE for c in word_rest):
        return latex_body
    
    drop = f"\\lettrine[lines=2, lhang=0.1, nindent=0.2em]{{{first_letter}}}{{{word_rest}}}{para_rest}"
    return drop + '\n\n' + rest

def _chapter_num(path) -> int:
    m = re.search(r"ch_(\d+)\.md", path.name)
    return int(m.group(1)) if m else 10**9


chapters_tex = []
chapter_files = sorted(CHAPTERS_DIR.glob("ch_*.md"), key=_chapter_num)
nums = [_chapter_num(p) for p in chapter_files]
if nums:
    missing = [n for n in range(1, max(nums) + 1) if n not in nums]
    if missing:
        print(f"WARNING: missing chapters (will be absent from the PDF): {missing}")
for path in chapter_files:
    m = re.search(r"ch_(\d+)\.md", path.name)
    if not m:
        continue
    n = int(m.group(1))
    
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # The PDF is a deliverable too: strip non-prose here as well, or a chapter
    # like v4's ch_20 ships its reasoning dump into the typeset book even
    # though manuscript.md was cleaned.
    text = prose.strip_non_prose(text)
    if prose.looks_like_non_prose(text):
        print(f"WARNING: {path.name} has only {prose.prose_words(text)} words of "
              f"prose — it needs a redraft, not a typeset")
    if not text.strip():
        print(f"WARNING: {path.name} is empty — skipped")
        continue
    
    lines = text.strip().split('\n')
    # Consume the first line as the chapter title only if it's actually a
    # title (markdown header or bold line) — otherwise the chapter's first
    # line would be silently eaten by the published book.
    title_line = None
    first = lines[0].strip()
    m = re.match(r'^#{1,6}\s*(.+?)\s*$', first)
    if not m:
        m = re.match(r'^\*\*(.+?)\*\*\s*$', first)
    if m:
        title_line = m.group(1).strip().strip('*').strip()
        body = '\n'.join(lines[1:]).strip()
    else:
        title_line = f"Chapter {n}"
        body = '\n'.join(lines).strip()
        print(f"WARNING: {path.name} has no markdown title line — rendering as 'Chapter {n}'")

    if ': ' in title_line:
        label, subtitle = title_line.split(': ', 1)
    else:
        label, subtitle = title_line, ""

    chapter_name = subtitle if subtitle else label
    if re.search(r"[a-z]_[a-z]", chapter_name):
        print(f"WARNING: {path.name} title is a snake_case codename: '{chapter_name}' — "
              f"fix the chapter file's first line before publishing")
    latex_body = md_to_latex(body)
    latex_body = make_drop_cap(latex_body)
    
    # Check for chapter ornament (prefer vector PDF over raster PNG)
    art_base = str(paths.get_project_dir())
    pdf_path = os.path.join(art_base, "art", "pdf", f"ornament_ch{n:02d}.pdf")
    png_path = os.path.join(art_base, "art", f"ornament_ch{n:02d}.png")
    ornament_tex = ""
    ornament_file = None
    if os.path.exists(pdf_path):
        ornament_file = pdf_path
    elif os.path.exists(png_path):
        ornament_file = png_path
    if ornament_file:
        ornament_tex = (
            f"\\begin{{center}}\n"
            f"\\includegraphics[width=0.8in]{{{ornament_file}}}\n"
            f"\\end{{center}}\n"
            f"\\vspace{{0.15in}}\n"
        )
    
    chapters_tex.append(f"\\chapter{{{latex_escape(chapter_name)}}}\n\n{ornament_tex}{latex_body}\n")
    print(f"  {n:2d}. {title_line}")

content = '\n\\clearpage\n\n'.join(chapters_tex)

with open(os.path.join(OUT_DIR, "chapters_content.tex"), 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nWrote {len(chapters_tex)} chapters to typeset/chapters_content.tex")
