"""`uv run offprint` — start the server."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="offprint", description="Serve the Offprint UI and API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    parser.add_argument(
        "--dev", action="store_true", help="Reload + point CORS at the Vite dev server"
    )
    args = parser.parse_args()

    uvicorn.run(
        "offprint.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload or args.dev,
        log_level="info",
    )


if __name__ == "__main__":
    main()
