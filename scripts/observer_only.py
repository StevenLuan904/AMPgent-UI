"""Minimal read-only API entrypoint for the local AMPgent UI.

This imports only the PostgreSQL-backed observer router. It deliberately does
not import the control-plane app or connect to Temporal. Identical reads are
coalesced because the authoritative PostgreSQL is reached through a high-RTT
SSH tunnel; opening two UI windows must not multiply the same aggregate query.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pepagent.api.observer import router as observer_router


@dataclass(frozen=True)
class _CachedResponse:
    status: int
    headers: list[tuple[bytes, bytes]]
    body: bytes
    expires_at: float


class ObserverReadCoalescingMiddleware:
    """Short-lived cache and single-flight guard for expensive observer reads."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self._cache: dict[bytes, _CachedResponse] = {}
        self._inflight: dict[bytes, asyncio.Future[_CachedResponse]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _ttl(path: str) -> float:
        if path == "/v1/observer/runs":
            return 20.0
        if "/nodes/" in path:
            return 30.0
        return 10.0

    @staticmethod
    async def _send_cached(send: Any, response: _CachedResponse, cache_state: bytes) -> None:
        headers = [(key, value) for key, value in response.headers if key.lower() != b"x-ampgent-cache"]
        headers.append((b"x-ampgent-cache", cache_state))
        await send({"type": "http.response.start", "status": response.status, "headers": headers})
        await send({"type": "http.response.body", "body": response.body, "more_body": False})

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path") or "")
        if scope.get("type") != "http" or scope.get("method") != "GET" or not path.startswith("/v1/observer/runs"):
            await self.app(scope, receive, send)
            return

        key = path.encode("utf-8") + b"?" + bytes(scope.get("query_string") or b"")
        now = time.monotonic()
        owner = False
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at > now:
                await self._send_cached(send, cached, b"hit")
                return
            future = self._inflight.get(key)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._inflight[key] = future
                owner = True

        if not owner:
            shared = await asyncio.shield(future)
            await self._send_cached(send, shared, b"coalesced")
            return

        messages: list[dict[str, Any]] = []

        async def capture(message: dict[str, Any]) -> None:
            messages.append(message)

        try:
            await self.app(scope, receive, capture)
            start = next(message for message in messages if message["type"] == "http.response.start")
            body = b"".join(
                bytes(message.get("body") or b"")
                for message in messages
                if message["type"] == "http.response.body"
            )
            shared = _CachedResponse(
                status=int(start["status"]),
                headers=list(start.get("headers") or []),
                body=body,
                expires_at=time.monotonic() + self._ttl(path),
            )
            async with self._lock:
                if shared.status == 200 and len(body) <= 4_000_000:
                    self._cache[key] = shared
                    if len(self._cache) > 64:
                        oldest = min(self._cache, key=lambda item: self._cache[item].expires_at)
                        self._cache.pop(oldest, None)
                self._inflight.pop(key, None)
                if not future.done():
                    future.set_result(shared)
            for message in messages:
                await send(message)
        except BaseException as error:
            async with self._lock:
                self._inflight.pop(key, None)
                if not future.done():
                    future.set_exception(error)
            raise


app = FastAPI(title="AMPgent Observer API", version="0.1.0")
app.add_middleware(ObserverReadCoalescingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.include_router(observer_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": "observer-only"}
