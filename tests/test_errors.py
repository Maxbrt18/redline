"""The failure model: one root, four categories, one exit point for the CLIs."""

from __future__ import annotations

import ast
import os
import subprocess

import pytest
from conftest import ENGINE_ROOT, OPERATOR_TOOLS, cli_command

from redline.model import entrypoint
from redline.model.errors import (
    ConfigError,
    ExternalError,
    InputError,
    RedlineError,
    StorageError,
)

CATEGORIES = {"RedlineError", "InputError", "ConfigError", "ExternalError", "StorageError"}
BUILTIN_FAILURES = {"Exception", "BaseException", "ValueError", "RuntimeError", "TypeError",
                    "KeyError", "LookupError", "OSError"}
SOURCES = ("src",)


def _engine_files():
    for root in SOURCES:
        for path in sorted((ENGINE_ROOT / root).rglob("*.py")):
            if "tests" not in path.parts:
                yield path


def _public_failures_outside_home() -> list[str]:
    offenders = []
    for path in _engine_files():
        if path.name == "errors.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
                continue
            bases = {ast.unparse(base).rsplit(".", 1)[-1] for base in node.bases}
            if bases & BUILTIN_FAILURES:
                offenders.append(f"{path.relative_to(ENGINE_ROOT)}:{node.lineno} {node.name}")
    return offenders


def test_every_public_failure_derives_from_a_category():
    assert _public_failures_outside_home() == []


def test_the_scan_sees_the_engine_failures():
    names = {node.name
             for path in _engine_files()
             for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
             if isinstance(node, ast.ClassDef)}
    assert {"LedgerError", "SchemaError", "StravaError", "InstanceError"} <= names


@pytest.mark.parametrize(("category", "builtin"), [
    (InputError, ValueError), (ConfigError, ValueError),
    (ExternalError, RuntimeError), (StorageError, RuntimeError)])
def test_a_category_is_still_caught_as_the_builtin_it_replaced(category, builtin):
    assert issubclass(category, RedlineError) and issubclass(category, builtin)


def test_run_turns_a_failure_into_its_message_and_exit_code_one(capsys):
    def main():
        raise InputError("refused input")

    with pytest.raises(SystemExit) as stopped:
        entrypoint.run(main)
    assert stopped.value.code == 1
    assert capsys.readouterr().err == "refused input\n"


def test_run_keeps_the_exit_code_main_returns():
    with pytest.raises(SystemExit) as stopped:
        entrypoint.run(lambda: 3)
    assert stopped.value.code == 3


def test_run_lets_a_bug_keep_its_traceback():
    def main():
        raise KeyError("bug")

    with pytest.raises(KeyError):
        entrypoint.run(main)


def test_a_cli_refusal_reaches_the_operator_without_a_traceback():
    env = {key: value for key, value in os.environ.items() if key != "REDLINE_TURN_ID"}
    env["DATABASE_URL"] = "postgresql+psycopg://unused/unused"
    result = subprocess.run(
        [*cli_command("fact-write"), "angle", "titre Strava"],
        cwd=ENGINE_ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "tour" in result.stderr



def test_only_the_entry_point_exits():
    exits = [f"{path.relative_to(ENGINE_ROOT)}:{node.lineno}"
             for path in sorted((ENGINE_ROOT / "src" / "redline").rglob("*.py"))
             if not {"service", "evaluation"} & set(path.parts)
             and path.name not in {"entrypoint.py", *OPERATOR_TOOLS}
             for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
             if isinstance(node, ast.Call)
             and ast.unparse(node.func) in {"sys.exit", "SystemExit", "exit"}]
    assert exits == []
