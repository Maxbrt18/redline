"""The image outbox: the channel through which the agent attaches charts.

The contract, in two invariants. A PNG written into the outbox (`paths.outbox_dir()`) during a run goes
out after the last text, then moves to outbox/sent/ -- and if the run failed or stayed
SILENT, it is archived WITHOUT being sent: never an image without its message. A `.link`
sidecar travels with its image everywhere, sent, archived and discarded with it: there
is no path in which a button goes out without its card.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TypeAlias

from redline.cards import archive

from .strength import read_sidecar, sidecar_for

log = logging.getLogger("redline")

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
KEEP_SENT = 20
KEEP_FAILED = 20
FileFingerprint: TypeAlias = tuple[int, int, int]
OutboxSnapshot: TypeAlias = dict[Path, FileFingerprint]


def _fingerprint(path: Path) -> FileFingerprint:
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


class Outbox:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._sent = root / "sent"
        self._failed = root / "failed"
        self._root.mkdir(parents=True, exist_ok=True)
        self._sent.mkdir(parents=True, exist_ok=True)
        self._failed.mkdir(parents=True, exist_ok=True)

    def target(self, filename: str) -> Path:
        """Chemin d'un nouvel artefact, sans laisser son producteur choisir ailleurs."""
        if not filename or Path(filename).name != filename:
            raise ValueError("nom d'artefact invalide")
        return self._root / filename

    def leftovers(self) -> list[Path]:
        """Return root images; at startup each is residue from an interrupted turn."""
        return sorted(self._images(), key=lambda path: path.stat().st_mtime)

    def snapshot(self) -> OutboxSnapshot:
        """Photo of the images present BEFORE a run: anything that appears on top
        of it afterwards was produced by that run."""
        return {path: _fingerprint(path) for path in self._images()}

    def collect_new(self, before: OutboxSnapshot) -> list[Path]:
        """Images created or replaced since `before`, oldest first."""
        fresh = [path for path in self._images()
                 if before.get(path) != _fingerprint(path)]
        return sorted(fresh, key=lambda path: path.stat().st_mtime)

    def link_for(self, image: Path):
        """(bubble text, URL) of the interactive page attached to this card, or
        None. Reading it is never fatal: a missing or malformed sidecar simply
        means the card goes out alone."""
        return read_sidecar(image)

    def archive(self, path: Path) -> None:
        """Move a processed image and its sidecar into ``sent/``."""
        self._move(path, self._sent, KEEP_SENT)

    def fail(self, path: Path) -> None:
        """Quarantine an attachment that Messenger refused in ``failed/``."""
        self._move(path, self._failed, KEEP_FAILED)

    @staticmethod
    def _move(path: Path, destination: Path, keep: int) -> None:
        sidecar = sidecar_for(path)
        try:
            path.replace(destination / path.name)
        except OSError as exc:
            log.warning("Cannot archive (%s): %s", path.name, exc)
            return
        descriptor = archive.sidecar(path)
        if descriptor.exists():
            descriptor.replace(destination / descriptor.name)
        if sidecar.exists():
            try:
                sidecar.replace(destination / sidecar.name)
            except OSError as exc:
                log.warning("Cannot archive (%s): %s", sidecar.name, exc)
        kept = sorted((p for p in destination.iterdir()
                       if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in kept[keep:]:
            for target in (sidecar_for(stale), archive.sidecar(stale), stale):
                with contextlib.suppress(OSError):
                    target.unlink()

    def _images(self):
        for path in self._root.iterdir():
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                yield path
