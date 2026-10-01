"""Serve browser tests with a temporary workspace outside the checkout."""

import os
from pathlib import Path
import sys
import tempfile

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
with tempfile.TemporaryDirectory(prefix="maestro-browser-") as directory:
    assert Path(directory).resolve().parent == Path(tempfile.gettempdir()).resolve()
    os.environ["MAESTRO_DATA_DIR"] = directory
    os.environ["MAESTRO_PORT"] = "8777"
    os.environ.pop("OPENAI_API_KEY", None)  # Browser checks must never inherit a paid provider credential.
    uvicorn.run("backend.app:app", host="127.0.0.1", port=8777, log_level="error", access_log=False)
