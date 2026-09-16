#!/usr/bin/env python3
"""RunManager — liveness and supervision, offline.

Covers the two ways a run's bookkeeping goes wrong: a recycled pid reported as
a live run forever (which blocks Start and aims `stop` at an unrelated
process), and a tree-kill that only kills the direct child.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webui"))

import run_manager as rm  # noqa: E402


class RunManagerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gesaku_runmgr_")
        self.project = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_status_without_pid_file_is_not_running(self):
        self.assertFalse(rm.RunManager().status(self.project)["running"])

    def test_stop_without_a_run_returns_false(self):
        self.assertFalse(rm.RunManager().stop(self.project))

    def test_dead_pid_is_not_our_run(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.assertFalse(rm._pid_is_run(proc.pid))

    def test_live_process_with_a_foreign_token_is_not_our_run(self):
        """The check that authorizes stop() must reject a LIVE pid that is not
        the process we recorded — that is what keeps a recycled pid from being
        signalled. This exercises the token path; a dead-pid test never
        reaches it."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(rm._pid_alive(proc.pid))
            self.assertFalse(rm._pid_is_run(proc.pid, "some-other-process"),
                             "a live pid with a mismatched token is not our run")
        finally:
            proc.kill()
            proc.wait()

    def test_recorded_token_identifies_the_live_process(self):
        """And the happy path: the token we would record is recognized while
        the run is alive. Skipped where the platform cannot fingerprint."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            token = rm._process_start_token(proc.pid)
            if token is None:
                self.skipTest("platform cannot fingerprint a process")
            self.assertEqual(token, rm._process_start_token(proc.pid),
                             "the token must be stable across calls")
            self.assertTrue(rm._pid_is_run(proc.pid, token))
        finally:
            proc.kill()
            proc.wait()

    def test_nonpositive_pids_are_never_live(self):
        for pid in (0, -1):
            self.assertFalse(rm._pid_alive(pid))
            self.assertFalse(rm._pid_is_run(pid))

    def test_stale_pid_file_is_cleared_so_the_project_unblocks(self):
        """A finished run's pid file must not report 'running' forever."""
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        pid_file = self.project / "run.json"
        pid_file.write_text(
            json.dumps({"pid": proc.pid, "logPath": "x.log"}), encoding="utf-8")

        status = rm.RunManager().status(self.project)
        self.assertFalse(status["running"])
        self.assertFalse(pid_file.exists(),
                         "a stale pid file must be dropped, not kept")

    def test_kill_tree_ignores_nonpositive_pids(self):
        # Must be a no-op rather than signalling the whole process group.
        rm._kill_tree(0)
        rm._kill_tree(-1)


if __name__ == "__main__":
    unittest.main()
