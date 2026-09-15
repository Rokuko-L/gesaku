"""Manual API smoke check against the resolved provider endpoint.

Reads the same config the pipeline uses (GESAKU_PROVIDER, per-role
overrides, base URLs, extra headers) and fires real requests through
core.llm's own request builder — what this validates is exactly what a
run will send.

Usage: uv run python tests/check_api.py [model_id ...]
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from core import llm

role = "writer"
provider = llm.resolve_provider(role)
models_to_test = sys.argv[1:] or [llm._resolve_model(provider, role)]
base_url = llm._resolve_base_url(provider, role)

print(f"Provider: {provider}")
print(f"URL:      {base_url}")

for model in models_to_test:
    url_path, headers, payload = llm._build_request(
        provider, model, system=None, prompt="Hi",
        max_tokens=1000, temperature=0.3, beta_context=False,
    )
    safe_headers = {
        k: (v[:12] + "..." if k.lower() in ("authorization", "x-api-key") and v else v)
        for k, v in headers.items()
    }
    print(f"\nTesting model: {model}")
    print(f"Headers: {safe_headers}")
    try:
        with httpx.Client() as client:
            resp = client.post(
                f"{base_url.rstrip('/')}{url_path}",
                headers=headers,
                json=payload,
                timeout=30,
            )
            print(f"Status: {resp.status_code}")
            if resp.status_code == 200:
                text = llm.extract_text_from_response(resp, dialect=provider)
                print(f"Success! Response: {text[:200]}")
            else:
                print(f"Error: {resp.text[:300]}")
    except Exception as e:
        print(f"Exception: {e}")
    print("-" * 40)
