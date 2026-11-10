#!/usr/bin/env python3
"""Expose a loopback-only Ollama server to Docker Desktop containers.

Ollama defaults to 127.0.0.1:11434, which is not reachable from a container
through host.docker.internal. This tiny TCP proxy listens on 0.0.0.0 and
forwards to the loopback Ollama server.

    python3 scripts/ollama_proxy.py
    # then use http://host.docker.internal:11435 in Compose
"""

from __future__ import annotations

import argparse
import asyncio


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65_536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(local_reader: asyncio.StreamReader, local_writer: asyncio.StreamWriter) -> None:
    try:
        remote_reader, remote_writer = await asyncio.open_connection(args.target_host, args.target_port)
    except OSError:
        local_writer.close()
        return
    await asyncio.gather(pipe(local_reader, remote_writer), pipe(remote_reader, local_writer))


async def main() -> None:
    server = await asyncio.start_server(handle, args.listen_host, args.listen_port)
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"Ollama proxy listening on {addresses} -> {args.target_host}:{args.target_port}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TCP proxy for a loopback-only Ollama server")
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=11435)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, default=11434)
    args = parser.parse_args()
    asyncio.run(main())
