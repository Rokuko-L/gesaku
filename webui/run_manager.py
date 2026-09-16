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

# Command-line probe budget for _pid_is_run (local process query, not a pipeline
# stage — deliberately not a GESAKU_TIMEOUT_* budget).
_PROBE_TIMEOUT = 5


def _pid_alive(pid: int) -> bool:
    """Liveness probe that never signals the target.

    os.kill(pid, 0) on Windows maps to TerminateProcess for non-CTRL signals,
    so probe through the process API there instead.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        # OpenProcess(SYNCHRONIZE) still succeeds for a process that has
        # terminated while its process object is referenced, which would
        # report a finished run as alive. Ask for the exit code instead: a
        # live process reports STILL_ACTIVE.
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _process_start_token(pid: int) -> str | None:
    """Opaque token identifying *this process instance*, not just the pid.

    The only portable way to tell "the same process is still running" from "the
    OS recycled this pid for something else". The kernel's process creation
    time is stable for the life of the process and differs for a reused pid.

    Returns None when the platform cannot tell (macOS has no /proc; a protected
    process refuses the query) — callers must treat None as "unknown".
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            creation = ctypes.c_ulonglong()
            exit_time = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            ok = k32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel), ctypes.byref(user))
            return str(creation.value) if ok else None
        finally:
            k32.CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    # After the parenthesised comm field, field 3 is state — so field 22
    # (starttime, in clock ticks) sits at index 19 of the remainder.
    rest = data.rpartition(b")")[2].split()
    return rest[19].decode() if len(rest) > 19 else None


def _looks_like_run_pipeline(pid: int) -> bool:
    """Command-line probe, for a run.json written before start tokens existed.

    Deliberately best-effort: `wmic` is absent on Windows 11 24H2+, so this can
    only ever say "cannot tell" there. New pid files carry a start token, which
    is checked instead — this path is a compatibility fallback, not the primary
    check, and it is not on any per-tick path.
    """
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}",
                 "get", "CommandLine"],
                capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return True  # no probe available — trust the liveness check
        text = (proc.stdout or "").lower()
        if proc.returncode != 0 or "commandline" not in text:
            return True  # wmic missing/deprecated — trust the liveness check
        return "run_pipeline" in text
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return b"run_pipeline" in fh.read()
    except OSError:
        return True  # /proc unavailable (e.g. macOS) — trust the liveness check


def _pid_is_run(pid: int, start_token: str | None = None) -> bool:
    """True only if `pid` is still the run we recorded.

    A bare "the pid exists" check is unsafe: run.json is never cleaned up on a
    crash or a reboot, so a recycled pid reports the project as permanently
    running — and the next stop would kill an unrelated process.

    With a start token (every run launched since tokens were added) this is
    exact and cheap. Without one, fall back to the command-line probe, which
    degrades to a plain liveness answer where the platform cannot tell.
    """
    if not _pid_alive(pid):
        return False
    if start_token:
        current = _process_start_token(pid)
        if current is None:
            return True  # cannot fingerprint — trust the liveness check
        return current == start_token
    return _looks_like_run_pipeline(pid)


def _kill_tree(pid: int) -> None:
    """Kill a run and its descendants.

    launch() puts each run in its own process group/session; a plain
    terminate() leaves grandchildren (gen_genre_framework etc., spawned via
    subprocess.run) alive and still spending tokens.
    """
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, check=False)
        return
    import errno as _errno
    import signal as _signal
    try:
        pgid = os.getpgid(pid)
        if pgid == pid:
            # The run is its own group leader (launch sets start_new_session),
            # so a group signal is both safe and complete.
            os.killpg(pgid, _signal.SIGTERM)
        else:
            # Not the leader: its group belongs to someone else (possibly the
            # bridge itself). Never signal the group from here.
            os.kill(pid, _signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    except OSError as e:
        if e.errno != _errno.ESRCH:
            raise


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
            if pid_meta and _pid_is_run(pid_meta.get("pid", 0), pid_meta.get("startToken")):
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
                # Identifies this process instance, so a recycled pid can never
                # be mistaken for this run (or be signalled by stop()).
                "startToken": _process_start_token(proc.pid),
                "startedAt": datetime.now(timezone.utc).isoformat(),
                "logPath": str(log_path),
                "args": cli_args,
            }
            self._procs[name] = proc
            self._meta[name] = meta
            self._write_pid_file(project_dir, meta)
            return meta

    def stop(self, project_dir: Path) -> bool:
        """Terminate the project's live run and its process tree."""
        name = project_dir.name
        proc = self._procs.get(name)
        if proc is not None and proc.poll() is None:
            _kill_tree(proc.pid)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            self._write_pid_file(project_dir, None)
            return True
        # run launched by a previous bridge process — kill by pid
        meta = self._read_pid_file(project_dir)
        if meta and _pid_is_run(meta.get("pid", 0), meta.get("startToken")):
            _kill_tree(meta["pid"])
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
        if meta and _pid_is_run(meta.get("pid", 0), meta.get("startToken")):
            return {"running": True, **meta}
        if meta:
            # Stale or recycled pid — drop the file so the project is not
            # reported as running forever (which would block Start).
            self._write_pid_file(project_dir, None)
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
        if meta and _pid_is_run(meta.get("pid", 0), meta.get("startToken")):
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


# The single supervisor instance. Both the console bridge (via deps) and the
# `gesaku` CLI import this name; a second RunManager elsewhere would not see
# the in-process handles this one holds.
run_manager = RunManager()
