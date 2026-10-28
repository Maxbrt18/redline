"""Comments state invariants in English: no dates, no emoji, no French prose.

History belongs in commit messages; a date in a comment is a journal entry that rots.
Athlete-facing French lives in string literals under `src/redline/coach/fr/`, never in
comments or docstrings.

The cleanup goes directory by directory. `PENDING` lists what is not done yet; an entry
leaves the list as soon as it is clean, and the test says so.
"""

import ast
import io
import re
import tokenize
from pathlib import Path

from conftest import ENGINE_ROOT

ROOTS = ("src", "deploy", "migrations", "tests", ".github", "pyproject.toml")
HASH_COMMENTED = {".sh", ".toml", ".yml", ".yaml", ".ini", ".txt", ".example", ".cfg"}
SLASH_COMMENTED = {".js", ".css", ".html"}
SKIPPED = ("tests/fixtures/",)

PENDING = (
    ".github",
    "deploy",
    "migrations",
    "pyproject.toml",
    "src/redline/analysis",
    "src/redline/cards",
    "src/redline/cli",
    "src/redline/evaluation",
    "src/redline/integrations",
    "tests",
)

DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
EMOJI = re.compile("[☀-➿\U0001f300-\U0001faff]")
# French function words that English never uses as words. Two distinct ones make a
# French sentence; one alone is usually a quoted label or a proper noun.
FRENCH = re.compile(
    r"\b(le|la|les|des|du|une|est|sont|pour|qui|que|dans|pas|sur|avec|ne|se|ce|au|aux"
    r"|il|elle|mais|où|déjà|être|sans|quand|donc|car|très)\b",
    re.IGNORECASE,
)
SLASH_COMMENT = re.compile(r"/\*.*?\*/|(?:^|(?<=\s))//[^\n]*|<!--.*?-->", re.DOTALL)


def is_french(text: str) -> bool:
    return len({word.lower() for word in FRENCH.findall(text)}) >= 2


def _python_comments(source: str):
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            yield token.start[0], token.string
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            docstring = ast.get_docstring(node, clean=False)
            if docstring is not None:
                yield node.body[0].lineno, docstring


def _comments(path: Path):
    if path.suffix == ".py":
        yield from _python_comments(path.read_text(encoding="utf-8"))
    elif path.suffix in SLASH_COMMENTED:
        source = path.read_text(encoding="utf-8")
        for match in SLASH_COMMENT.finditer(source):
            yield source.count("\n", 0, match.start()) + 1, match.group()
    elif path.suffix in HASH_COMMENTED or path.name == "Caddyfile":
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if line.lstrip().startswith("#"):
                yield number, line


def _files():
    for root in ROOTS:
        base = ENGINE_ROOT / root
        for path in [base] if base.is_file() else sorted(base.rglob("*")):
            relative = path.relative_to(ENGINE_ROOT).as_posix()
            if path.is_file() and not relative.startswith(SKIPPED):
                yield relative, path


def _violations():
    for relative, path in _files():
        for line, text in _comments(path):
            for rule, broken in (("date", DATE.search(text)), ("emoji", EMOJI.search(text)),
                                 ("french", is_french(text))):
                if broken:
                    yield relative, line, rule


def _pending(relative: str) -> str | None:
    return next((entry for entry in PENDING
                 if relative == entry or relative.startswith(entry + "/")), None)


def test_comments_follow_the_conventions():
    offenders = [f"{relative}:{line} {rule}" for relative, line, rule in _violations()
                 if _pending(relative) is None]
    assert offenders == []


def test_every_pending_entry_still_has_work():
    dirty = {_pending(relative) for relative, _, _ in _violations()}
    assert sorted(set(PENDING) - dirty) == []


def test_the_french_detector_needs_a_sentence():
    assert is_french("La séance est validée avant le bilan.")
    assert not is_french("Persist the turn before delivering the reply.")
    assert not is_french("Map `séance` to `session` in the payload.")
