"""RunManager — launches and supervises run_pipeline.py subprocesses for the webui.

Single-user local console: at most one live run per project. Liveness is tracked
in-process via Popen handles and, for runs launched by an earlier bridge
process, via a pid file (projects/<name>/run.json) probed signal-0-style.

The bridge itself is started with:
    uv run uvicorn server:app --app-dir webui --port 8600
so sys.executable is the project venv's python — the same interpreter the
pipeline needs.
"""

import ctypes
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEEDS_DIR = Path(__file__).resolve().parent / "seeds"


def _pid_alive(pid: int) -> bool:
    """Liveness probe that never signals the target.

    os.kill(pid, 0) on Windows maps to TerminateProcess for non-CTRL signals,
    so probe with OpenProcess there instead.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class RunManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._meta: dict[str, dict] = {}

    # -- pid-file plumbing --------------------------------------------------

    def _pid_file(self, project_dir: Path) -> Path:
        return project_dir / "run.json"

    def _read_pid_file(self, project_dir: Path) -> dict | None:
        f = self._pid_file(project_dir)
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_pid_file(self, project_dir: Path, meta: dict | None) -> None:
        f = self._pid_file(project_dir)
        if meta is None:
            f.unlink(missing_ok=True)
            return
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)

    # -- public API ----------------------------------------------------------

    def launch(self, project_dir: Path, cli_args: list[str], env: dict | None = None) -> dict:
        """Spawn run_pipeline.py detached-ish; stdout goes to a captured log.

        Returns the run metadata dict (also persisted to run.json).
        Raises RuntimeError if the project already has a live run.
        """
        name = project_dir.name
        with self._lock:
            if name in self._procs and self._procs[name].poll() is None:
                raise RuntimeError(f"project '{name}' already has a running pipeline (pid {self._procs[name].pid})")
            pid_meta = self._read_pid_file(project_dir)
            if pid_meta and _pid_alive(pid_meta.get("pid", 0)):
                raise RuntimeError(f"project '{name}' already has a running pipeline (pid {pid_meta['pid']})")

            logs_dir = project_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = logs_dir / f"{stamp}_webui.log"

            kwargs: dict = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            log_fh = open(log_path, "w", encoding="utf-8")
            try:
                child_env = {**os.environ, **(env or {})}
                child_env.setdefault("PYTHONUNBUFFERED", "1")
                proc = subprocess.Popen(
                    [sys.executable, str(ROOT / "run_pipeline.py"), *cli_args],
                    cwd=ROOT,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    env=child_env,
                    **kwargs,
                )
            finally:
                log_fh.close()  # Popen holds its own dup of the handle

            meta = {
                "pid": proc.pid,
                "startedAt": datetime.now(timezone.utc).isoformat(),
                "logPath": str(log_path),
                "args": cli_args,
            }
            self._procs[name] = proc
            self._meta[name] = meta
            self._write_pid_file(project_dir, meta)
            return meta

    def stop(self, project_dir: Path) -> bool:
        """Terminate the project's live run, if any. Returns True if stopped."""
        name = project_dir.name
        proc = self._procs.get(name)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            self._write_pid_file(project_dir, None)
            return True
        # run launched by a previous bridge process — kill by pid
        meta = self._read_pid_file(project_dir)
        if meta and _pid_alive(meta.get("pid", 0)):
            pid = meta["pid"]
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, check=False)
            else:
                import signal as _signal
                import errno as _errno
                try:
                    os.kill(pid, _signal.SIGTERM)
                except OSError as e:
                    if e.errno != _errno.ESRCH:
                        raise
            self._write_pid_file(project_dir, None)
            return True
        return False

    def status(self, project_dir: Path) -> dict:
        """{running, pid, startedAt, logPath, args} for the project."""
        name = project_dir.name
        proc = self._procs.get(name)
        if proc is not None:
            running = proc.poll() is None
            meta = self._meta[name]
            if not running:
                meta = {**meta, "exitCode": proc.returncode}
                self._write_pid_file(project_dir, None)
            return {"running": running, **meta}
        meta = self._read_pid_file(project_dir)
        if meta and _pid_alive(meta.get("pid", 0)):
            return {"running": True, **meta}
        return {"running": False}

    def log_path(self, project_dir: Path) -> Path | None:
        """Best log file to tail: the run's captured stdout, else the newest
        pipeline log on disk (covers runs launched from the CLI)."""
        name = project_dir.name
        proc = self._procs.get(name)
        if proc is not None:
            p = Path(self._meta[name]["logPath"])
            if p.exists():
                return p
        meta = self._read_pid_file(project_dir)
        if meta and _pid_alive(meta.get("pid", 0)):
            p = Path(meta["logPath"])
            if p.exists():
                return p
        logs_dir = project_dir / "logs"
        if logs_dir.is_dir():
            candidates = sorted(
                logs_dir.glob("*.log"), key=lambda f: f.stat().st_mtime)
            if candidates:
                return candidates[-1]
        return None


run_manager = RunManager()
