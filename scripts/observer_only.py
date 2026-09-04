"""Minimal read-only API entrypoint for the local AMPgent UI.

This imports only the PostgreSQL-backed observer router. It deliberately does
not import the control-plane app or connect to Temporal. Identical reads are
coalesced because the authoritative PostgreSQL is reached through a high-RTT
SSH tunnel; opening two UI windows must not multiply the same aggregate query.
"""

import asyncio
import base64
import binascii
import hashlib
import hmac
import importlib
import inspect
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

observer_module = importlib.import_module("pepagent.api.observer")
observer_router = observer_module.router

OBSERVER_PROTOCOL_VERSION = "ampgent-observer/v2"


def compute_observer_source_fingerprint(source_files: list[tuple[str, Path]]) -> str:
    """Hash the exact source files that define this read-only service.

    Labels are stable rather than absolute paths, so the health response never
    discloses workspace layout while launchers can reproduce the value locally.
    """
    digest = hashlib.sha256()
    for label, path in sorted(source_files, key=lambda item: item[0]):
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


_observer_module_path = getattr(observer_module, "__file__", None) or inspect.getfile(observer_module)
OBSERVER_SOURCE_FILES = [
    ("observer_only.py", Path(__file__).resolve()),
    ("pepagent/api/observer.py", Path(_observer_module_path).resolve()),
]
OBSERVER_SOURCE_FINGERPRINT = compute_observer_source_fingerprint(OBSERVER_SOURCE_FILES)
OBSERVER_SERVICE_VERSION = "observer-only-cache-v2"


@dataclass(frozen=True)
class _CachedResponse:
    status: int
    headers: list[tuple[bytes, bytes]]
    body: bytes
    expires_at: float
    stale_expires_at: float | None = None
    restored: bool = False


