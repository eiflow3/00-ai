"""Object storage service — the origin container for files we embed.

Backed by Cloudflare R2 through its S3-compatible API, so the same code works
against any S3-compatible store (R2, B2, MinIO, AWS) by changing the endpoint.

Every function here is async and hands the blocking boto3 call to a worker
thread, so callers cannot accidentally stall the event loop.
"""

import asyncio
from typing import Any, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings
from app.schemas.source import SourceObject


class ObjectStoreManager:
    """Singleton holder for the S3-compatible client.

    Mirrors PineconeManager: built once, lazily, so a missing credential
    surfaces on first use rather than crashing application startup.
    """

    _instance: Optional["ObjectStoreManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.client = boto3.client(
                "s3",
                endpoint_url=settings.r2_endpoint_url,
                aws_access_key_id=settings.r2_access_key_id,
                aws_secret_access_key=settings.r2_secret_access_key,
                # R2 has no regions, but the SDK requires one.
                region_name="auto",
                config=Config(
                    signature_version="s3v4",
                    # boto3 sends CRC checksums by default, which R2 rejects.
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_supported",
                ),
            )
        return cls._instance

    @classmethod
    def get_client(cls):
        """Return the active S3-compatible client."""
        return cls().client


def _to_source_object(entry: dict[str, Any]) -> SourceObject:
    """Normalise one raw list_objects_v2 entry into our own schema."""
    return SourceObject(
        key=entry["Key"],
        last_modified=entry["LastModified"],
        size=entry.get("Size", 0),
        # Providers wrap the hash in quotes; strip them so comparisons are plain.
        etag=str(entry.get("ETag", "")).strip('"'),
    )


def _list_objects_sync(prefix: str) -> list[SourceObject]:
    """Page through the bucket and return every object under `prefix`."""
    client = ObjectStoreManager.get_client()
    objects: list[SourceObject] = []

    # A bucket can hold more than one page of results; follow every page.
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.r2_bucket, Prefix=prefix):
        objects.extend(_to_source_object(entry) for entry in page.get("Contents", []))

    return objects


async def list_objects(prefix: str = "") -> list[SourceObject]:
    """List the files available to embed, newest change first.

    Args:
        prefix: Restrict the listing to keys beginning with this prefix.

    Returns:
        Every matching object with its last-modified time and content hash.
    """
    objects = await asyncio.to_thread(_list_objects_sync, prefix)
    objects.sort(key=lambda obj: obj.last_modified, reverse=True)
    return objects


async def get_object(key: str) -> bytes:
    """Download one object's raw bytes.

    Args:
        key: The object key within the bucket.

    Returns:
        The object's contents.

    Raises:
        FileNotFoundError: If no object exists at that key.
    """

    def _get() -> bytes:
        client = ObjectStoreManager.get_client()
        try:
            response = client.get_object(Bucket=settings.r2_bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                raise FileNotFoundError(f"No object at key: {key}") from exc
            raise
        return response["Body"].read()

    return await asyncio.to_thread(_get)


async def put_object(key: str, data: bytes, content_type: str = "text/plain") -> SourceObject:
    """Write an object, creating it or overwriting whatever is at that key.

    Returns the stored object rather than nothing, so the caller gets the new
    etag and last-modified time without a second round trip — and those are
    exactly the fields the staleness comparison runs on.

    Args:
        key: The object key within the bucket.
        data: The file's raw contents.
        content_type: MIME type recorded on the object.

    Returns:
        The object as it now exists in the store.
    """

    def _put() -> SourceObject:
        client = ObjectStoreManager.get_client()
        client.put_object(
            Bucket=settings.r2_bucket, Key=key, Body=data, ContentType=content_type
        )
        # put_object's own response carries the etag but not the timestamp the
        # store settled on, so read it back rather than inventing one.
        response = client.head_object(Bucket=settings.r2_bucket, Key=key)
        return _to_source_object(
            {**response, "Key": key, "Size": response.get("ContentLength", len(data))}
        )

    return await asyncio.to_thread(_put)


async def head_object(key: str) -> SourceObject:
    """Fetch one object's metadata without downloading its contents.

    Args:
        key: The object key within the bucket.

    Returns:
        The object's size, last-modified time, and content hash.

    Raises:
        FileNotFoundError: If no object exists at that key.
    """

    def _head() -> SourceObject:
        client = ObjectStoreManager.get_client()
        try:
            response = client.head_object(Bucket=settings.r2_bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                raise FileNotFoundError(f"No object at key: {key}") from exc
            raise
        # head_object returns the same fields as a listing entry, minus the key.
        return _to_source_object({**response, "Key": key, "Size": response.get("ContentLength", 0)})

    return await asyncio.to_thread(_head)
