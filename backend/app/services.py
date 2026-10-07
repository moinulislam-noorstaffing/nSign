"""Integrations: ONLYOFFICE, Gotenberg, PandaDoc, and local file storage."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from typing import Any

import boto3
import httpx
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import settings


class ServiceError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
#  Object storage — MinIO (S3 API)
#
#  Documents live in object storage, not on a container filesystem, for the same
#  reason HELIX does it: the API is stateless and replaceable, and a lawyer-
#  approved .docx must survive the container that happened to receive it.
#  boto3 is synchronous, so every call is pushed to a worker thread rather than
#  blocking the event loop.
# --------------------------------------------------------------------------- #

_session = boto3.session.Session()


def _client():
    return _session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def ensure_bucket() -> None:
    """Create the bucket on first boot. Idempotent."""
    client = _client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise
        client.create_bucket(Bucket=settings.s3_bucket)


async def put_object(key: str, content: bytes, content_type: str) -> str:
    """Store bytes and return their sha256."""
    digest = hashlib.sha256(content).hexdigest()
    await asyncio.to_thread(
        _client().put_object,
        Bucket=settings.s3_bucket, Key=key, Body=content,
        ContentType=content_type, Metadata={"sha256": digest},
    )
    return digest


async def get_object(key: str) -> bytes:
    def _read() -> bytes:
        return _client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
    try:
        return await asyncio.to_thread(_read)
    except ClientError as exc:
        raise ServiceError(f"object {key} is missing from storage: {exc}") from exc


async def delete_object(key: str) -> None:
    await asyncio.to_thread(_client().delete_object, Bucket=settings.s3_bucket, Key=key)


async def storage_healthy() -> bool:
    try:
        await asyncio.to_thread(_client().head_bucket, Bucket=settings.s3_bucket)
        return True
    except Exception:
        return False


#: Key layout. Versions are separate objects so an issued letter stays
#: retrievable after the template moves on.
def source_key(template_id: str, version: int) -> str:
    return f"templates/{template_id}/v{version}.docx"


def asset_key(asset_id: str, suffix: str) -> str:
    return f"assets/{asset_id}{suffix}"


# --------------------------------------------------------------------------- #
#  ONLYOFFICE — JWT in BOTH directions
# --------------------------------------------------------------------------- #

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


#: A callback token is single-session. Anything older is a replay.
JWT_MAX_AGE_SECONDS = 8 * 60 * 60


def sign_jwt(payload: dict[str, Any], *, ttl: int = JWT_MAX_AGE_SECONDS) -> str:
    """HS256, hand-rolled to avoid a dependency for ~15 lines."""
    if not settings.onlyoffice_jwt_secret:
        raise ServiceError("ONLYOFFICE_JWT_SECRET is not set")
    payload = {**payload, "iat": now(), "exp": now() + ttl}
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    sig = hmac.new(settings.onlyoffice_jwt_secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(sig)}"


def verify_jwt(token: str) -> dict[str, Any]:
    """Verify a token Document Server signed.

    Integrations routinely sign the outbound editor config and forget that
    Document Server signs its INBOUND callback too. Skipping this check leaves
    an unauthenticated file-write into the offer-letter store: anything that can
    reach the callback URL can replace an approved document.
    """
    try:
        header_b64, body_b64, sig_b64 = token.split(".")
    except ValueError as exc:
        raise ServiceError("malformed JWT") from exc
    expected = hmac.new(
        settings.onlyoffice_jwt_secret.encode(),
        f"{header_b64}.{body_b64}".encode(),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected, _unb64(sig_b64)):
        raise ServiceError("JWT signature does not verify")

    body = json.loads(_unb64(body_b64))
    # A valid signature is not a fresh one. Without an expiry check a token
    # captured once — from a proxy log, the ONLYOFFICE log volume, a network
    # tap — replays forever against the callback that writes approved documents.
    exp = body.get("exp")
    if not isinstance(exp, (int, float)):
        raise ServiceError("token has no expiry")
    if now() > exp:
        raise ServiceError("token has expired")
    iat = body.get("iat")
    if isinstance(iat, (int, float)) and now() - iat > JWT_MAX_AGE_SECONDS:
        raise ServiceError("token is too old")
    return body


def callback_url_allowed(url: str) -> bool:
    """Only the document server may tell us where to fetch an edited file from.

    The callback payload carries the download URL, and it was previously passed
    straight to httpx. That turns the API into a confused deputy: a replayed
    token with `url=http://minio:9000/...` reaches every service on the compose
    network, and whatever comes back is stored as a new approved version of a
    legal document.
    """
    from urllib.parse import urlparse
    allowed = urlparse(settings.onlyoffice_internal_url)
    got = urlparse(url)
    return (got.scheme in ("http", "https")
            and got.hostname == allowed.hostname
            and (got.port or 80) == (allowed.port or 80))


def editor_config(*, template_id: str, version: int, filename: str,
                  title: str, mode: str = "view", user_name: str = "HR") -> dict[str, Any]:
    """Config for DocsAPI.DocEditor, signed.

    `document.url` and `callbackUrl` must be reachable FROM the document server
    container, so they use the internal address — pointing them at localhost is
    the single most common way this integration fails.
    """
    key = f"{template_id}-v{version}"
    config: dict[str, Any] = {
        "document": {
            "fileType": "docx",
            "key": key,
            "title": title or filename or "letter.docx",
            "url": f"{settings.api_internal_url}/api/editor/file/{template_id}/{version}",
            "permissions": {
                "edit": mode == "edit",
                "download": True,
                "print": True,
                # Community cannot white-label anyway (branding is server-gated
                # off), and stripping it would breach the 9.4 additional terms.
                "protect": False,
            },
        },
        "documentType": "word",
        "editorConfig": {
            "mode": "edit" if mode == "edit" else "view",
            "lang": "en-US",
            "callbackUrl": f"{settings.api_internal_url}/api/editor/callback/{template_id}",
            "user": {"id": "hr", "name": user_name},
            "customization": {"autosave": False, "forcesave": True, "compactHeader": False},
        },
        "type": "desktop",
        "width": "100%",
        "height": "100%",
    }
    config["token"] = sign_jwt(config)
    return config


async def onlyoffice_healthy() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{settings.onlyoffice_internal_url}/healthcheck")
        return r.text.strip().lower() == "true"
    except Exception:
        return False


async def fetch_saved(url: str) -> bytes:
    """Download the edited file Document Server produced."""
    if not callback_url_allowed(url):
        raise ServiceError(f"refusing to fetch {url[:80]!r}: not the document server")
    async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
        r = await client.get(url)
    if not r.is_success:
        raise ServiceError(f"could not fetch the edited document: HTTP {r.status_code}")
    return r.content


# --------------------------------------------------------------------------- #
#  Gotenberg — .docx to PDF
# --------------------------------------------------------------------------- #

async def render_pdf(docx_bytes: bytes, filename: str = "letter.docx") -> bytes:
    """Render through LibreOffice inside Gotenberg.

    Chosen over calling `soffice` directly because Gotenberg owns the process
    pool, the restart policy and the queue — the three things that make raw
    headless LibreOffice unreliable under concurrency.
    """
    url = f"{settings.gotenberg_url}/forms/libreoffice/convert"
    files = {"files": (filename, docx_bytes,
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    async with httpx.AsyncClient(timeout=settings.gotenberg_timeout) as client:
        r = await client.post(url, files=files)
    if not r.is_success:
        raise ServiceError(f"Gotenberg refused the document: HTTP {r.status_code} {r.text[:300]}")
    return r.content


async def gotenberg_healthy() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{settings.gotenberg_url}/health")
        return r.is_success
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  PandaDoc — signature only
# --------------------------------------------------------------------------- #

async def pandadoc_upload(name: str, pdf: bytes, recipient_email: str) -> dict[str, Any]:
    """Upload a finished PDF. PandaDoc signs; it does not merge.

    Established by measurement: it accepts a .docx but detects zero merge fields
    in it, and its own templates expose only auto-named fields (Text1, Date1).
    """
    if not settings.pandadoc_api_key:
        raise ServiceError("PANDADOC_API_KEY is not set")
    descriptor = {
        "name": name,
        "recipients": [{"email": recipient_email, "first_name": "Test", "last_name": "Candidate"}],
        "parse_form_fields": False,
    }
    files = {
        "file": (f"{name}.pdf", pdf, "application/pdf"),
        "data": (None, json.dumps(descriptor), "application/json"),
    }
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            f"{settings.pandadoc_base_url}/documents",
            headers={"Authorization": f"API-Key {settings.pandadoc_api_key}"},
            files=files,
        )
    return {"status": r.status_code, "ok": r.is_success,
            "response": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:400]}


def now() -> int:
    return int(time.time())
