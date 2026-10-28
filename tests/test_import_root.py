"""One import root: the installed `redline` package, never a path pushed onto `sys.path`.

A module reachable under two names is loaded twice, with two copies of its classes: an
exception raised under one name is not caught under the other.
"""

import ast

from conftest import ENGINE_ROOT

SOURCES = ENGINE_ROOT / "src" / "redline"


def _modules():
    for path in sorted(SOURCES.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def test_no_module_touches_the_import_path():
    offenders = [f"{path.relative_to(ENGINE_ROOT)}:{node.lineno}"
                 for path, tree in _modules() for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute) and ast.unparse(node) == "sys.path"]
    assert offenders == []


def test_every_absolute_import_of_the_engine_goes_through_redline():
    roots = {"scripts", "model", "storage", "evaluation", "server"}
    offenders = [f"{path.relative_to(ENGINE_ROOT)}:{node.lineno}"
                 for path, tree in _modules() for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
                 and node.module.split(".")[0] in roots]
    assert offenders == []
