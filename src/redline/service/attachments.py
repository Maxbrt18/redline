"""Safe boundary between a Meta attachment and the coaching engine.

The webhook downloads nothing: it persists the URL signed by Meta and returns. The
worker then downloads into a private, bounded, ephemeral file. The context manager is
deliberate: even a model exception goes through ``finally`` and removes the raw file.
No received content is an instruction for the agent.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from redline.coach.fr import attachments as wording
from redline.model.errors import ExternalError

MAX_ATTACHMENTS = 4
MAX_REDIRECTS = 3
CHUNK_SIZE = 64 * 1024

ALLOWED_HOST_SUFFIXES = (".fbcdn.net", ".fbsbx.com", ".facebook.com")
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
ALLOWED_DOCUMENT_TYPES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
}
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class AttachmentError(ExternalError):
    """Error presentable to the athlete, without copying the URL or remote response."""


@dataclass(frozen=True)
class RemoteAttachment:
    kind: str
    url: str


@dataclass(frozen=True)
class LocalAttachment:
    kind: str
    path: Path
    media_type: str
    size: int


@dataclass(frozen=True)
class MaterializedAttachments:
    files: tuple[LocalAttachment, ...]
    ignored_video: bool = False


def encode_payload(attachments: list[dict], caption: str = "") -> str:
    """Reduce the Meta shape to our minimal durable contract."""
    rows = []
    for attachment in attachments[:MAX_ATTACHMENTS]:
        payload = attachment.get("payload") or {}
        rows.append({
            "type": str(attachment.get("type") or "").strip().lower(),
            "url": str(payload.get("url") or "").strip(),
        })
    return json.dumps({"version": 1, "caption": caption.strip()[:2000],
                       "attachments": rows}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))


def safe_conversation_label(raw: str) -> str:
    """A conversation trace with no URL, file name or raw content."""
    try:
        payload = json.loads(raw)
        kinds = {str(row.get("type") or "") for row in payload.get("attachments", [])}
    except (TypeError, ValueError, AttributeError):
        kinds = set()
    labels = []
    if "image" in kinds:
        labels.append(wording.PHOTO)
    if "file" in kinds:
        labels.append(wording.DOCUMENT)
    if kinds & {"video", "audio"}:
        labels.append(wording.UNSUPPORTED_MEDIA)
    return wording.LABEL.format(kinds=" + ".join(labels) or wording.UNKNOWN_FORMAT)


def _remote_rows(raw: str) -> tuple[list[RemoteAttachment], str]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise AttachmentError(wording.UNREADABLE) from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise AttachmentError(wording.UNKNOWN_VERSION)
    rows = payload.get("attachments")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ATTACHMENTS:
        raise AttachmentError(wording.COUNT.format(max=MAX_ATTACHMENTS))
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            raise AttachmentError(wording.INCOMPLETE)
        parsed.append(RemoteAttachment(
            str(row.get("type") or "").lower(), str(row.get("url") or "")))
    return parsed, str(payload.get("caption") or "").strip()[:2000]


def _safe_host(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and bool(host)
            and any(host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES))


def _media_type(value: str) -> str:
    return value.partition(";")[0].strip().lower()


def _content_matches(path: Path, media_type: str) -> bool:
    head = path.read_bytes()[:16]
    if media_type == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if media_type == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/webp":
        return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if media_type == "application/pdf":
        return head.startswith(b"%PDF-")
    if media_type in {"text/plain", "text/csv"}:
        return b"\x00" not in head
    return False


class AttachmentService:
    """Download photos/PDF/text under quota and destroy them after reading."""

    def __init__(self, directory: Path, *, max_bytes: int, total_bytes: int,
                 session: requests.Session | None = None) -> None:
        self.directory = Path(directory)
        self.max_bytes = int(max_bytes)
        self.total_bytes = int(total_bytes)
        self._session = session or requests.Session()

    def cleanup(self) -> int:
        """Delete only our ephemeral directory, never a broad path."""
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        removed = 0
        for path in self.directory.iterdir():
            if path.is_file() and path.name.startswith("inbound-"):
                path.unlink(missing_ok=True)
                removed += 1
            elif path.is_dir() and path.name.startswith("inbound-"):
                shutil.rmtree(path)
                removed += 1
        return removed

    @contextmanager
    def materialize(self, raw: str, message_id: str) -> Iterator[MaterializedAttachments]:
        remotes, _ = _remote_rows(raw)
        allowed = [row for row in remotes if row.kind in {"image", "file"}]
        ignored_video = any(row.kind in {"video", "audio"} for row in remotes)
        unknown = [row.kind for row in remotes
                   if row.kind not in {"image", "file", "video", "audio"}]
        if unknown:
            raise AttachmentError(wording.UNKNOWN_KIND)
        if not allowed:
            if ignored_video:
                raise AttachmentError(wording.NO_VIDEO)
            raise AttachmentError(wording.WHAT_TO_SEND)

        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        key = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:20]
        created: list[Path] = []
        files: list[LocalAttachment] = []
        total = 0
        try:
            for index, remote in enumerate(allowed):
                local = self._download(remote, key, index,
                                       remaining=max(0, self.total_bytes - total))
                created.append(local.path)
                files.append(local)
                total += local.size
            yield MaterializedAttachments(tuple(files), ignored_video=ignored_video)
        finally:
            for path in created:
                with suppress(OSError):
                    path.unlink(missing_ok=True)
                # The next startup cleans a leftover; a momentarily failing unlink
                # never masks the model's answer.

    def caption(self, raw: str) -> str:
        _, caption = _remote_rows(raw)
        return caption

    def _download(self, remote: RemoteAttachment, key: str, index: int, *,
                  remaining: int) -> LocalAttachment:
        if not remote.url or not _safe_host(remote.url):
            raise AttachmentError(wording.UNSAFE_URL)
        if remaining <= 0:
            raise AttachmentError(wording.TOTAL_TOO_LARGE)

        url = remote.url
        response = None
        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = self._session.get(
                    url, stream=True, timeout=(5, 30), allow_redirects=False,
                    headers={"User-Agent": "Redline-Coach/1.0"})
            except requests.RequestException as exc:
                raise AttachmentError(wording.DOWNLOAD_FAILED) from exc
            if response.status_code not in REDIRECT_CODES:
                break
            location = response.headers.get("Location", "")
            response.close()
            if not _safe_host(location):
                raise AttachmentError(wording.UNSAFE_REDIRECT)
            url = location
        if response is None or response.status_code != 200:
            if response is not None:
                response.close()
            raise AttachmentError(wording.DOWNLOAD_REFUSED)

        try:
            declared = int(response.headers.get("Content-Length") or 0)
        except ValueError:
            declared = 0
        limit = min(self.max_bytes, remaining)
        if declared > limit:
            response.close()
            raise AttachmentError(wording.TOO_LARGE.format(megabytes=self.max_bytes // (1024 * 1024)))
        content_type = _media_type(response.headers.get("Content-Type", ""))
        allowed_types = ALLOWED_IMAGE_TYPES if remote.kind == "image" else ALLOWED_DOCUMENT_TYPES
        if content_type not in allowed_types:
            response.close()
            accepted = (wording.IMAGE_FORMATS if remote.kind == "image"
                        else wording.DOCUMENT_FORMATS)
            raise AttachmentError(wording.FORMAT.format(accepted=accepted))
        extension = allowed_types[content_type]
        path = self.directory / f"inbound-{key}-{index}{extension}"
        size = 0
        try:
            with path.open("xb") as handle:
                os.chmod(path, 0o600)
                for chunk in response.iter_content(CHUNK_SIZE):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > limit:
                        raise AttachmentError(wording.TOO_LARGE.format(
                            megabytes=self.max_bytes // (1024 * 1024)))
                    handle.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            response.close()
        if size == 0:
            path.unlink(missing_ok=True)
            raise AttachmentError(wording.EMPTY)
        if not _content_matches(path, content_type):
            path.unlink(missing_ok=True)
            raise AttachmentError(wording.MISMATCH)
        return LocalAttachment(remote.kind, path.resolve(), content_type, size)
