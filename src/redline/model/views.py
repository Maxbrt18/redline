"""Generated views: the only legitimate way for a fact to appear twice.

A view is generated between markers, and `--check` fails if the file was edited by
hand. Derivation runs data -> prose, never the other way round.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from .errors import InputError


def csv_rows(path: Path) -> list:
    """Les lignes d'une vue CSV, cellules vides retirées : un appelant lit `row.get(...)`,
    jamais une valeur silencieusement plausible (comme au registre)."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [{key: value.strip() for key, value in entry.items()
                 if key and value and value.strip()}
                for entry in csv.DictReader(handle)]


class ViewDrift(InputError):
    """The generated block no longer matches its source."""


def markers(name: str) -> tuple:
    return f"<!-- {name}_START -->", f"<!-- {name}_END -->"


def _block(text: str, name: str) -> re.Match:
    start, end = markers(name)
    found = re.search(f"{re.escape(start)}(.*?){re.escape(end)}", text, re.DOTALL)
    if found is None:
        raise ViewDrift(f"marqueurs « {name} » absents — attendus :\n{start}\n{end}")
    return found


def render(text: str, name: str, content: str) -> str:
    """The text with the `name` block replaced by `content`."""
    found = _block(text, name)
    start, end = markers(name)
    # Slicing, not re.sub: a backslash or a \1 in the content would be read as a
    # replacement group. Generated content contains arbitrary prose.
    return text[:found.start()] + f"{start}\n{content.strip()}\n{end}" + text[found.end():]


def current(text: str, name: str) -> str:
    return _block(text, name).group(1).strip()


def apply(path: Path, name: str, content: str, check: bool = False) -> bool:
    """Write the block (or, with `check`, only report). True if it had drifted.

    `check` is what `vault-check.py` calls: it never writes, so a control can
    never silently repair what it was supposed to flag.
    """
    text = path.read_text(encoding="utf-8")
    if current(text, name) == content.strip():
        return False
    if not check:
        path.write_text(render(text, name, content), encoding="utf-8")
    return True


def table(headers: list, rows: list) -> str:
    """A Markdown table. Cells are escaped for the one character that would
    break the grid."""
    def cells(values):
        return "| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |"

    return "\n".join([cells(headers), "|" + "---|" * len(headers)]
                     + [cells(row) for row in rows])
