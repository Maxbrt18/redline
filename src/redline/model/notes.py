"""Reading a note: PyYAML for the syntax, schema.py for the meaning, and a hard
refusal in between. A note that does not conform stops `vault-check.py`; it does not
reach Saturday morning.

THE BODY IS NEVER READ. The frontmatter holds the fact, the body holds the why.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from .errors import InputError
from .schema import Schema, SchemaError

FENCE = "---"
#: Frontmatter already read, per file and disk state (mtime, size): parsing hundreds of
#: notes with pure-Python YAML dominates a page load. A modified note changes mtime or
#: size and is read again.
_PARSED: dict[Path, tuple[tuple[int, int], dict]] = {}


class NoteError(InputError):
    """The note cannot be read, or does not conform. Names the file."""


def split(text: str) -> tuple:
    """(frontmatter text, body). Empty frontmatter if the note has none."""
    if not text.startswith(FENCE):
        return "", text
    end = text.find(f"\n{FENCE}", len(FENCE))
    if end == -1:
        return "", text
    return text[len(FENCE):end], text[end + len(FENCE) + 1:].lstrip("\n")


def frontmatter(path: Path) -> dict:
    """The frontmatter of a note, parsed. Raises NoteError, never a YAML trace.

    `safe_load` only: a vault note has no business instantiating Python objects.
    A copy is returned: the caller can modify it without touching the cache.
    """
    try:
        stat = path.stat()
        state = (stat.st_mtime_ns, stat.st_size)
        cached = _PARSED.get(path)
        if cached and cached[0] == state:
            return copy.deepcopy(cached[1])
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NoteError(f"{path.name} : illisible ({exc})") from exc
    head, _ = split(text)
    if not head.strip():
        raise NoteError(f"{path.name} : pas de frontmatter")
    try:
        data = yaml.safe_load(head)
    except yaml.YAMLError as exc:
        detail = str(exc).replace("\n", " ")
        raise NoteError(f"{path.name} : frontmatter illisible — {detail}") from exc
    if not isinstance(data, dict):
        raise NoteError(f"{path.name} : frontmatter : objet attendu")
    _PARSED[path] = (state, copy.deepcopy(data))
    return data


def read(path: Path, schema: Schema) -> dict:
    """Frontmatter validated against `schema`, or NoteError naming every problem.

    The file NAME is the key: `coaching/exercises/calf-raise-bent.md` is the exercise
    `calf-raise-bent`. One less field to keep in sync with itself.
    """
    data = frontmatter(path)
    try:
        return schema.validate(data, path.name)
    except SchemaError as exc:
        raise NoteError("\n  - ".join([f"{path.name} : note invalide :", *exc.problems])) from exc


def read_all(folder: Path, schema: Schema, key: str = "slug") -> dict:
    """Every note of a folder, keyed by file stem. Reports EVERY broken note,
    not the first: fixing a folder blind, one file per run, is how a validator
    gets abandoned."""
    notes, problems = {}, []
    for path in sorted(folder.glob("*.md")):
        try:
            notes[path.stem] = dict(read(path, schema), **{key: path.stem})
        except NoteError as exc:
            problems.append(str(exc))
    if problems:
        raise NoteError("\n".join(problems))
    return notes
