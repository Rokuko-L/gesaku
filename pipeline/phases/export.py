"""Phase 4 — Export: outline, arc summary, manuscript, PDF.

Ships the peak, not the latest: when the best scored cycle is not the
current HEAD, its chapters are restored before building deliverables.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

import re
import shutil
import sys

from core import novel_tex as novel_tex_module
from core import paths
from core.llm import call_llm

from pipeline.pipeline_infra import (
    _chapter_num_key, banner, best_novel_checkpoint, count_chapter_files,
    count_words_in_chapters, fmt_score, git_add_commit, log_result,
    run_tool, save_state, step, timeout_for, uv_run,
)



# ---------------------------------------------------------------------------
# PHASE 4 — EXPORT
# ---------------------------------------------------------------------------




def run_export(state: dict) -> dict:
    """
    Build final deliverables: outline, arc summary, manuscript, PDF.
    """
    banner("PHASE 4: EXPORT", "=")

    # Ship the peak, not the latest. Revision cycles keep edits that pass
    # tolerance (an LLM rewrite may regress 0.8 and still be kept), so the
    # last cycle is not necessarily the best one. Restore the best-scoring
    # commit's chapters before building deliverables.
    best_score, best_commit = best_novel_checkpoint(state)
    current = state.get("novel_score")
    if best_score is not None and best_commit and current is not None:
        try:
            if float(current) < float(best_score):
                step(f"Restoring best novel {best_commit} ({best_score}) "
                     f"over current {current}")
                res = run_tool(f"git checkout {best_commit} -- chapters",
                               cwd=str(paths.get_project_dir()))
                if res.returncode == 0:
                    git_add_commit(
                        f"restore best novel {best_commit} (score {best_score}) for export")
                    state["novel_score"] = best_score
                    save_state(state)
                else:
                    step(f"WARNING: best-commit restore failed — exporting current {current}")
        except (TypeError, ValueError):
            pass

    root_dir = paths.get_root_dir()
    chapters_dir = paths.get_chapters_dir()
    typeset_dir = paths.get_typeset_dir()

    # 1. Rebuild outline from chapters
    build_outline = root_dir / "pipeline" / "build_outline.py"
    if build_outline.exists():
        step("Rebuilding outline from chapters...")
        uv_run("pipeline/build_outline.py", timeout=timeout_for("long"))

    # 2. Build arc summary
    build_arc = root_dir / "pipeline" / "build_arc_summary.py"
    if build_arc.exists():
        step("Building arc summary...")
        n_ch_arc = count_chapter_files()
        uv_run("pipeline/build_arc_summary.py",
               timeout=max(timeout_for("short"),
                           -(-n_ch_arc // 4) * timeout_for("short") // 2 + 60))

    # 3. Pre-export cleanup: strip AI-tell formatting patterns for the EXPORTED
    #    deliverables only — the canonical chapter files are never mutated.
    #    (build_tex.py applies the same em-dash treatment for the PDF.)
    _EM_DASH_RE = re.compile(r'\u2014')                          # unicode em dash
    _BOLD_RE    = re.compile(r'\*\*(.+?)\*\*')                   # **bold** → plain

    def _export_clean(text: str) -> str:
        return _BOLD_RE.sub(r'\1', _EM_DASH_RE.sub(', ', text))

    # 4. Concatenate chapters into manuscript.md (written into project dir)
    step("Building manuscript.md...")
    manuscript = paths.get_manuscript_path()
    chapter_files = sorted(chapters_dir.glob("ch_*.md"), key=_chapter_num_key)

    total_planned = state.get("chapters_total", 0)
    if total_planned and len(chapter_files) < total_planned:
        present = {_chapter_num_key(p) for p in chapter_files}
        missing = [n for n in range(1, total_planned + 1) if n not in present]
        step(f"WARNING: {len(missing)} planned chapter(s) missing from manuscript: {missing}")

    parts = []
    for ch_file in chapter_files:
        text = _export_clean(ch_file.read_text(encoding="utf-8").strip())
        if text:
            parts.append(text)

    if parts:
        manuscript.write_text("\n\n---\n\n".join(parts) + "\n", encoding="utf-8")
        word_count = sum(len(p.split()) for p in parts)
        step(f"Manuscript: {len(parts)} chapters, {word_count} words")
    else:
        step("WARNING: no chapter files found for manuscript")

    # 4. Build LaTeX
    build_tex = root_dir / "typeset" / "build_tex.py"
    if build_tex.exists():
        step("Building LaTeX content...")
        run_tool(f'uv run python "{build_tex}"', timeout=timeout_for("short"), cwd=str(paths.get_typeset_dir()))

        # 5. Typeset with tectonic (if available)
        novel_tex = typeset_dir / "novel.tex"
        tex_valid = (
            novel_tex.exists()
            and novel_tex.stat().st_size >= 100
            and "\\end{document}" in novel_tex.read_text(encoding="utf-8")
        )
        if not tex_valid:
            step("novel.tex not found, empty, or incomplete (no \\end{document}) — generating via LLM...")
            for tex_attempt in range(3):
                try:
                    uv_run("pipeline/gen_novel_tex.py", timeout=timeout_for("short"))
                    if novel_tex.exists() and novel_tex.stat().st_size >= 100 and "\\end{document}" in novel_tex.read_text(encoding="utf-8"):
                        break
                except Exception as e:
                    step(f"LLM tex generation attempt {tex_attempt+1}/3 failed ({e})")
            if not novel_tex.exists() or novel_tex.stat().st_size < 100:
                step("Falling back to default template...")
                novel_tex_module.generate_default_novel_tex(novel_tex)

        compiled = False
        if novel_tex.exists():
            if shutil.which("tectonic"):
                # Ensure required fonts are installed before typesetting
                install_fonts_script = root_dir / "install_fonts.py"
                if install_fonts_script.exists():
                    step("Ensuring fonts are installed...")
                    uv_run("install_fonts.py", timeout=timeout_for("short"))

                # Retry loop for tectonic compilation with LLM debugging
                max_latex_fixes = 3
                compiled = False
                for fix_attempt in range(max_latex_fixes + 1):
                    if fix_attempt > 0:
                        step(f"Retrying typesetting PDF (attempt {fix_attempt + 1}/{max_latex_fixes + 1})...")
                    else:
                        step("Typesetting PDF with tectonic...")

                    # Use explicit bundle to avoid DNS/network connection failure
                    cmd = f"tectonic --bundle https://archive.org/services/purl/net/pkgwpub/tectonic-default {novel_tex.name}"
                    res = run_tool(cmd, timeout=timeout_for("short"), cwd=str(paths.get_typeset_dir()))
                    
                    pdf_out = typeset_dir / "novel.pdf"
                    if res.returncode == 0 and pdf_out.exists() and pdf_out.stat().st_size > 1000:
                        step(f"PDF generated: {pdf_out} ({pdf_out.stat().st_size // 1024} KB)")
                        compiled = True
                        break
                    
                    if fix_attempt >= max_latex_fixes:
                        break
                        
                    step(f"LaTeX compilation failed (exit code {res.returncode}).")
                    step("Attempting to auto-debug novel.tex using LLM with error logs...")
                    
                    # Call LLM to fix the LaTeX template
                    tex_code = novel_tex.read_text(encoding="utf-8")
                    
                    prompt = f"""The LaTeX file 'novel.tex' failed to compile using Tectonic.
                    
