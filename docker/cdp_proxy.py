#!/usr/bin/env python3
"""Bridge a containerised Chromium DevTools endpoint to other containers.

Three problems to solve, all caused by Chromium binding to loopback:

1. It listens on 127.0.0.1 even when told `--remote-debugging-address=0.0.0.0`.
2. It answers 500 to requests whose Host header isn't a loopback name, which is
   what a peer container sends (`browser:9222`).
3. `/json/version` returns a WebSocket URL containing `127.0.0.1:<port>`, which
   the *client* then tries to dial inside its own container.

So: a small HTTP-aware proxy. Non-upgrade requests are buffered and their
responses rewritten to point back at the address the client used; WebSocket
upgrades are streamed byte-for-byte after the Host fix.
"""

from __future__ import annotations

import asyncio
import re
import sys

# Overridden from argv when run as a script; importable defaults for tests.
LISTEN_PORT = 9222
TARGET_PORT = 9223

HOST_HEADER = re.compile(rb"(?im)^host:[^\r\n]*\r\n")
CONTENT_LENGTH = re.compile(rb"(?im)^content-length:\s*(\d+)\s*\r\n")
READ_LIMIT = 4 * 1024 * 1024


def rewrite_host(head: bytes, target_port: int) -> bytes:
    replacement = f"Host: 127.0.0.1:{target_port}\r\n".encode()
    if HOST_HEADER.search(head):
        return HOST_HEADER.sub(replacement, head, count=1)
    return head


def client_host_of(head: bytes) -> str:
    match = re.search(rb"(?im)^host:\s*([^\r\n]+)\r\n", head)
    return match.group(1).decode().strip() if match else "127.0.0.1"


async def read_head(reader: asyncio.StreamReader) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = await reader.read(4096)
        if not chunk:
            break
        data += chunk
    return data


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle_websocket(head: bytes, local_reader, local_writer, remote_reader, remote_writer) -> None:
    remote_writer.write(rewrite_host(head, TARGET_PORT))
    await remote_writer.drain()
    await asyncio.gather(
        pipe(local_reader, remote_writer),
        pipe(remote_reader, local_writer),
    )


async def handle_http(
    head: bytes, client_host: str, local_reader, local_writer, remote_reader, remote_writer
) -> None:
    """Buffer a plain HTTP exchange so the body can be rewritten."""
    remote_writer.write(rewrite_host(head, TARGET_PORT))
    await remote_writer.drain()

    response = await read_head(remote_reader)
    if not response:
        local_writer.close()
        return
    resp_head, _, body = response.partition(b"\r\n\r\n")

    if b"transfer-encoding: chunked" in resp_head.lower():
        # Can't rewrite a stream we can't measure; pass it through untouched.
        local_writer.write(response)
        await local_writer.drain()
        await pipe(remote_reader, local_writer)
        return

    match = CONTENT_LENGTH.search(resp_head)
    expected = int(match.group(1)) if match else 0
    while len(body) < expected and len(body) < READ_LIMIT:
        chunk = await remote_reader.read(65536)
        if not chunk:
            break
        body += chunk

    # Point the DevTools URLs back at the address the client dialled.
    body = body.replace(f"127.0.0.1:{TARGET_PORT}".encode(), client_host.encode())
    body = body.replace(f"localhost:{TARGET_PORT}".encode(), client_host.encode())
    if match:
        resp_head = CONTENT_LENGTH.sub(
            f"Content-Length: {len(body)}\r\n".encode(), resp_head, count=1
        )

    local_writer.write(resp_head + b"\r\n\r\n" + body)
    await local_writer.drain()
    local_writer.close()
    remote_writer.close()


async def handle(local_reader: asyncio.StreamReader, local_writer: asyncio.StreamWriter) -> None:
    try:
        remote_reader, remote_writer = await asyncio.open_connection("127.0.0.1", TARGET_PORT)
    except Exception:
        local_writer.close()
        return

    try:
        head = await read_head(local_reader)
        if not head:
            local_writer.close()
            return

        if b"upgrade: websocket" in head.lower():
            await handle_websocket(head, local_reader, local_writer, remote_reader, remote_writer)
        else:
            await handle_http(
                head,
                client_host_of(head),
                local_reader,
                local_writer,
                remote_reader,
                remote_writer,
            )
    except Exception:
        try:
            local_writer.close()
        except Exception:
            pass
        try:
            remote_writer.close()
        except Exception:
            pass


async def main() -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", LISTEN_PORT)
    print(
        f"cdp-proxy: 0.0.0.0:{LISTEN_PORT} -> 127.0.0.1:{TARGET_PORT} "
        "(Host + URL rewriting)",
        flush=True,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        LISTEN_PORT = int(sys.argv[1])
    if len(sys.argv) > 2:
        TARGET_PORT = int(sys.argv[2])
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
