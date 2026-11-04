"""Shared ASGI adapter for explicit Vercel API function routes."""
from __future__ import annotations

import json
import io
import os
from urllib.parse import parse_qs, urlencode

import server
from db import connect as open_database, has_postgres_url, postgres_url_name

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


def dispatch(method, path, query_string, headers, body, forced_path=None):
    query = parse_qs(query_string.decode("latin-1"), keep_blank_values=True)
    rewritten = query.pop("__path", [None])[0]
    if forced_path:
        path = forced_path
    elif rewritten:
        path = rewritten
    elif not path.startswith("/api/"):
        for key in ("x-invoke-path", "x-matched-path", "x-original-url", "x-forwarded-uri"):
            candidate = headers.get(key)
            if candidate and candidate.startswith("/api/"):
                path = candidate.split("?", 1)[0]
                break
        if path.startswith("/api/index.py/"):
            path = "/api/" + path[len("/api/index.py/"):]
        elif path.startswith("/api/index/"):
            path = "/api/" + path[len("/api/index/"):]
        elif path and not path.startswith("/"):
            path = "/api/" + path.removeprefix("api/")
        elif path.startswith("/") and not path.startswith("/api/") and path != "/api":
            path = "/api" + path
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


def app_for(route: str):
    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        try:
            if os.environ.get("VERCEL") and not has_postgres_url():
                raise RuntimeError("PostgreSQL is not connected. Add DATABASE_URL (or POSTGRES_URL) to this Vercel environment and redeploy.")
            if route == "/api/db-status":
                connection = open_database(server.DB)
                try:
                    row = connection.execute(
                        "SELECT current_database() AS database_name, current_schema() AS schema_name, "
                        "to_regclass('files')::text AS files_table"
                    ).fetchone()
                    result = {
                        "connectionVariable": postgres_url_name(),
                        "database": row["database_name"],
                        "schema": row["schema_name"],
                        "filesTable": row["files_table"],
                    }
                finally:
                    connection.close()
                body = json.dumps(result).encode("utf-8")
                await send({"type": "http.response.start", "status": 200, "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ]})
                await send({"type": "http.response.body", "body": body})
                return
            # Idempotent DDL on each request handles cold starts and schema
            # changes consistently across Vercel's separate function instances.
            server.init()
            chunks = []
            while True:
                message = await receive()
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
            query = scope.get("query_string", b"")
            dispatch_path = route
            if route == "/api/rule-delete":
                rule_id = parse_qs(query.decode("latin-1")).get("ruleId", [""])[0]
                dispatch_path = f"/api/rules/{rule_id}"
            status, response_headers, body = dispatch(
                scope["method"], route, query, headers, b"".join(chunks), forced_path=dispatch_path
            )
            await send({"type": "http.response.start", "status": status, "headers": response_headers})
            await send({"type": "http.response.body", "body": body})
        except Exception as exc:
            body = json.dumps({"error": f"Workspace startup failed: {type(exc).__name__}: {exc}"}).encode("utf-8")
            await send({"type": "http.response.start", "status": 503, "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ]})
            await send({"type": "http.response.body", "body": body})
    return app
