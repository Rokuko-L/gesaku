import sys
from pathlib import Path

root = Path(r"D:\Tugas\LLM\autonovel")
sys.path[:0] = [str(root), str(root / "webui")]

from fastapi.staticfiles import StaticFiles
from server import app

dist = root / "webui" / "frontend" / "dist"
app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")

import uvicorn

uvicorn.run(app, host="127.0.0.1", port=8600, log_level="warning")
