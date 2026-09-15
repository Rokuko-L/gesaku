"""Gesaku CLI — operator console + agent-facing pipeline control.

    gesaku                          # web UI (built dist) + API on :8600
    gesaku --dev                    # vite HMR + API
    gesaku run --project noir \\
      --genre "Cyberpunk Noir" --notes premise.txt --json
    gesaku status --json
    gesaku logs -f
    gesaku stop

Agent mode: `run`/`status`/`logs`/`stop` use the same RunManager + run.json
contract as the webui, so the console shows CLI-launched runs unchanged.
`--json` emits JSONL events for machine consumers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path


def _find_root() -> Path:
    env = os.environ.get("GESAKU_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if (root / "webui" / "server.py").is_file():
            return root
        raise SystemExit(f"GESAKU_ROOT={root} does not look like a gesaku repo")
    here = Path(__file__).resolve().parent
    if (here / "webui" / "server.py").is_file():
        return here
    cwd = Path.cwd().resolve()
    if (cwd / "webui" / "server.py").is_file():
        return cwd
    raise SystemExit(
        "Run from the gesaku repo root, or set GESAKU_ROOT to that directory."
    )


def _bootstrap(root: Path) -> None:
    for p in (root, root / "webui"):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _free_port(host: str, port: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, port))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((host, port))
                return True
            except OSError:
                time.sleep(0.15)
    return False


def _npm_cmd() -> list[str]:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    return [npm] if npm else []


def _spawn(args: list[str], cwd: Path | None = None, env: dict | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


# -- console (web UI) ----------------------------------------------------------

def _serve_static(root: Path, host: str, port: int) -> subprocess.Popen:
    code = f"""
