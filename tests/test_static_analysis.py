#!/usr/bin/env python3
"""Static analysis gate: ruff pyflakes rules over the whole repo.

Catches the bug class the import-integrity scanner can't: names USED but
never imported (F821) — e.g. process_notes() calling call_llm with no
import, which only NameErrors when that code path first runs. Also flags
accidental redefinitions (F811).

Also guards the entry-point shape: a duplicated `if __name__ == "__main__"`
block makes the orchestrator run TWICE, which with `--from-scratch` silently
wipes the project and redoes the whole run (observed after the module split).

Run: uv run python -m unittest tests.test_static_analysis
"""

import ast
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class StaticAnalysisTest(unittest.TestCase):
    def test_ruff_pyflakes_clean(self):
        cmd = [
            sys.executable, "-m", "ruff", "check", ".",
            "--select", "F821,F811",
            "--no-cache",
            "--output-format", "concise",
        ]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(
            proc.returncode, 0,
            f"ruff found undefined/redefined names:\n{proc.stdout}\n{proc.stderr}"
        )

    def test_entry_points_call_main_once(self):
        """Exactly one top-level `if __name__ == "__main__": main()` per entry."""
        entry_points = [
            ROOT / "run_pipeline.py",
            ROOT / "cli.py",
            ROOT / "install_fonts.py",
        ]
        for path in entry_points:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls = 0
            for node in tree.body:
                if not isinstance(node, ast.If):
                    continue
                test = node.test
                is_main = (
                    isinstance(test, ast.Compare)
                    and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"
                )
                if is_main:
                    calls += 1
            self.assertEqual(
                calls, 1,
                f"{path.name} has {calls} `if __name__` blocks — a duplicate "
                f"runs the whole pipeline twice (with --from-scratch that "
                f"silently wipes the project)",
            )


if __name__ == "__main__":
    unittest.main()
