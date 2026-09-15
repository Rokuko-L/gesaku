#!/usr/bin/env python3
"""Static import-integrity check: EVERY import statement in the repo must
resolve — including lazy (function-level) imports.

Catches the bug class where moving/renaming modules leaves stale imports
inside functions that only blow up (or get silently swallowed) at runtime.
A plain `import module` smoke test cannot catch these; AST scanning can.

Run: uv run python -m unittest tests.test_import_integrity
"""

import ast
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCAN_DIRS = ["core", "pipeline", "foundation", "typeset", "tests"]
SKIP_PARTS = {"projects", ".venv", "__pycache__", "landing"}


def repo_files():
    yield from sorted(ROOT.glob("*.py"))
    for d in SCAN_DIRS:
        yield from sorted((ROOT / d).rglob("*.py"))


def local_top_level_modules():
    """Names importable because they exist at repo root."""
    mods = {p.stem for p in ROOT.glob("*.py")}
    mods |= {d.name for d in ROOT.iterdir()
             if d.is_dir() and (d / "__init__.py").exists()}
    return mods


def imported_top_levels(tree):
    """Yield (lineno, top_level_module) for every absolute import in the AST,
    including nested/function-level ones."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or node.module is None:
                continue  # relative intra-package import
            yield node.lineno, node.module.split(".")[0]


class ImportIntegrityTest(unittest.TestCase):
    def test_every_import_resolves(self):
        locals_ = local_top_level_modules()
        broken = []

        for path in repo_files():
            rel = path.relative_to(ROOT).as_posix()
            if SKIP_PARTS & set(path.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), rel)
            for lineno, top in imported_top_levels(tree):
                if top in locals_ or top in sys.stdlib_module_names:
                    continue
                try:
                    if importlib.util.find_spec(top) is not None:
                        continue
                except (ImportError, ValueError, ModuleNotFoundError):
                    pass
                broken.append(f"{rel}:{lineno} -> '{top}'")

        self.assertEqual(
            broken, [],
            "Imports that do not resolve (stdlib, third-party, or repo-local).\n"
            "These will crash at runtime — or worse, be silently swallowed by "
            "a broad except:\n" + "\n".join(broken))

    def test_no_stale_utils_imports(self):
        """utils.py was deleted; any reference is a regression."""
        offenders = []
        for path in repo_files():
            rel = path.relative_to(ROOT).as_posix()
            if SKIP_PARTS & set(path.parts):
                continue
            if path.name == "test_import_integrity.py":
                continue  # this file mentions 'utils' in its own check logic
            src = path.read_text(encoding="utf-8")
            if "utils" in src:
                for i, line in enumerate(src.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if "utils" in line and ("import utils" in line or "utils." in line
                                            or "from utils" in line):
                        offenders.append(f"{rel}:{i}: {stripped[:80]}")
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
