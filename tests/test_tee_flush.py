"""Regression test: the Tee'd pipeline log must be watchable live.

The wrapped handle is a plain `open()` (block-buffered). If Tee stops
flushing, the on-disk log sits at 0B while the run works — the exact
symptom this guards against.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import paths  # noqa: E402
from pipeline.pipeline_infra import Tee  # noqa: E402


class TeeFlushTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="gesaku_tee_")
        self.log = Path(self._tmp.name) / "run.log"
        self._saved_stdout = sys.stdout

    def tearDown(self):
        sys.stdout = self._saved_stdout
        self._tmp.cleanup()

    def test_log_is_readable_before_close(self):
        class _Null:
            def write(self, data):
                pass

            def flush(self):
                pass

            def isatty(self):
                return False

            def fileno(self):
                raise OSError("no fileno")

        fh = open(self.log, "w", encoding="utf-8", buffering=1)
        tee = Tee(fh, _Null())
        tee.write("[12:00:00] generating world bible...\n")

        # Nothing closed yet — the line must already be on disk.
        self.assertEqual(
            self.log.read_text(encoding="utf-8"),
            "[12:00:00] generating world bible...\n",
        )
        fh.close()

    def test_write_survives_a_dead_log_handle(self):
        class _Null:
            def write(self, data):
                pass

            def flush(self):
                pass

            def isatty(self):
                return False

            def fileno(self):
                raise OSError("no fileno")

        fh = open(self.log, "w", encoding="utf-8")
        fh.close()  # writes now raise ValueError, not OSError
        tee = Tee(fh, _Null())
        try:
            tee.write("still running\n")
        except Exception as e:  # pragma: no cover - regression guard
            self.fail(f"Tee.write raised with a dead log handle: {e!r}")


if __name__ == "__main__":
    unittest.main()
