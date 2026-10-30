"""The engine writes English; only the coach's language speaks French.

A French string literal belongs under `src/redline/coach/fr/` (what the coach and the
athlete read) or in the notebook page, `src/redline/service/page/`, which is the
athlete's interface and keeps its words inline. Everywhere else -- logs, errors, argparse
help, operator output, test failure messages -- the text is English.

`test_conventions.py` holds the same line for comments and docstrings.
"""

import ast
import re
from pathlib import Path

from conftest import ENGINE_ROOT
from test_conventions import is_french

SOURCE = ENGINE_ROOT / "src" / "redline"
COACH_LANGUAGE = (SOURCE / "coach" / "fr", SOURCE / "service" / "page")
ACCENT = re.compile(r"[àâçéèêëîïôûùüœ]", re.IGNORECASE)
# French on purpose, outside the coach's language: what the athlete types, the notebook's
# routes, the token the persona fills in, and patterns that must match French text.
ALLOWED = {
    "/carnet", "/carnet-revoquer", "/renfo", "carnet-theme",
    "⟨athlète⟩", "oui", "non", "’",
    r"^[AEIOUYÂÊÎÔÛÉÈÀHaeiouyâêîôûéèàh]", r"\b[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)*\b",
}


def is_french_text(text: str) -> bool:
    return bool(ACCENT.search(text)) or is_french(text)


def _strings(node: ast.AST):
    """The literal text of a constant or an f-string, with `{}` for each expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}"
                       for part in node.values)
    return None


def _docstrings(tree: ast.AST) -> set[int]:
    """The ids of docstring nodes: `test_conventions.py` judges them."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _french_literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skipped = _docstrings(tree)
    inside_fstring = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
                      for part in node.values}
    for node in ast.walk(tree):
        if id(node) in skipped or id(node) in inside_fstring:
            continue
        text = _strings(node)
        if text and text.strip() not in ALLOWED and is_french_text(text):
            yield node.lineno, text


def _failure_messages(path: Path):
    """What a test prints when it fails: an assert message, an AssertionError, a
    pytest.fail. Test data may imitate the coach; these messages speak to a developer."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        message = None
        if isinstance(node, ast.Assert):
            message = node.msg
        elif (isinstance(node, ast.Call) and node.args
              and getattr(node.func, "attr", getattr(node.func, "id", "")) in {"AssertionError", "fail"}):
            message = node.args[0]
        while isinstance(message, ast.BinOp):
            message = message.left
        text = _strings(message) if message is not None else None
        if text and is_french_text(text):
            yield node.lineno, text


def test_the_engine_writes_english_outside_the_coach_language():
    offenders = [
        f"{path.relative_to(ENGINE_ROOT)}:{line}: {text[:80]!r}"
        for path in sorted(SOURCE.rglob("*.py"))
        if not any(path.is_relative_to(root) for root in COACH_LANGUAGE)
        for line, text in _french_literals(path)
    ]
    assert not offenders, "\n".join(offenders)


def test_a_failing_test_explains_itself_in_english():
    offenders = [
        f"{path.relative_to(ENGINE_ROOT)}:{line}: {text[:80]!r}"
        for path in sorted((ENGINE_ROOT / "tests").rglob("*.py"))
        if "fixtures" not in path.parts
        for line, text in _failure_messages(path)
    ]
    assert not offenders, "\n".join(offenders)


def test_the_detector_sees_french_and_leaves_english_alone():
    assert is_french_text("Séance de renfo terminée")
    assert is_french_text("le programme est dans la note")
    assert not is_french_text("the plan must stay readable")
    assert not is_french_text("RPE 6-7, cadence ~170")
