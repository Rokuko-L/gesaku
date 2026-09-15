"""Minimal single-request API smoke check through core.llm's own builder.

Uses whatever provider/endpoint the env resolves to (GESAKU_PROVIDER,
per-role overrides, base URLs, extra headers). Model id via argv[1].

Usage: uv run python tests/check_api_minimal.py [model_id]
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
model = sys.argv[1] if len(sys.argv) > 1 else llm._resolve_model(provider, role)
base_url = llm._resolve_base_url(provider, role)
url_path, headers, payload = llm._build_request(
    provider, model, system=None, prompt="Hello, write one paragraph.",
    max_tokens=1000, temperature=0.3, beta_context=False,
)

print(f"Provider: {provider}")
print(f"URL:      {base_url.rstrip('/')}{url_path}")
print(f"Model:    {model}")
safe_headers = {
    k: (v[:12] + "..." if k.lower() in ("authorization", "x-api-key") and v else v)
    for k, v in headers.items()
}
print(f"Headers:  {safe_headers}")

try:
    with httpx.Client() as client:
        resp = client.post(
            f"{base_url.rstrip('/')}{url_path}",
            headers=headers,
            json=payload,
            timeout=60.0,
        )
        print(f"Status Code: {resp.status_code}")
        if resp.status_code == 200:
            parsed_text = llm.extract_text_from_response(resp, dialect=provider)
            print("=" * 40)
            print("Parsed Text:")
            print(repr(parsed_text))
        else:
            print(f"Error body: {resp.text[:500]}")
except Exception as e:
    print(f"Error occurred: {e}", file=sys.stderr)
