"""`uv run bpc-web` — start the server."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bpc-web", description="Serve the bpc-fetch web UI and API"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    parser.add_argument(
        "--dev", action="store_true", help="Reload + point CORS at the Vite dev server"
    )
    args = parser.parse_args()

    uvicorn.run(
        "bpc_web.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload or args.dev,
        log_level="info",
    )


if __name__ == "__main__":
    main()
