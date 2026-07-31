import io
import mimetypes
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from pepagent.provenance.hashing import sha256_bytes, sha256_file
from pepagent.settings import get_settings


@dataclass(frozen=True)
class StoredObject:
    sha256: str
    size_bytes: int
    uri: str
    media_type: str


class ContentAddressedObjectStore:
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self.client.create_bucket(Bucket=self.bucket)

    def put_bytes(self, payload: bytes, media_type: str) -> StoredObject:
        digest = sha256_bytes(payload)
        key = f"sha256/{digest[:2]}/{digest}"
        self.client.upload_fileobj(
            io.BytesIO(payload),
            self.bucket,
            key,
            ExtraArgs={"ContentType": media_type, "Metadata": {"sha256": digest}},
        )
        return StoredObject(
            sha256=digest,
            size_bytes=len(payload),
            uri=f"s3://{self.bucket}/{key}",
            media_type=media_type,
        )

    def put_file(self, path: str | Path, media_type: str | None = None) -> StoredObject:
        source = Path(path)
        digest = sha256_file(source)
        key = f"sha256/{digest[:2]}/{digest}"
        resolved_media_type = media_type or mimetypes.guess_type(source.name)[0] or (
            "application/octet-stream"
        )
        self.client.upload_file(
            str(source),
            self.bucket,
            key,
            ExtraArgs={"ContentType": resolved_media_type, "Metadata": {"sha256": digest}},
        )
        return StoredObject(
            sha256=digest,
            size_bytes=source.stat().st_size,
            uri=f"s3://{self.bucket}/{key}",
            media_type=resolved_media_type,
        )

    def get_bytes(self, uri: str) -> bytes:
        prefix = f"s3://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError(f"URI is outside configured bucket: {uri}")
        response = self.client.get_object(Bucket=self.bucket, Key=uri.removeprefix(prefix))
        payload = response["Body"].read()
        expected = response.get("Metadata", {}).get("sha256")
        if expected and sha256_bytes(payload) != expected:
            raise OSError("artifact checksum mismatch")
        return payload
