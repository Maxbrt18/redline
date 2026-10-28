"""Une révision Alembic est un instantané : elle ne relit jamais le schéma courant.

Une migration qui importerait `redline.storage.schema` (ou ses constantes) créerait
le schéma DU JOUR au lieu de celui de sa date : la révision suivante, qui le fait
évoluer, trouverait déjà ses tables et échouerait sur toute base neuve, tandis que
les bases existantes, elles, n'auraient jamais vu l'étape. L'histoire du schéma ne
se réécrit pas.
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
