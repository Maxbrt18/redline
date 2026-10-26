"""Frontière sûre entre une pièce jointe Meta et le moteur de coaching.

Le webhook ne télécharge rien : il persiste l'URL signée par Meta puis rend la main.
Le worker télécharge ensuite dans un fichier privé, borné et éphémère. Le contexte
manager est volontaire : même une exception du modèle passe par ``finally`` et retire
le brut. Aucun contenu reçu n'est une instruction pour l'agent.
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


class AttachmentError(RuntimeError):
    """Erreur présentable à l'athlète, sans recopier URL ni réponse distante."""


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
    """Réduit la forme Meta à notre contrat durable minimal."""
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
    """Trace relationnelle sans URL, nom de fichier ni contenu brut."""
    try:
        payload = json.loads(raw)
        kinds = {str(row.get("type") or "") for row in payload.get("attachments", [])}
    except (TypeError, ValueError, AttributeError):
        kinds = set()
    labels = []
    if "image" in kinds:
        labels.append("photo")
    if "file" in kinds:
        labels.append("document")
    if kinds & {"video", "audio"}:
        labels.append("média non pris en charge")
    return "[Pièce jointe : " + (" + ".join(labels) or "format inconnu") + "]"


def _remote_rows(raw: str) -> tuple[list[RemoteAttachment], str]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise AttachmentError("Cette pièce jointe est illisible. Renvoie-la une fois.") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise AttachmentError("Cette pièce jointe utilise un format que je ne reconnais pas.")
    rows = payload.get("attachments")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ATTACHMENTS:
        raise AttachmentError(
            f"Envoie entre 1 et {MAX_ATTACHMENTS} photos ou documents à la fois.")
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            raise AttachmentError("Cette pièce jointe est incomplète. Renvoie-la une fois.")
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
    """Télécharge des photos/PDF/texte sous quota et les détruit après lecture."""

    def __init__(self, directory: Path, *, max_bytes: int, total_bytes: int,
                 session: requests.Session | None = None) -> None:
        self.directory = Path(directory)
        self.max_bytes = int(max_bytes)
        self.total_bytes = int(total_bytes)
        self._session = session or requests.Session()

    def cleanup(self) -> int:
        """Supprime uniquement notre répertoire d'éphémères, jamais un chemin large."""
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
            raise AttachmentError("Ce type de pièce jointe n'est pas pris en charge.")
        if not allowed:
            if ignored_video:
                raise AttachmentError(
                    "Je ne traite pas les vidéos ni l'audio : l'analyse serait trop peu "
                    "précise. Envoie plutôt une photo nette ou un document.")
            raise AttachmentError("Envoie une photo, un PDF ou un fichier texte.")

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
                # Le prochain démarrage nettoiera un résidu ; un unlink momentanément
                # impossible ne masque jamais la réponse du modèle.

    def caption(self, raw: str) -> str:
        _, caption = _remote_rows(raw)
        return caption

    def _download(self, remote: RemoteAttachment, key: str, index: int, *,
                  remaining: int) -> LocalAttachment:
        if not remote.url or not _safe_host(remote.url):
            raise AttachmentError("L'adresse de cette pièce jointe n'est pas une URL Meta sûre.")
        if remaining <= 0:
            raise AttachmentError("L'ensemble des pièces jointes dépasse la limite autorisée.")

        url = remote.url
        response = None
        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = self._session.get(
                    url, stream=True, timeout=(5, 30), allow_redirects=False,
                    headers={"User-Agent": "Redline-Coach/1.0"})
            except requests.RequestException as exc:
                raise AttachmentError(
                    "Le téléchargement Meta a échoué. Renvoie le fichier dans un instant.") from exc
            if response.status_code not in REDIRECT_CODES:
                break
            location = response.headers.get("Location", "")
            response.close()
            if not _safe_host(location):
                raise AttachmentError("La redirection de la pièce jointe n'est pas sûre.")
            url = location
        if response is None or response.status_code != 200:
            if response is not None:
                response.close()
            raise AttachmentError("Meta ne me laisse plus télécharger ce fichier. Renvoie-le.")

        try:
            declared = int(response.headers.get("Content-Length") or 0)
        except ValueError:
            declared = 0
        limit = min(self.max_bytes, remaining)
        if declared > limit:
            response.close()
            raise AttachmentError(
                f"Ce fichier dépasse la limite de {self.max_bytes // (1024 * 1024)} Mo.")
        content_type = _media_type(response.headers.get("Content-Type", ""))
        allowed_types = ALLOWED_IMAGE_TYPES if remote.kind == "image" else ALLOWED_DOCUMENT_TYPES
        if content_type not in allowed_types:
            response.close()
            accepted = "JPEG, PNG ou WebP" if remote.kind == "image" else "PDF, TXT ou CSV"
            raise AttachmentError(f"Format non pris en charge. Formats acceptés : {accepted}.")
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
                        raise AttachmentError(
                            f"Ce fichier dépasse la limite de "
                            f"{self.max_bytes // (1024 * 1024)} Mo.")
                    handle.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            response.close()
        if size == 0:
            path.unlink(missing_ok=True)
            raise AttachmentError("Le fichier reçu est vide.")
        if not _content_matches(path, content_type):
            path.unlink(missing_ok=True)
            raise AttachmentError(
                "Le contenu réel du fichier ne correspond pas au format annoncé.")
        return LocalAttachment(remote.kind, path.resolve(), content_type, size)
