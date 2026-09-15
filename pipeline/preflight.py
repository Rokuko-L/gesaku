"""Pre-flight checks — run before any LLM call.

Fails fast on a dead proxy or a model name that 404s, so a doomed launch
does not burn ten minutes before its first real error.
"""

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import os
import sys

import httpx

from core import paths
from core.genre import load_genre



# ---------------------------------------------------------------------------
# Sanity check (pre-flight before any LLM call)
# ---------------------------------------------------------------------------

def sanity_check(args):
    """Run pre-flight checks. Exit 1 on critical failures."""
    ok = True
    notes_provided = bool(args.notes)
    root_dir = paths.get_root_dir()

    # 1. .env exists
    if not (root_dir / ".env").exists():
        print("FAIL: .env not found — create one from .env.example", file=sys.stderr)
        ok = False

    # 2. API key loaded for the resolved provider (load_dotenv already called).
    # First-party endpoints always need a key -> FAIL; custom gateways
    # (OpenRouter/LiteLLM/Ollama) may be keyless -> WARN only.
    from core.llm import resolve_provider, KEY_ENV_VARS, BASE_URL_ENV_VARS, DEFAULT_BASE_URLS
    provider = resolve_provider("writer")
    api_key = os.getenv(KEY_ENV_VARS[provider], "")
    base = os.getenv(BASE_URL_ENV_VARS[provider], DEFAULT_BASE_URLS[provider])
    if not api_key:
        msg = f"FAIL: {KEY_ENV_VARS[provider]} not set in .env"
        if base == DEFAULT_BASE_URLS[provider]:
            print(msg, file=sys.stderr)
            ok = False
        else:
            print(f"WARN: {msg.replace('FAIL', '')} — continuing anyway (custom endpoint may be keyless)", file=sys.stderr)

    # 3. LLM proxy reachable + model name known. Fail fast: a doomed
    # launch otherwise costs 10+ minutes before the first call errors.
    try:
        from core.llm import _resolve_model, _resolve_base_url
        model = _resolve_model(provider, "writer")
        try:
            probe = httpx.post(
                f"{_resolve_base_url(provider, 'writer')}"
                f"{'/v1/messages' if provider == 'anthropic' else '/chat/completions'}",
                headers={"content-type": "application/json"},
                json={"model": model, "max_tokens": 1,
                      "messages": [{"role": "user", "content": "ping"}]},
                timeout=15,
            )
            if probe.status_code == 404:
                print(f"FAIL: proxy reachable but model '{model}' not found (404) — "
                      f"check GESAKU_WRITER_MODEL", file=sys.stderr)
                ok = False
        except Exception as e:
            print(f"FAIL: LLM proxy unreachable ({e}) — refusing to launch a doomed run",
                  file=sys.stderr)
            ok = False
    except Exception as e:
        print(f"FAIL: provider config broken ({e})", file=sys.stderr)
        ok = False

    # 4. At least one of seed.txt or --notes exists
    if not (root_dir / "seed.txt").exists() and not paths.get_seed_path().exists() and not notes_provided:
        print("FAIL: provide --notes or place a seed.txt in the project root or project folder", file=sys.stderr)
        ok = False

    # 5. Genre is specified (skip if already configured from a previous run)
    if not args.genre and not os.getenv("GESAKU_GENRE"):
        if not paths.get_active_genre_path().exists() and not (root_dir / "active_genre.json").exists():
            print("FAIL: provide --genre or set GESAKU_GENRE in .env", file=sys.stderr)
            ok = False

    # --- Warnings (non-fatal) ---

    # Chapters parseable
    if args.chapters:
        try:
            int(args.chapters)
        except ValueError:
            descriptive = {"short", "story", "novella", "novelette", "epic", "saga"}
            if not any(w in args.chapters.lower() for w in descriptive):
                print(f"WARN: --chapters '{args.chapters}' looks unusual", file=sys.stderr)

    # active_genre.json valid if present
    active_path = paths.get_active_genre_path()
    if not active_path.exists():
        active_path = root_dir / "active_genre.json"
    if active_path.exists():
        try:
            json.loads(active_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"WARN: {active_path.name} is corrupted — delete it and re-run", file=sys.stderr)

    # state.json valid if present and not in --from-scratch mode
    state_path = paths.get_state_path()
    if state_path.exists() and not args.from_scratch:
        try:
            json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("WARN: state.json is corrupted — use --from-scratch to reset", file=sys.stderr)

    if not ok:
        sys.exit(1)
