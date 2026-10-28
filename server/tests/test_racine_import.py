"""UNE seule racine d'import pour l'arbre `scripts/`, et un contrôle qui le tient.

⭐ 2026-08-18. Le dépôt a longtemps porté deux racines pour le même code : les CLI
importaient `model.weekly_plan`, le service et `scripts/storage/` importaient
`scripts.model.weekly_plan`. Comme `python3 scripts/foo.py` met `scripts/` en tête de
`sys.path`, les deux marchaient -- et chargeaient DEUX objets-modules distincts, avec
deux jeux de classes. Vérifié à l'époque : `model.schema.SchemaError is not
scripts.model.schema.SchemaError`. Un `except SchemaError` posé d'un côté ne rattrapait
donc pas celui levé de l'autre, et `pyproject.toml` documentait la double racine comme
la raison pour laquelle mypy ne pouvait pas couvrir les CLI.

Rien n'était encore tombé : tous les franchissements de frontière attrapaient
`Exception`. C'est exactement le genre de piège qui attend le jour où quelqu'un écrit
le `except` juste.

Le porteur ne peut pas être une ligne de doctrine : `scripts/` reste en tête de
`sys.path` à chaque exécution directe, donc l'ancienne forme marchera toujours. Ce
sont donc ces deux tests qui l'interdisent.
"""

from __future__ import annotations

import ast
import re

from conftest import ENGINE_ROOT

SCRIPTS = ENGINE_ROOT / "scripts"

# Les paquets qui vivent sous `scripts/` : leur nom complet commence par `scripts.`.
PACKAGES = {"model", "cards", "storage"}
# Les modules `scripts/*.py` importables (sans tiret), qui s'importent aussi par là.
MODULES = {path.stem for path in SCRIPTS.glob("*.py")
           if path.stem.isidentifier() and path.stem != "__init__"}


def _sources():
    for folder in (SCRIPTS, ENGINE_ROOT / "server" / "redline", ENGINE_ROOT / "server" / "tests"):
        for path in sorted(folder.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def test_personne_nimporte_le_meme_module_sous_deux_noms():
    """`from model.x import` ou `import ledger` : la même chose sous un autre nom."""
    interdits = PACKAGES | MODULES
    fautes = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                racine = (node.module or "").split(".")[0]
                if node.level == 0 and racine in interdits:
                    fautes.append(f"{path.relative_to(ENGINE_ROOT)}:{node.lineno} "
                                  f"from {node.module} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in interdits:
                        fautes.append(f"{path.relative_to(ENGINE_ROOT)}:{node.lineno} "
                                      f"import {alias.name}")
    assert not fautes, (
        "importe sous l'ancienne racine, donc charge une SECONDE copie du module avec "
        "ses propres classes :\n  " + "\n  ".join(fautes)
        + "\nÉcris `from scripts.model...`, `from scripts.cards...`, "
          "`from scripts import ledger`.")


def test_chaque_cli_pose_la_racine_avant_dimporter_maison():
    """Un `from scripts.` en tête de fichier exige la racine du dépôt sur `sys.path`.

    Sans ce bootstrap le script marche sous pytest (qui porte `.`) et meurt lancé à la
    main -- c'est-à-dire partout où le coach l'appelle.
    """
    boot = re.compile(r"ROOT = Path\(__file__\)\.resolve\(\)\.parent\.parent\s*\n"
                      r"if str\(ROOT\) not in sys\.path:\s*\n"
                      r"\s+sys\.path\.insert\(0, str\(ROOT\)\)")
    manquants = []
    for path in sorted(SCRIPTS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "from scripts" not in source and "import scripts" not in source:
            continue
        if not boot.search(source):
            manquants.append(str(path.relative_to(ENGINE_ROOT)))
    assert not manquants, (
        "importe `scripts.*` sans poser la racine du dépôt sur sys.path : "
        + ", ".join(manquants))


def test_le_controle_nest_pas_vide():
    """Deux tests verts sur un corpus vide diraient la même chose que deux tests justes."""
    fichiers = list(_sources())
    assert len(fichiers) > 60, len(fichiers)
    assert {"ledger", "signals", "strava", "weather"} <= MODULES
    assert any("from scripts.model" in path.read_text(encoding="utf-8")
               for path in SCRIPTS.glob("*.py"))
