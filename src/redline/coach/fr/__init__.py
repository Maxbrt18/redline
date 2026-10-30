"""The coach's texts in French.

`prompts/` holds every text as Markdown, a `{name}` standing for a value the service
fills in. A file under `prompts/notes/` is one paragraph wrapped for reading: its line
breaks are read as spaces. `labels.py` holds the short labels looked up by key.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"


@cache
def prompt(name: str) -> str:
    """`prompts/<name>.md` as written, without the file's final newline."""
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").removesuffix("\n")


def note(name: str) -> str:
    """`prompts/notes/<name>.md` as the single paragraph it is."""
    return " ".join(prompt(f"notes/{name}").split("\n"))


def fill(template: str, **values: str) -> str:
    """`template` with each `{name}` replaced by its value."""
    for name, value in values.items():
        template = template.replace("{" + name + "}", value)
    return template