class ObserverReadCoalescingMiddleware:
    """Read-through cache for expensive observer reads.

    The in-memory layer coalesces concurrent requests. Successful JSON list and
    detail responses also get a small versioned disk snapshot so a new API
    process can answer immediately while it refreshes from PostgreSQL. No
    request headers are persisted and response headers are reduced to a safe
    content type before storage.
    """

    cache_version = 1
    max_entry_bytes = 4_000_000
    max_file_bytes = 6_000_000
    max_entries = 64
    max_total_bytes = 32_000_000
    persistent_max_age = 24 * 60 * 60

    def __init__(self, app: Any) -> None:
        self.app = app
        self._cache: dict[bytes, _CachedResponse] = {}
        self._inflight: dict[bytes, asyncio.Future[_CachedResponse]] = {}
        self._disk_loaded: set[bytes] = set()
        self._lock = asyncio.Lock()
        self._evict_persistent()

    @property
    def cache_dir(self) -> Path:
        configured = os.environ.get("AMPGENT_OBSERVER_CACHE_DIR")
        return Path(configured) if configured else Path(__file__).resolve().parent.parent / "output" / "observer-cache"

    @staticmethod
    def _cache_key(path: str, query_string: bytes) -> bytes:
        return path.encode("utf-8") + b"?" + query_string

    @staticmethod
    def _persistable_path(path: str) -> bool:
        return bool(re.fullmatch(r"/v1/observer/runs(?:/[^/]+)?", path))

    @classmethod
    def _cacheable_response(cls, response: _CachedResponse) -> bool:
        if response.status != 200 or len(response.body) > cls.max_entry_bytes:
            return False
        try:
            json.loads(response.body)
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _safe_headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
        sensitive = {b"authorization", b"cookie", b"proxy-authorization", b"set-cookie"}
        return [(key, value) for key, value in headers if key.lower() not in sensitive and key.lower() not in {b"content-length", b"x-ampgent-cache"}]

    def _cache_file(self, key: bytes) -> Path:
        digest = hashlib.sha256(key).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _write_persistent(self, key: bytes, path: str, query_string: bytes, response: _CachedResponse) -> None:
        if not self._persistable_path(path) or not self._cacheable_response(response):
            return
        cache_dir = self.cache_dir
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            stored_at = time.time()
            envelope = {
                "version": self.cache_version,
                "method": "GET",
                "path": path,
                "query": query_string.decode("utf-8", errors="replace"),
                "stored_at": stored_at,
                "status": response.status,
                "content_type": "application/json",
                "body_sha256": hashlib.sha256(response.body).hexdigest(),
                "body_base64": base64.b64encode(response.body).decode("ascii"),
            }
            target = self._cache_file(key)
            fd, temporary = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp", dir=cache_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                    json.dump(envelope, handle, ensure_ascii=False, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self._evict_persistent()
        except (OSError, ValueError):
            # Disk snapshots are an optimization. A read-only API must remain
            # usable when its runtime directory is read-only or unavailable.
            return

    def _evict_persistent(self) -> None:
        try:
            files = list(self.cache_dir.glob("*.json"))
        except OSError:
            return
        entries: list[tuple[Path, float, int]] = []
        now = time.time()
        for file in files:
            try:
                size = file.stat().st_size
                with file.open("r", encoding="utf-8") as handle:
                    envelope = json.load(handle)
                stored_at = float(envelope.get("stored_at"))
                path = str(envelope.get("path") or "")
                if size > self.max_file_bytes or envelope.get("version") != self.cache_version or not self._persistable_path(path) or now - stored_at > self.persistent_max_age:
                    file.unlink(missing_ok=True)
                    continue
                entries.append((file, stored_at, size))
            except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
                try:
                    file.unlink(missing_ok=True)
                except OSError:
                    pass
        total = sum(size for _, _, size in entries)
        for entry in sorted(entries, key=lambda item: item[1]):
            if len(entries) <= self.max_entries and total <= self.max_total_bytes:
                break
            file, _, size = entry
            try:
                file.unlink(missing_ok=True)
            except OSError:
                pass
            entries.remove(entry)
            total -= size

    def _load_persistent(self, key: bytes, path: str, query_string: bytes, now: float) -> None:
        if key in self._disk_loaded or not self._persistable_path(path):
            return
        self._disk_loaded.add(key)
        file = self._cache_file(key)
        try:
            with file.open("r", encoding="utf-8") as handle:
                envelope = json.load(handle)
            stored_at = float(envelope["stored_at"])
            expected_query = query_string.decode("utf-8", errors="replace")
            if envelope.get("version") != self.cache_version or envelope.get("method") != "GET" or envelope.get("path") != path or envelope.get("query") != expected_query:
                file.unlink(missing_ok=True)
                return
            age = max(0.0, time.time() - stored_at)
            if age > self.persistent_max_age:
                file.unlink(missing_ok=True)
                return
            body = base64.b64decode(str(envelope["body_base64"]), validate=True)
            if not hmac.compare_digest(
                hashlib.sha256(body).hexdigest(),
                str(envelope.get("body_sha256") or ""),
            ):
                file.unlink(missing_ok=True)
                return
            response = _CachedResponse(
                status=int(envelope["status"]),
                headers=[(b"content-type", str(envelope.get("content_type") or "application/json").encode("ascii", errors="ignore"))],
                body=body,
                expires_at=now,
                stale_expires_at=now + max(0.0, self.persistent_max_age - age),
                restored=True,
            )
            if self._cacheable_response(response):
                self._cache[key] = response
            else:
                file.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError, binascii.Error):
            try:
                file.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _ttl(path: str) -> float:
        if path == "/v1/observer/runs":
            return 20.0
        if "/nodes/" in path:
            return 30.0
        return 10.0

    @staticmethod
    def _stale_window(path: str) -> float:
        # A stale response is only used while a refresh is running or the
        # remote tunnel is briefly unavailable. Every stale read still starts
        # one background refresh, so normal polling observes the new snapshot
        # on its next pass without blocking the interface on tunnel latency.
        if path == "/v1/observer/runs":
            return 120.0
        if "/nodes/" in path:
            return 90.0
        return 60.0

    @staticmethod
    async def _send_cached(send: Any, response: _CachedResponse, cache_state: bytes) -> None:
        headers = ObserverReadCoalescingMiddleware._safe_headers(response.headers)
        headers.append((b"x-ampgent-cache", cache_state))
        await send({"type": "http.response.start", "status": response.status, "headers": headers})
        await send({"type": "http.response.body", "body": response.body, "more_body": False})

    async def _refresh_in_background(
        self,
        key: bytes,
        scope: dict[str, Any],
        previous: _CachedResponse,
        future: asyncio.Future[_CachedResponse],
    ) -> None:
        messages: list[dict[str, Any]] = []
        request_delivered = False

        async def receive() -> dict[str, Any]:
            nonlocal request_delivered
            if not request_delivered:
                request_delivered = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def capture(message: dict[str, Any]) -> None:
            messages.append(message)

        resolved = previous
        try:
            await self.app(dict(scope), receive, capture)
            start = next(message for message in messages if message["type"] == "http.response.start")
            body = b"".join(
                bytes(message.get("body") or b"")
                for message in messages
                if message["type"] == "http.response.body"
            )
            fresh_expires_at = time.monotonic() + self._ttl(str(scope.get("path") or ""))
            refreshed = _CachedResponse(
                status=int(start["status"]),
                headers=self._safe_headers(list(start.get("headers") or [])),
                body=body,
                expires_at=fresh_expires_at,
                stale_expires_at=fresh_expires_at + self._stale_window(str(scope.get("path") or "")),
            )
            if self._cacheable_response(refreshed):
                resolved = refreshed
                self._write_persistent(key, str(scope.get("path") or ""), bytes(scope.get("query_string") or b""), refreshed)
                async with self._lock:
                    self._cache[key] = refreshed
        except BaseException:
            # Preserve the last successful read. A later request will retry;
            # database or tunnel failures remain visible through fresh reads
            # once the stale safety window expires.
            resolved = previous
        finally:
            async with self._lock:
                self._inflight.pop(key, None)
                if not future.done():
                    future.set_result(resolved)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path") or "")
        if scope.get("type") != "http" or scope.get("method") != "GET" or not path.startswith("/v1/observer/runs"):
            await self.app(scope, receive, send)
            return

        query_string = bytes(scope.get("query_string") or b"")
        key = self._cache_key(path, query_string)
        now = time.monotonic()
        owner = False
        stale: _CachedResponse | None = None
        async with self._lock:
            cached = self._cache.get(key)
            if cached is None:
                self._load_persistent(key, path, query_string, now)
                cached = self._cache.get(key)
            if cached is not None and cached.expires_at > now:
                await self._send_cached(send, cached, b"hit")
                return
            future = self._inflight.get(key)
            stale_expires_at = cached.stale_expires_at if cached is not None and cached.stale_expires_at is not None else cached.expires_at + self._stale_window(path) if cached is not None else 0
            if cached is not None and stale_expires_at > now:
                stale = cached
                if future is None:
                    future = asyncio.get_running_loop().create_future()
                    self._inflight[key] = future
                    asyncio.create_task(self._refresh_in_background(key, scope, cached, future))
            elif future is None:
                future = asyncio.get_running_loop().create_future()
                self._inflight[key] = future
                owner = True

        if stale is not None:
            await self._send_cached(send, stale, b"restored-stale" if stale.restored else b"stale-refresh")
            return

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
            fresh_expires_at = time.monotonic() + self._ttl(path)
            shared = _CachedResponse(
                status=int(start["status"]),
                headers=self._safe_headers(list(start.get("headers") or [])),
                body=body,
                expires_at=fresh_expires_at,
                stale_expires_at=fresh_expires_at + self._stale_window(path),
            )
            if self._cacheable_response(shared):
                self._write_persistent(key, path, query_string, shared)
            async with self._lock:
                if self._cacheable_response(shared):
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
    expose_headers=["X-Ampgent-Cache"],
)
app.include_router(observer_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "mode": "observer-only",
        "protocol_version": OBSERVER_PROTOCOL_VERSION,
        "service_version": OBSERVER_SERVICE_VERSION,
        "source_fingerprint": OBSERVER_SOURCE_FINGERPRINT,
    }