COMPILE LOGS / STDERR:
---
{res.stderr or '(no stderr)'}
---

CURRENT CONTENT OF 'novel.tex':
---
{tex_code}
---

Please analyze the compile log, identify the error (such as undefined control sequences, missing packages, syntax errors, or unescaped characters), and output the fully corrected, compile-ready version of 'novel.tex'. 

Rules:
1. Do NOT load fontspec. Use \\usepackage{{ebgaramond}} as defined.
2. Return ONLY the valid LaTeX code inside ```latex ... ``` fences. No conversational filler or explanations.
"""
                    try:
                        fixed_tex = call_llm(
                            prompt=prompt,
                            system="You are an expert LaTeX troubleshooter. You fix compilation errors and return only compile-ready corrected LaTeX code.",
                            model_key="review",
                            max_tokens=8000,
                            temperature=0.2,
                        )
                        # Extract from fences
                        m = re.search(r"```(?:latex|tex)?\s*\n(.*?)```", fixed_tex, re.DOTALL)
                        if m:
                            fixed_tex = m.group(1).strip()
                        else:
                            m2 = re.search(r"(\\documentclass[^]*?\\end\{document\})", fixed_tex, re.DOTALL)
                            if m2:
                                fixed_tex = m2.group(1).strip()
                        
                        if fixed_tex and len(fixed_tex) > 200:
                            novel_tex.write_text(fixed_tex, encoding="utf-8")
                            step("Wrote corrected novel.tex from LLM debugging.")
                        else:
                            step("WARNING: LLM returned invalid or empty LaTeX for fix.")
                    except Exception as e:
                        step(f"WARNING: LLM auto-debug API call failed: {e}")

                if not compiled:
                    # LLM debugging failed (observed: 3 attempts on a missing
                    # brace). Fall back to the deterministic default template —
                    # a known-good wrapper — and retry once before giving up.
                    step("LLM auto-debug failed to fix novel.tex. Falling back to "
                         "the deterministic default template...")
                    try:
                        novel_tex_module.generate_default_novel_tex(novel_tex)
                        res = run_tool(cmd, timeout=timeout_for("short"), cwd=str(paths.get_typeset_dir()))
                        if res.returncode == 0 and pdf_out.exists() and pdf_out.stat().st_size > 1000:
                            step(f"PDF generated from default template: {pdf_out} "
                                 f"({pdf_out.stat().st_size // 1024} KB)")
                            compiled = True
                        else:
                            step("WARNING: default template also failed to typeset.")
                    except Exception as e:
                        step(f"WARNING: default-template fallback failed: {e}")

                if not compiled:
                    step("WARNING: tectonic typesetting failed — novel.pdf not produced")
            else:
                step("tectonic not found, skipping PDF generation")
    else:
        step("typeset/build_tex.py not found, skipping LaTeX")


    # 6. Final commit
    commit_hash = git_add_commit("export: manuscript, outline, arc summary, PDF")
    total_words = count_words_in_chapters()
    log_result(commit_hash, "export", fmt_score(state.get("novel_score")),
               total_words, "export", "Final export")

    if shutil.which("tectonic") and not compiled:
        state["phase"] = "complete_no_pdf"
    else:
        state["phase"] = "complete"
    state["current_focus"] = "done"
    save_state(state)

    banner(f"EXPORT COMPLETE — {len(chapter_files)} chapters, {total_words} words (Phase: {state['phase']})")
    return state
