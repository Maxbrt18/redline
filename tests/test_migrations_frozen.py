"""An Alembic revision is a snapshot: it never rereads the current schema.

A migration that imported `redline.storage.schema` (or its constants) would create
TODAY's schema instead of the schema of its own revision: the next revision, which
evolves it, would find its tables already there and fail on any fresh database, while
existing databases would never have seen the step. Schema history is not rewritten.
"""

import ast

from conftest import ENGINE_ROOT

VERSIONS = ENGINE_ROOT / "migrations" / "versions"


def test_no_revision_reads_the_live_schema_declaration():
    offenders = []
    for path in sorted(VERSIONS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = (node.module or "") if isinstance(node, ast.ImportFrom) else ""
            names = [alias.name for alias in node.names] if isinstance(
                node, ast.Import | ast.ImportFrom) else []
            if module.startswith("redline") or any(name.startswith("redline") for name in names):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, "révision qui relit le schéma courant : " + ", ".join(offenders)


def test_the_control_sees_the_revisions():
    assert len(list(VERSIONS.glob("*.py"))) >= 1
