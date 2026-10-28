"""Identité documentaire typée voyageant avec une image, jamais extraite de sa prose."""

import hashlib
import json
from pathlib import Path

SUFFIX = ".card.json"


def sidecar(image: Path) -> Path:
    return image.with_name(image.name + SUFFIX)


def write(image: Path, *, kind: str, title: str, period: str = "") -> None:
    sidecar(image).write_text(json.dumps({
        "kind": kind, "title": title, "period": period,
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
    }, ensure_ascii=False), encoding="utf-8")


def read(image: Path) -> dict:
    try:
        value = json.loads(sidecar(image).read_text(encoding="utf-8"))
        if (not isinstance(value, dict)
                or value.get("kind") not in {"weekly", "monthly", "other"}
                or not isinstance(value.get("title"), str)
                or not isinstance(value.get("period"), str)
                or value.get("sha256") != hashlib.sha256(image.read_bytes()).hexdigest()):
            return {}
        return value
    except (OSError, ValueError):
        return {}