import sys
from pathlib import Path
root = Path({str(root)!r})
sys.path[:0] = [str(root), str(root / "webui")]
from fastapi.staticfiles import StaticFiles
from server import app
dist = root / "webui" / "frontend" / "dist"
app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")
import uvicorn
uvicorn.run(app, host={host!r}, port={port}, log_level="warning")
"""
    return _spawn([sys.executable, "-c", code], cwd=root)


def _serve_api(root: Path, host: str, api_port: int) -> subprocess.Popen:
    return _spawn(
        [
            sys.executable, "-m", "uvicorn", "server:app",
            "--app-dir", str(root / "webui"),
            "--host", host, "--port", str(api_port),
            "--log-level", "warning",
        ],
        cwd=root,
    )


def _serve_vite(root: Path, host: str, ui_port: int) -> subprocess.Popen | None:
    npm = _npm_cmd()
    frontend = root / "webui" / "frontend"
    if not npm or not (frontend / "node_modules" / "vite").exists():
        return None
    env = os.environ.copy()
    env["VITE_HOST"] = host
    env["VITE_PORT"] = str(ui_port)
    return _spawn(
        [*npm, "run", "dev", "--", "--host", host, "--port", str(ui_port), "--strictPort"],
        cwd=frontend, env=env,
    )


def cmd_ui(args: argparse.Namespace) -> int:
    root = _find_root()
    dist = root / "webui" / "frontend" / "dist" / "index.html"
    use_dev = args.dev or (not args.static and not dist.is_file())
    if args.static:
        use_dev = False
    if not use_dev and not dist.is_file():
        print("No webui/frontend/dist — run `npm run build` in webui/frontend, or pass --dev.", file=sys.stderr)
        return 1

    api_port = args.port
    try:
        _free_port(args.host, api_port)
    except OSError:
        print(f"Port {api_port} busy — pass --port", file=sys.stderr)
        return 1

    procs: list[subprocess.Popen] = []
    try:
        if use_dev:
            ui_port = args.ui_port
            try:
                _free_port(args.host, ui_port)
            except OSError:
                print(f"Port {ui_port} busy — pass --ui-port", file=sys.stderr)
                return 1
            api = _serve_api(root, args.host, api_port)
            procs.append(api)
            if not _wait_for_port(args.host, api_port, timeout=20):
                print(f"API did not come up on {args.host}:{api_port}", file=sys.stderr)
                return 1
            vite = _serve_vite(root, args.host, ui_port)
            if vite is None:
                print("vite not available (npm or node_modules missing) — try without --dev", file=sys.stderr)
                return 1
            procs.append(vite)
            if not _wait_for_port(args.host, ui_port, timeout=40):
                print(f"vite did not come up on {args.host}:{ui_port}", file=sys.stderr)
                return 1
            url = f"http://{args.host}:{ui_port}"
        else:
            api = _serve_static(root, args.host, api_port)
            procs.append(api)
            if not _wait_for_port(args.host, api_port, timeout=20):
                print(f"Console did not come up on {args.host}:{api_port}", file=sys.stderr)
                return 1
            url = f"http://{args.host}:{api_port}"

        print(f"gesaku console  {url}")
        print(f"  API docs      http://{args.host}:{api_port}/api/docs")
        print("Ctrl+C to stop.")
        if not args.no_open:
            webbrowser.open(url)

        while True:
            for p in procs:
                rc = p.poll()
                if rc is not None:
                    print(f"process exited (code {rc})", file=sys.stderr)
                    return rc or 1
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\nshutting down…")
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


# -- agent mode ----------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(event: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    else:
        kind = event.get("type", "log")
        msg = event.get("msg") or event.get("line") or ""
        extra = ""
        if kind == "phase":
            extra = f"  [{event.get('phase', '')}]"
        elif kind == "state":
            bits = []
            if event.get("phase"):
                bits.append(f"phase={event['phase']}")
            if event.get("foundation_score") is not None:
                bits.append(f"foundation={event['foundation_score']}")
            if event.get("chapters_drafted") is not None:
                bits.append(f"ch={event['chapters_drafted']}")
            if event.get("novel_score") is not None:
                bits.append(f"novel={event['novel_score']}")
            extra = f"  ({', '.join(bits)})" if bits else ""
        elif kind == "error":
            print(f"[error] {msg}", file=sys.stderr, flush=True)
            return
        elif kind == "warn":
            print(f"[warn] {msg}", file=sys.stderr, flush=True)
            return
        prefix = {"fatal": "!!!"}.get(kind, "")
        if kind == "phase":
            print(f"{msg}{extra}", flush=True)
        elif prefix:
            print(f"{prefix} {msg}{extra}", flush=True)
        else:
            print(f"{msg}{extra}", flush=True)


def _classify_line(line: str) -> dict:
    s = line.lstrip("﻿").strip()
    upper = s.upper()
    if "PHASE " in upper and ("FOUNDATION" in upper or "DRAFTING" in upper
                              or "REVISION" in upper or "EXPORT" in upper
                              or "OPUS" in upper):
        return {"type": "phase", "msg": s, "phase": _phase_from_banner(s)}
    if "FATAL" in upper or upper.startswith("FAIL") or "FATAL ERROR" in upper:
        return {"type": "fatal", "msg": s}
    if "ERROR" in upper:
        return {"type": "error", "msg": s}
    if "WARN" in upper:
        return {"type": "warn", "msg": s}
    if "SCORE" in upper or "PASSED" in upper or "PLATEAU" in upper:
        return {"type": "score", "msg": s}
    return {"type": "log", "line": s}


def _phase_from_banner(s: str) -> str:
    u = s.upper()
    for name in ("FOUNDATION", "DRAFTING", "REVISION", "EXPORT"):
        if name in u:
            return name.lower()
    return ""


def _project_dir(root: Path, name: str) -> Path:
    d = root / "projects" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_state(project_dir: Path) -> dict:
    f = project_dir / "state.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _state_snapshot(state: dict) -> dict:
    keys = (
        "phase", "current_focus", "foundation_score", "novel_score",
        "chapters_drafted", "chapters_total", "revision_cycle",
    )
    return {k: state.get(k) for k in keys if k in state or state.get(k) is not None}


def _tail(
    log_path: Path,
    *,
    as_json: bool,
    follow: bool,
    project_dir: Path | None,
    state_poll: float = 2.0,
) -> int:
    """Stream log lines (+ optional state diffs) until the run ends."""
    offset = 0
    if log_path.is_file():
        # skip historical content when attaching mid-run unless file is brand new
        offset = 0  # agent wants full context from this log's start
    last_state: dict = {}
    last_state_t = 0.0
    buf = b""
    exit_code = 0
    seen_end = False

    while True:
        if log_path.is_file():
            with open(log_path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
                offset = fh.tell()
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    try:
                        line = raw.decode("utf-8", errors="replace")
                    except Exception:
                        line = raw.decode("latin-1", errors="replace")
                    if line.strip():
                        _emit(_classify_line(line), as_json)
                        if "FATAL ERROR" in line.upper():
                            exit_code = 1

        if project_dir and time.time() - last_state_t >= state_poll:
            last_state_t = time.time()
            snap = _state_snapshot(_read_state(project_dir))
            if snap and snap != last_state:
                changed = {k: v for k, v in snap.items() if last_state.get(k) != v}
                if changed:
                    _emit({"type": "state", "ts": _now(), **snap}, as_json)
                last_state = snap

        if not follow:
            break

        # run.json cleared or pid dead → drain remaining log then exit
        if project_dir is not None:
            run_json = project_dir / "run.json"
            if not run_json.exists() and not seen_end and offset > 0:
                # give the writer a moment to flush
                time.sleep(0.3)
                seen_end = True
            elif not run_json.exists() and seen_end:
                # final drain
                with open(log_path, "rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                if chunk:
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.decode("utf-8", errors="replace")
                        if line.strip():
                            _emit(_classify_line(line), as_json)
                break

        time.sleep(0.25)

    _emit({"type": "done", "ts": _now(), "exitCode": exit_code, "logPath": str(log_path)}, as_json)
    return exit_code


def cmd_run(args: argparse.Namespace, passthrough: list[str]) -> int:
    root = _find_root()
    _bootstrap(root)
    from webui.run_manager import run_manager

    project = args.project
    project_dir = _project_dir(root, project)

    status = run_manager.status(project_dir)
    if status.get("running"):
        _emit({
            "type": "error",
            "msg": f"project '{project}' already running (pid {status.get('pid')}) — use gesaku logs/stop",
        }, args.json)
        return 1

    cli_args = list(passthrough)
    if "--project" not in cli_args:
        cli_args = ["--project", project, *cli_args]

    try:
        meta = run_manager.launch(project_dir, cli_args)
    except RuntimeError as e:
        _emit({"type": "error", "msg": str(e)}, args.json)
        return 1

    _emit({
        "type": "started",
        "ts": _now(),
        "project": project,
        "pid": meta.get("pid"),
        "logPath": meta.get("logPath"),
        "args": meta.get("args"),
        "console": "gesaku ui  (same run appears in the operator console)",
    }, args.json)

    log_path = Path(meta["logPath"])
    try:
        return _tail(log_path, as_json=args.json, follow=not args.detach, project_dir=project_dir)
    except KeyboardInterrupt:
        if args.json:
            _emit({"type": "interrupted", "ts": _now(), "note": "run left alive; gesaku stop to kill"}, True)
        else:
            print("\ninterrupted — run left alive; `gesaku stop` to kill", file=sys.stderr)
        return 130


def cmd_status(args: argparse.Namespace) -> int:
    root = _find_root()
    _bootstrap(root)
    from webui.run_manager import run_manager

    project_dir = _project_dir(root, args.project)
    st = run_manager.status(project_dir)
    state = _read_state(project_dir)
    payload = {
        "type": "status",
        "ts": _now(),
        "project": args.project,
        "running": bool(st.get("running")),
        "pid": st.get("pid"),
        "startedAt": st.get("startedAt"),
        "logPath": st.get("logPath"),
        "exitCode": st.get("exitCode"),
        "state": _state_snapshot(state),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    else:
        flag = "RUNNING" if payload["running"] else "idle"
        print(f"{args.project}: {flag}" + (f"  pid={payload['pid']}" if payload["pid"] else ""))
        snap = payload["state"] or {}
        if snap:
            print("  " + ", ".join(f"{k}={v}" for k, v in snap.items() if v is not None))
        if payload.get("logPath"):
            print(f"  log: {payload['logPath']}")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    root = _find_root()
    _bootstrap(root)
    from webui.run_manager import run_manager

    project_dir = _project_dir(root, args.project)
    log_path = run_manager.log_path(project_dir)
    if log_path is None or not Path(log_path).exists():
        _emit({"type": "error", "msg": f"no log for project '{args.project}'"}, args.json)
        return 1
    return _tail(Path(log_path), as_json=args.json, follow=args.follow, project_dir=project_dir)


def cmd_stop(args: argparse.Namespace) -> int:
    root = _find_root()
    _bootstrap(root)
    from webui.run_manager import run_manager

    project_dir = _project_dir(root, args.project)
    ok = run_manager.stop(project_dir)
    payload = {"type": "stopped", "ts": _now(), "project": args.project, "ok": ok}
    if args.json:
        print(json.dumps(payload), flush=True)
    else:
        print(f"{args.project}: {'stopped' if ok else 'not running'}")
    return 0 if ok else 1


# -- parser --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"ui", "run", "status", "logs", "stop"}

    if not argv or argv[0] not in commands:
        # `gesaku` / `gesaku --dev` → console
        parser = argparse.ArgumentParser(prog="gesaku", description="Gesaku operator console + agent CLI.")
        _add_ui_args(parser)
        return cmd_ui(parser.parse_args(argv))

    cmd, rest = argv[0], argv[1:]

    if cmd == "ui":
        parser = argparse.ArgumentParser(prog="gesaku ui", description="Launch the operator console.")
        _add_ui_args(parser)
        return cmd_ui(parser.parse_args(rest))

    if cmd == "run":
        parser = argparse.ArgumentParser(
            prog="gesaku run",
            description="Launch a pipeline run (same supervisor as the webui). "
                        "Unknown flags are passed to run_pipeline.py. Use -- to force.",
        )
        parser.add_argument("--project", "-p", default=os.environ.get("GESAKU_PROJECT", "default"))
        parser.add_argument("--json", action="store_true", help="JSONL events on stdout")
        parser.add_argument("--detach", action="store_true", help="start and return; do not tail")
        ns, pipe_argv = parser.parse_known_args(rest)
        return cmd_run(ns, pipe_argv)

    if cmd == "status":
        parser = argparse.ArgumentParser(prog="gesaku status")
        parser.add_argument("--project", "-p", default=os.environ.get("GESAKU_PROJECT", "default"))
        parser.add_argument("--json", action="store_true")
        return cmd_status(parser.parse_args(rest))

    if cmd == "logs":
        parser = argparse.ArgumentParser(prog="gesaku logs")
        parser.add_argument("--project", "-p", default=os.environ.get("GESAKU_PROJECT", "default"))
        parser.add_argument("--json", action="store_true")
        parser.add_argument("-f", "--follow", action="store_true")
        return cmd_logs(parser.parse_args(rest))

    if cmd == "stop":
        parser = argparse.ArgumentParser(prog="gesaku stop")
        parser.add_argument("--project", "-p", default=os.environ.get("GESAKU_PROJECT", "default"))
        parser.add_argument("--json", action="store_true")
        return cmd_stop(parser.parse_args(rest))

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


def _add_ui_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dev", action="store_true", help="vite dev server instead of built dist")
    parser.add_argument("--static", action="store_true", help="force serving webui/frontend/dist")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8600)
    parser.add_argument("--ui-port", type=int, default=5175)
    parser.add_argument("--no-open", action="store_true")


if __name__ == "__main__":
    raise SystemExit(main())
