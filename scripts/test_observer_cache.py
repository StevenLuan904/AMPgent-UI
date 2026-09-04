"""Executable contract tests for the observer read-through cache.

These tests stub the FastAPI/router imports so they run without PostgreSQL or
the agent-platform environment. They exercise the ASGI middleware itself.
"""

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path


def load_observer_module():
    fastapi = types.ModuleType("fastapi")

    class FakeFastAPI:
        def __init__(self, **_kwargs):
            pass

        def add_middleware(self, *_args, **_kwargs):
            pass

        def include_router(self, *_args, **_kwargs):
            pass

        def get(self, *_args, **_kwargs):
            return lambda function: function

    fastapi.FastAPI = FakeFastAPI
    cors = types.ModuleType("fastapi.middleware.cors")
    cors.CORSMiddleware = object
    middleware = types.ModuleType("fastapi.middleware")
    observer = types.ModuleType("pepagent.api.observer")
    observer.router = object()
    pepagent = types.ModuleType("pepagent")
    pepagent_api = types.ModuleType("pepagent.api")
    sys.modules.update({
        "fastapi": fastapi,
        "fastapi.middleware": middleware,
        "fastapi.middleware.cors": cors,
        "pepagent": pepagent,
        "pepagent.api": pepagent_api,
        "pepagent.api.observer": observer,
    })
    path = Path(__file__).with_name("observer_only.py")
    spec = importlib.util.spec_from_file_location("observer_only_cache_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ObserverCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_observer_module()

    def invoke(self, middleware, path, query=b"limit=12"):
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "method": "GET", "path": path, "query_string": query}
        asyncio.run(middleware(scope, receive, send))
        return messages

    async def ok_app(self, scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json"), (b"set-cookie", b"should-not-persist")]})
        await send({"type": "http.response.body", "body": b'{"runs": []}', "more_body": False})

    def test_snapshot_survives_middleware_restart_and_stale_is_marked(self):
        with tempfile.TemporaryDirectory() as directory:
            old = os.environ.get("AMPGENT_OBSERVER_CACHE_DIR")
            os.environ["AMPGENT_OBSERVER_CACHE_DIR"] = directory
            try:
                first = self.module.ObserverReadCoalescingMiddleware(self.ok_app)
                fresh = self.invoke(first, "/v1/observer/runs/run-1")
                self.assertEqual(fresh[0]["status"], 200)
                cache_files = list(Path(directory).glob("*.json"))
                self.assertEqual(len(cache_files), 1)
                envelope = json.loads(cache_files[0].read_text(encoding="utf-8"))
                self.assertEqual(envelope["version"], 1)
                self.assertEqual(envelope["path"], "/v1/observer/runs/run-1")
                self.assertIn("stored_at", envelope)
                self.assertEqual(envelope["body_sha256"], "3d2d9419f6676434cb2f67d4944dc98b578ceec70214325370fb9c1e2423c365")
                self.assertNotIn("set-cookie", json.dumps(envelope).lower())

                second = self.module.ObserverReadCoalescingMiddleware(self.ok_app)
                restored = self.invoke(second, "/v1/observer/runs/run-1")
                headers = dict(restored[0]["headers"])
                self.assertEqual(headers[b"x-ampgent-cache"], b"restored-stale")

                key = second._cache_key("/v1/observer/runs/run-1", b"limit=12")
                second._cache[key] = self.module._CachedResponse(200, [(b"content-type", b"application/json")], b'{"runs": []}', 0, time.monotonic() + 30)
                stale = self.invoke(second, "/v1/observer/runs/run-1")
                self.assertEqual(dict(stale[0]["headers"])[b"x-ampgent-cache"], b"stale-refresh")

                envelope["stored_at"] = time.time() - second.persistent_max_age - 1
                cache_files[0].write_text(json.dumps(envelope), encoding="utf-8")
                self.module.ObserverReadCoalescingMiddleware(self.ok_app)
                self.assertEqual(list(Path(directory).glob("*.json")), [])
            finally:
                if old is None:
                    os.environ.pop("AMPGENT_OBSERVER_CACHE_DIR", None)
                else:
                    os.environ["AMPGENT_OBSERVER_CACHE_DIR"] = old

    def test_valid_json_with_wrong_digest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            old = os.environ.get("AMPGENT_OBSERVER_CACHE_DIR")
            os.environ["AMPGENT_OBSERVER_CACHE_DIR"] = directory
            try:
                first = self.module.ObserverReadCoalescingMiddleware(self.ok_app)
                self.invoke(first, "/v1/observer/runs/run-1")
                cache_file = next(Path(directory).glob("*.json"))
                envelope = json.loads(cache_file.read_text(encoding="utf-8"))
                envelope["body_base64"] = "e30="
                cache_file.write_text(json.dumps(envelope), encoding="utf-8")

                second = self.module.ObserverReadCoalescingMiddleware(self.ok_app)
                restored = self.invoke(second, "/v1/observer/runs/run-1")
                self.assertNotEqual(dict(restored[0]["headers"]).get(b"x-ampgent-cache"), b"restored-stale")
            finally:
                if old is None:
                    os.environ.pop("AMPGENT_OBSERVER_CACHE_DIR", None)
                else:
                    os.environ["AMPGENT_OBSERVER_CACHE_DIR"] = old

    def test_error_response_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            old = os.environ.get("AMPGENT_OBSERVER_CACHE_DIR")
            os.environ["AMPGENT_OBSERVER_CACHE_DIR"] = directory
            try:
                async def error_app(scope, receive, send):
                    await send({"type": "http.response.start", "status": 502, "headers": []})
                    await send({"type": "http.response.body", "body": b"upstream error", "more_body": False})

                middleware = self.module.ObserverReadCoalescingMiddleware(error_app)
                messages = []

                async def receive():
                    return {"type": "http.request", "body": b"", "more_body": False}

                async def send(message):
                    messages.append(message)

                async def run():
                    await middleware({"type": "http", "method": "GET", "path": "/v1/observer/runs", "query_string": b"limit=1"}, receive, send)

                asyncio.run(run())
                self.assertEqual(messages[0]["status"], 502)
                self.assertEqual(list(Path(directory).glob("*.json")), [])
            finally:
                if old is None:
                    os.environ.pop("AMPGENT_OBSERVER_CACHE_DIR", None)
                else:
                    os.environ["AMPGENT_OBSERVER_CACHE_DIR"] = old


if __name__ == "__main__":
    unittest.main()
