"""Vercel ASGI entry point for the existing iTaxonomy HTTP handlers."""
from __future__ import annotations

import io
from urllib.parse import parse_qs, urlencode

import server

# Database schema is created idempotently when a Vercel function instance starts.
server.init()


class MemorySocket:
    def __init__(self, request_bytes):
        self.reader = io.BytesIO(request_bytes)
        self.writer = io.BytesIO()

    def makefile(self, mode, *args, **kwargs):
        return self.reader if "r" in mode else self.writer

    def sendall(self, data):
        self.writer.write(data)

    def close(self):
        pass


class MemoryServer:
    pass


def dispatch(method, path, query_string, headers, body):
    query = parse_qs(query_string.decode("latin-1"), keep_blank_values=True)
    rewritten = query.pop("__path", [None])[0]
    if rewritten:
        path = rewritten
    query_bytes = urlencode(query, doseq=True)
    target = path + ("?" + query_bytes if query_bytes else "")
    lines = [f"{method} {target} HTTP/1.1", "Host: vercel.local"]
    for name, value in headers.items():
        if name.lower() not in ("host", "content-length", "connection"):
            lines.append(f"{name}: {value}")
    lines.extend((f"Content-Length: {len(body)}", "Connection: close", "", ""))
    sock = MemorySocket("\r\n".join(lines).encode("latin-1") + body)
    server.Handler(sock, ("127.0.0.1", 0), MemoryServer())
    response = sock.writer.getvalue()
    head, payload = response.split(b"\r\n\r\n", 1)
    status = int(head.split(b"\r\n", 1)[0].split()[1])
    response_headers = []
    for line in head.split(b"\r\n")[1:]:
        if b":" in line:
            name, value = line.split(b":", 1)
            if name.lower() not in (b"connection", b"transfer-encoding", b"content-length"):
                response_headers.append((name.lower(), value.strip()))
    return status, response_headers, payload


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    chunks = []
    while True:
        message = await receive()
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope.get("headers", [])}
    status, response_headers, body = dispatch(scope["method"], scope["path"], scope.get("query_string", b""), headers, b"".join(chunks))
    await send({"type": "http.response.start", "status": status, "headers": response_headers})
    await send({"type": "http.response.body", "body": body})
