from __future__ import annotations

from typing import Any

from pepagent.storage import object_store as object_store_module


class _FakeS3Client:
    def __init__(self) -> None:
        self.head_bucket_calls = 0

    def head_bucket(self, **_: Any) -> None:
        self.head_bucket_calls += 1

    def upload_fileobj(self, *_: Any, **__: Any) -> None:
        return None


def test_read_client_is_bounded_and_does_not_probe_bucket(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    client = _FakeS3Client()

    def fake_client(*args: Any, **kwargs: Any) -> _FakeS3Client:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return client

    monkeypatch.setattr(object_store_module.boto3, "client", fake_client)
    store = object_store_module.ContentAddressedObjectStore()

    config = captured["kwargs"]["config"]
    settings = object_store_module.get_settings()
    assert config.connect_timeout == float(settings.s3_connect_timeout_seconds)
    assert config.read_timeout == float(settings.s3_read_timeout_seconds)
    assert config.retries["max_attempts"] == int(settings.s3_max_attempts)
    assert client.head_bucket_calls == 0
    assert store.client is client


def test_write_checks_bucket_before_upload(monkeypatch: Any) -> None:
    client = _FakeS3Client()
    monkeypatch.setattr(object_store_module.boto3, "client", lambda *_, **__: client)
    store = object_store_module.ContentAddressedObjectStore()

    stored = store.put_bytes(b"payload", "application/octet-stream")

    assert client.head_bucket_calls == 1
    assert stored.size_bytes == 7
