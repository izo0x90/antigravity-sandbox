"""
Relays Chrome DevTools Protocol connections from 127.0.0.1:<listen-port> inside the
sandbox to a Chrome running on the host.

The sandbox reaches the host only through the sbx HTTP proxy, while CDP clients
(chrome-devtools-mcp, OMP, ...) connect directly. The relay accepts those direct
connections, rewrites each request into proxy form (absolute URI) and then pipes the
stream both ways, so websocket upgrades keep working.

Chrome builds the webSocketDebuggerUrl it hands out from the request's Host header, and
the proxy presents the host as `localhost:<host-port>`. The relay rewrites that address
in discovery responses (/json/*) to its own, so clients reconnect through the relay.
"""
import argparse
import asyncio
import os
from typing import Tuple
from urllib.parse import urlsplit

HOST_ALIAS = "host.docker.internal"
PROXY_HOST_NAMES = ("localhost", HOST_ALIAS)
HEADER_END = b"\r\n\r\n"
DEFAULT_LISTEN_PORT = 9222


def proxy_address() -> Tuple[str, int]:
    url = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    if not url:
        raise SystemExit("chrome-host relay: HTTP_PROXY is not set, so the host is unreachable.")
    parts = urlsplit(url)
    return parts.hostname, parts.port or 80


def is_upgrade_request(head: bytes) -> bool:
    return any(h.lower().startswith(b"upgrade:") for h in head.split(b"\r\n"))


def rewrite_request_head(head: bytes, target: str, host_header: str) -> bytes:
    """Turns a direct request head into a proxy request for http://<target>."""
    request_line, _, header_block = head[: -len(HEADER_END)].partition(b"\r\n")
    method, path, version = request_line.split(b" ", 2)
    headers = [h for h in header_block.split(b"\r\n") if h and not h.lower().startswith(b"host:")]
    if not is_upgrade_request(head):
        # One request per connection, so only the first request line ever needs rewriting.
        headers = [h for h in headers if not h.lower().startswith(b"connection:")] + [b"Connection: close"]
    lines = [b" ".join((method, f"http://{target}".encode() + path, version)), f"Host: {host_header}".encode(), *headers]
    return b"\r\n".join(lines) + HEADER_END


def rewrite_discovery_response(response: bytes, host_port: int, relay_address: str) -> bytes:
    """Points the addresses Chrome advertises in a /json/* response at the relay."""
    head, sep, body = response.partition(HEADER_END)
    if not sep:
        return response
    for name in PROXY_HOST_NAMES:
        body = body.replace(f"{name}:{host_port}".encode(), relay_address.encode())
    headers = [
        f"Content-Length: {len(body)}".encode() if h.lower().startswith(b"content-length:") else h
        for h in head.split(b"\r\n")
    ]
    return b"\r\n".join(headers) + HEADER_END + body


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    finally:
        writer.close()


async def _handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    proxy: Tuple[str, int],
    host_port: int,
    relay_address: str,
) -> None:
    try:
        head = await client_reader.readuntil(HEADER_END)
        proxy_reader, proxy_writer = await asyncio.open_connection(*proxy)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
        client_writer.close()
        return
    proxy_writer.write(rewrite_request_head(head, f"{HOST_ALIAS}:{host_port}", relay_address))
    await proxy_writer.drain()

    if is_upgrade_request(head):
        await asyncio.gather(_pipe(client_reader, proxy_writer), _pipe(proxy_reader, client_writer), return_exceptions=True)
        return
    # Discovery request: the response is small and ends at EOF (`Connection: close`).
    response = await proxy_reader.read(-1)
    client_writer.write(rewrite_discovery_response(response, host_port, relay_address))
    await client_writer.drain()
    client_writer.close()
    proxy_writer.close()


async def serve(listen_port: int, host_port: int) -> None:
    proxy = proxy_address()
    relay_address = f"127.0.0.1:{listen_port}"
    server = await asyncio.start_server(
        lambda reader, writer: _handle(reader, writer, proxy, host_port, relay_address), "127.0.0.1", listen_port
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--host-port", type=int, required=True, help="Remote debugging port of the host Chrome")
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT, help="Port CDP clients connect to")
    args = parser.parse_args()
    asyncio.run(serve(args.listen_port, args.host_port))


if __name__ == "__main__":
    main()
