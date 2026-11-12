"""The container CDP bridge rewrites Host headers and DevTools URLs."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "cdp_proxy", Path(__file__).resolve().parent.parent / "docker" / "cdp_proxy.py"
)
cdp_proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cdp_proxy)


def test_host_header_is_rewritten():
    head = b"GET /json/version HTTP/1.1\r\nHost: browser:9222\r\nAccept: */*\r\n\r\n"
    out = cdp_proxy.rewrite_host(head, 9223)
    assert b"Host: 127.0.0.1:9223" in out
    assert b"browser:9222" not in out


def test_host_header_preserved_when_absent():
    head = b"GET /json/version HTTP/1.1\r\nAccept: */*\r\n\r\n"
    assert cdp_proxy.rewrite_host(head, 9223) == head


def test_client_host_is_captured_before_rewrite():
    head = b"GET /json/version HTTP/1.1\r\nHost: browser:9222\r\n\r\n"
    assert cdp_proxy.client_host_of(head) == "browser:9222"
    assert cdp_proxy.client_host_of(b"GET / HTTP/1.1\r\n\r\n") == "127.0.0.1"


def test_content_length_regex_matches():
    head = b"HTTP/1.1 200 OK\r\nContent-Length: 42\r\n\r\n"
    assert cdp_proxy.CONTENT_LENGTH.search(head).group(1) == b"42"


@pytest.mark.asyncio
async def test_http_response_body_urls_are_rewritten():
    """A /json/version body pointing at loopback must point at the peer instead."""

    class FakeReader:
        def __init__(self, data: bytes) -> None:
            self.data = data

        async def read(self, _n: int) -> bytes:
            out, self.data = self.data, b""
            return out

    class FakeWriter:
        def __init__(self) -> None:
            self.buffer = b""

        def write(self, data: bytes) -> None:
            self.buffer += data

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

    body = b'{"webSocketDebuggerUrl":"ws://127.0.0.1:9223/devtools/browser/abc"}'
    response = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    local_writer = FakeWriter()
    remote_writer = FakeWriter()

    await cdp_proxy.handle_http(
        b"GET /json/version HTTP/1.1\r\nHost: browser:9222\r\n\r\n",
        "browser:9222",
        FakeReader(b""),
        local_writer,
        FakeReader(response),
        remote_writer,
    )

    assert b"ws://browser:9222/devtools/browser/abc" in local_writer.buffer
    assert b"127.0.0.1:9223" not in local_writer.buffer
