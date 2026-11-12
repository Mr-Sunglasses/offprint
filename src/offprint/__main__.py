"""`offprint` — serve the app, or manage publisher sessions."""

from __future__ import annotations

import argparse
import asyncio
import sys


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run(
        "offprint.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload or args.dev,
        log_level="info",
    )


async def _capture_session(domain: str, timeout_seconds: int) -> dict:
    from playwright.async_api import async_playwright

    from .session import save_session

    domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(locale="en-US")
        page = await context.new_page()
        try:
            await page.goto(f"https://{domain}/", wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            print(f"could not open https://{domain}/: {exc}", file=sys.stderr)

        print(
            f"\nLog in to {domain} in the window that just opened.\n"
            "When the article pages render in full, come back here and press Enter.\n"
            f"(Waiting up to {timeout_seconds // 60} minutes.)\n"
        )
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(loop.run_in_executor(None, input), timeout=timeout_seconds)
        except TimeoutError:
            print("timed out waiting for Enter — saving whatever the browser has.", file=sys.stderr)
        except (EOFError, KeyboardInterrupt):
            print("cancelled.", file=sys.stderr)
            await browser.close()
            raise SystemExit(1) from None

        state = await context.storage_state()
        await browser.close()

    saved = save_session(domain, state.get("cookies", []), origins=state.get("origins", []))
    return saved


def _session_command(args: argparse.Namespace) -> int:
    from .session import delete_session, list_sessions, parse_cookie_header, save_session

    command = args.session_command
    if command == "list":
        rows = list_sessions()
        if not rows:
            print("No sessions stored.")
            return 0
        for row in rows:
            state = "expired" if row["expired"] else ("stale" if row["stale"] else "ok")
            print(f"{row['domain']:28} {row['cookies']:3} cookies  {row['age_days']:>5}d  {state}")
        return 0

    if command == "remove":
        ok = delete_session(args.domain)
        print("removed." if ok else "no session for that domain.")
        return 0 if ok else 1

    if command == "add":
        if args.cookie:
            cookies = parse_cookie_header(args.cookie, args.domain)
            if not cookies:
                print("could not parse that cookie string.", file=sys.stderr)
                return 1
            saved = save_session(args.domain, cookies)
            print(f"saved {saved['cookies']} cookies for {saved['domain']}.")
            return 0

        try:
            saved = asyncio.run(_capture_session(args.domain, args.timeout))
        except SystemExit as exc:
            return int(exc.code or 1)
        print(f"saved {saved['cookies']} cookies for {saved['domain']}.")
        print("Sessions are stored 0600 under OFFPRINT_SESSION_DIR and never leave this machine.")
        return 0

    print("usage: offprint session {add|list|remove}", file=sys.stderr)
    return 2


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="offprint",
        description="Serve the Offprint UI and API, or manage publisher sessions",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    parser.add_argument(
        "--dev", action="store_true", help="Reload + point CORS at the Vite dev server"
    )

    sub = parser.add_subparsers(dest="command")
    session = sub.add_parser("session", help="Manage publisher sessions (your own subscriptions)")
    session_sub = session.add_subparsers(dest="session_command")

    add = session_sub.add_parser("add", help="Log in once in a real browser and save the cookies")
    add.add_argument("domain")
    add.add_argument("--timeout", type=int, default=300, help="Seconds to wait for you to log in")
    add.add_argument(
        "--cookie",
        default="",
        help="Paste a `Cookie:` header instead of opening a browser (useful on a server)",
    )

    session_sub.add_parser("list", help="Show stored sessions")
    remove = session_sub.add_parser("remove", help="Delete a stored session")
    remove.add_argument("domain")

    args = parser.parse_args()
    if args.command == "session":
        raise SystemExit(_session_command(args))
    _serve(args)


if __name__ == "__main__":
    main()
