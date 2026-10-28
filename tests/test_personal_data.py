"""The public engine carries no trace of a real athlete.

Two rules over every tracked text file:

- no word of a private deny list. The list is stored as SHA-256 digests so that the test
  does not publish the words it protects; a new term is added as
  `hashlib.sha256(word.lower().encode()).hexdigest()`;
- outside `tests/`, no date before 2030. Fixtures, samples and format examples use
  dates from 2030 on, which no coaching history can contain; a real date is history,
  and history belongs in the private vault.

`migrations/` is frozen until the revisions are squashed for the first release.
"""

import hashlib
import re
import subprocess

from conftest import ENGINE_ROOT

FORBIDDEN = {
    "8fe9c2c44847ab57", "a33c1afd3c8de742", "85ba1d26a6f28a7c", "e13bc4a84d4f0b33",
    "61067a6030809a78", "b49c27ffc717aed8", "0eeb83bd6ea81aaa", "3cbcaece45697b98",
    "76b4b08b00dacd25", "b1a0f1c99f5e843c", "4c0d617c974a763f", "86009cc361823fe7",
}
FROZEN = ("migrations/",)
TOKEN = re.compile(r"[\w-]+")
PAST_DATE = re.compile(
    r"\b20[0-2]\d-(?:\d\d-\d\d|\d\d\b|W\d\d)"
    r"|(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre"
    r"|décembre) 20[0-2]\d", re.IGNORECASE)


def _tracked_texts():
    names = subprocess.run(["git", "ls-files"], cwd=ENGINE_ROOT, check=True,
                           capture_output=True, text=True).stdout.splitlines()
    for name in names:
        if name.startswith(FROZEN):
            continue
        try:
            yield name, (ENGINE_ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue


def _digest(word: str) -> str:
    return hashlib.sha256(word.encode()).hexdigest()[:16]


def test_no_private_word_is_published():
    found = set()
    for name, text in _tracked_texts():
        for token in TOKEN.findall(text.lower()):
            for word in {token, *token.split("-")}:
                if _digest(word) in FORBIDDEN:
                    found.add(f"{name}: {_digest(word)}")
    assert not found, sorted(found)


def test_no_real_date_outside_the_tests():
    found = [f"{name}: {match}"
             for name, text in _tracked_texts() if not name.startswith("tests/")
             for match in PAST_DATE.findall(text)]
    assert not found, found
