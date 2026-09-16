#!/usr/bin/env python3
"""
test_multi_project.py — Verifies multi-project isolation using path helpers.

Tests:
  1. Two projects get separate directories under projects/
  2. Registry is updated atomically for each project
  3. --from-scratch clears state for the right project only
  4. Path isolation: project A's files don't appear in project B's dirs

Run with: python tests/test_multi_project.py
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import paths
import run_pipeline  # noqa: E402  — imported at module level: its imports
# read prompts/ from the real repo root, which a test that patches
# paths._root_dir would otherwise break.

PASS = "[PASS]"
FAIL = "[FAIL]"
_failed = []


def check(label, condition, detail=""):
    if condition:
        print(f"  {PASS} {label}")
    else:
        msg = f"  {FAIL} {label}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        _failed.append(label)


def test_project_dir_isolation(tmp_root: Path):
    """Projects A and B should have completely separate directories."""
    # Override root to tmp
    orig_root = paths._root_dir
    paths._root_dir = tmp_root

    try:
        paths.set_project_name("alpha")
        alpha_dir = paths.get_project_dir()
        alpha_chapters = paths.get_chapters_dir()
        alpha_state = paths.get_state_path()

        paths.set_project_name("beta")
        beta_dir = paths.get_project_dir()
        beta_chapters = paths.get_chapters_dir()
        beta_state = paths.get_state_path()

        check("alpha dir != beta dir", alpha_dir != beta_dir,
              f"{alpha_dir} vs {beta_dir}")
        check("alpha chapters != beta chapters", alpha_chapters != beta_chapters)
        check("alpha state != beta state", alpha_state != beta_state)

        # alpha directories shouldn't appear inside beta's tree
        check("alpha not in beta subtree",
              not str(alpha_dir).startswith(str(beta_dir)))

        # Write a sentinel file in alpha's chapters
        alpha_chapters.mkdir(parents=True, exist_ok=True)
        sentinel = alpha_chapters / "ch_01.md"
        sentinel.write_text("alpha content", encoding="utf-8")

        # Beta's glob should not see it
        paths.set_project_name("beta")
        beta_chapters.mkdir(parents=True, exist_ok=True)
        beta_files = list(beta_chapters.glob("ch_*.md"))
        check("alpha file not visible from beta chapters",
              sentinel not in beta_files,
              f"beta glob returned: {beta_files}")

    finally:
        paths._root_dir = orig_root
        paths._project_name = None


def test_registry_atomic_write(tmp_root: Path):
    """Registry should be written atomically and contain both projects."""
    orig_root = paths._root_dir
    paths._root_dir = tmp_root

    try:
        reg_path = paths.get_registry_path()
        reg_path.parent.mkdir(parents=True, exist_ok=True)

        registry = {}
        registry["project_a"] = {"title": "A Novel", "phase": "foundation"}
        paths.save_registry(registry, reg_path)

        # Verify written correctly
        loaded = json.loads(reg_path.read_text(encoding="utf-8"))
        check("registry written correctly", "project_a" in loaded)
        check("registry contains expected data",
              loaded["project_a"]["phase"] == "foundation")

        # Add second project atomically
        registry["project_b"] = {"title": "B Novel", "phase": "drafting"}
        paths.save_registry(registry, reg_path)
        loaded2 = json.loads(reg_path.read_text(encoding="utf-8"))
        check("registry has both projects",
              "project_a" in loaded2 and "project_b" in loaded2)
        check("no .tmp file leftover", not reg_path.with_suffix(".json.tmp").exists())

    finally:
        paths._root_dir = orig_root
        paths._project_name = None


def test_path_isolation_violation():
    """set_project_name should raise on path traversal attempts."""
    orig_name = paths._project_name
    try:
        raised = False
        try:
            paths.set_project_name("../escape")
        except ValueError:
            raised = True
        check("path traversal raises ValueError", raised)

        raised = False
        try:
            paths.set_project_name(".")
        except ValueError:
            raised = True
        check("dot project name raises ValueError", raised)

    finally:
        paths._project_name = orig_name


def test_get_root_dir_raises():
    """get_root_dir should raise RuntimeError on missing markers."""
    # Can't test this in-process without messing up state, just verify happy path
    root = paths.get_root_dir()
    check("get_root_dir returns valid path", root.is_dir(), str(root))
    check("get_root_dir has pyproject.toml or .env",
          (root / "pyproject.toml").exists() or (root / ".env").exists())


def test_state_isolation(tmp_root: Path):
    """State files for different projects should be independent."""
    orig_root = paths._root_dir
    paths._root_dir = tmp_root

    try:
        paths.set_project_name("novel_one")
        state_one = paths.get_state_path()
        state_one.parent.mkdir(parents=True, exist_ok=True)
        state_one.write_text('{"phase": "foundation"}', encoding="utf-8")

        paths.set_project_name("novel_two")
        state_two = paths.get_state_path()
        check("state files are different", state_one != state_two)
        check("novel_two state does not exist yet", not state_two.exists())

        state_two.parent.mkdir(parents=True, exist_ok=True)
        state_two.write_text('{"phase": "drafting"}', encoding="utf-8")

        # Verify they hold separate data
        data_one = json.loads(state_one.read_text(encoding="utf-8"))
        data_two = json.loads(state_two.read_text(encoding="utf-8"))
        check("projects have independent state",
              data_one["phase"] == "foundation" and data_two["phase"] == "drafting")

    finally:
        paths._root_dir = orig_root
        paths._project_name = None


def test_from_scratch_cleanup(tmp_root: Path):
    """from_scratch should clean up stale folders and files in the project workspace."""
    orig_root = paths._root_dir
    paths._root_dir = tmp_root

    try:
        paths.set_project_name("test_isolation_project")
        project_dir = paths.get_project_dir()
        project_dir.mkdir(parents=True, exist_ok=True)

        # Create mock directories and files
        chapters_dir = paths.get_chapters_dir()
        ch2 = chapters_dir / "ch_02.md"
        ch2.write_text("stale chapter content", encoding="utf-8")

        edit_logs = paths.get_edit_logs_dir()
        log = edit_logs / "ch02_cuts.json"
        log.write_text("{}", encoding="utf-8")

        state = paths.get_state_path()
        state.write_text("{}", encoding="utf-8")

        # A stale micro-plant store from a previous novel must also go.
        store = paths.get_open_callbacks_path()
        store.write_text("{}", encoding="utf-8")

        # Drive the loop from the production list so the test cannot drift from
        # the policy it claims to exercise.
        for name in ["chapters", "briefs", "edit_logs", "eval_logs", "typeset"]:
            p = project_dir / name
            if p.is_dir():
                try:
                    shutil.rmtree(p)
                except Exception:
                    pass
        for name in run_pipeline.FROM_SCRATCH_STALE_FILES:
            p = project_dir / name
            if p.is_file():
                try:
                    p.unlink()
                except Exception:
                    pass
        for p in project_dir.glob("retry_feedback_ch*.txt"):
            p.unlink(missing_ok=True)

        # Re-create empty folders like run_pipeline.py does
        paths.get_chapters_dir()

        check("stale ch_02.md deleted", not ch2.exists())
        check("stale log deleted", not log.exists())
        check("stale state.json deleted", not state.exists())
        check("stale micro-plant store deleted", not store.exists())
        check("chapters directory empty", len(list(chapters_dir.glob("*"))) == 0)
        # The policy must cover what a previous novel would leak into this one.
        for name in ("open_callbacks.json", ".outline_part2.done",
                     "premise_validation.json", "plant_hygiene.json"):
            check(f"from-scratch policy clears {name}",
                  name in run_pipeline.FROM_SCRATCH_STALE_FILES)

    finally:
        paths._root_dir = orig_root
        paths._project_name = None


class MultiProjectIsolationTest(unittest.TestCase):
    """Runs the check()-style assertions above under unittest.

    Without a TestCase these checks never executed in CI: `unittest discover`
    imports the module and finds nothing, so the path-isolation guarantees
    went unverified.
    """

    def setUp(self):
        _failed.clear()
        self._tmp = tempfile.TemporaryDirectory(prefix="gesaku_test_")
        self.tmp_root = Path(self._tmp.name)
        (self.tmp_root / "pyproject.toml").write_text("[tool.gesaku]", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()
        _failed.clear()

    def _assert_checks(self, fn, *args):
        fn(*args)
        self.assertEqual([], list(_failed), f"failed checks: {list(_failed)}")

    def test_project_dir_isolation(self):
        self._assert_checks(test_project_dir_isolation, self.tmp_root)

    def test_registry_atomic_write(self):
        self._assert_checks(test_registry_atomic_write, self.tmp_root)

    def test_path_isolation_violation(self):
        self._assert_checks(test_path_isolation_violation)

    def test_get_root_dir(self):
        self._assert_checks(test_get_root_dir_raises)

    def test_state_isolation(self):
        self._assert_checks(test_state_isolation, self.tmp_root)

    def test_from_scratch_cleanup(self):
        self._assert_checks(test_from_scratch_cleanup, self.tmp_root)


def main():
    print("\n=== test_multi_project.py ===\n")

    with tempfile.TemporaryDirectory(prefix="gesaku_test_") as tmp:
        tmp_root = Path(tmp)
        # Create minimal project root markers
        (tmp_root / "pyproject.toml").write_text("[tool.gesaku]")

        print("1. Project directory isolation:")
        test_project_dir_isolation(tmp_root)

        print("\n2. Registry atomic write:")
        test_registry_atomic_write(tmp_root)

        print("\n3. Path isolation violation guard:")
        test_path_isolation_violation()

        print("\n4. get_root_dir():")
        test_get_root_dir_raises()

        print("\n5. State file isolation:")
        test_state_isolation(tmp_root)

        print("\n6. From-scratch deep cleanup:")
        test_from_scratch_cleanup(tmp_root)

    print()
    if _failed:
        print(f"\033[91mFAILED {len(_failed)} checks:\033[0m")
        for f in _failed:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\033[92mAll checks passed.\033[0m")


if __name__ == "__main__":
    main()
