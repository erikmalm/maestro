"""Bind the native server once and report its actual loopback port to the launcher."""

import argparse
import json
import os
import re
import socket

import uvicorn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=os.environ.get("MAESTRO_PORT", "8765"))
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535; 0 chooses an available port.")
    launch_id = os.environ.get("MAESTRO_LAUNCH_ID", "")
    if not re.fullmatch(r"[a-f0-9]{32}", launch_id):
        parser.error("Start the native server with scripts/start.ps1.")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", args.port))
        port = listener.getsockname()[1]
        # Resolve port 0 before importing the app, including its origin checks.
        os.environ["MAESTRO_PORT"] = str(port)
        from backend.app import DATA_DIR

        address = DATA_DIR / f"server-address-{launch_id}.json"
        stream = address.open("x", encoding="utf-8")
        try:
            with stream:
                json.dump({"port": port, "launch_id": launch_id}, stream)
            server = uvicorn.Server(uvicorn.Config(
                "backend.app:app", host="127.0.0.1", port=port, access_log=False,
            ))
            server.run(sockets=[listener])
            if not server.started:
                raise SystemExit(1)
        finally:
            address.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
