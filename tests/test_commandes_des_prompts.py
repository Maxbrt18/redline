"""Toute commande nommée dans un prompt doit exister et accepter ce qu'on lui dicte.

Un prompt qui cite une commande fausse ne casse pas au déploiement : il casse plus
tard, chez Sam, au milieu d'un débrief. Le 2026-08-14 le déclencheur de débrief
proposait `... | scripts/descent.py` — hors de l'unique lanceur autorisé, donc refusé,
donc déguisé en panne d'accès Strava. Le même jour, un scénario de test appelait
`fact-write.py daily --day` alors que l'option s'appelle `--date`.

Ces deux fautes sont invisibles aux tests unitaires : les prompts sont des chaînes,
personne ne les exécute. Ici on les lit comme ce qu'elles sont — des ORDRES — et on
vérifie que chacun est exécutable : le script existe, son `--help` passe, et les
sous-commandes et options citées y figurent vraiment.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess

import pytest
from conftest import ENGINE_ROOT, cli_command

from redline.service import prompts

# Les commandes vivent dans des `spans` entre accents graves. On ne parse donc pas
# la prose : on lit ce que le prompt présente lui-même comme une commande.
SPAN = re.compile(r"`([^`]+)`")
SCRIPT = re.compile(
    r"/usr/local/bin/redline-agent-cli\s+([a-z-]+\.py)((?:\s+[a-z][a-z-]*)*)")


def _prompts() -> dict[str, str]:
    """Tous les textes injectés au coach, réunis sous un nom lisible."""
    textes = {
        "STYLE_PROMPT": prompts.STYLE_PROMPT,
        "TRIAGE_RULES": prompts.TRIAGE_RULES,
        "EMOTION_NOTE": prompts.EMOTION_NOTE,
        "STRENGTH_CARD_POINTER": prompts.STRENGTH_CARD_POINTER,
        "STRENGTH_REPORT_NOTE": prompts.STRENGTH_REPORT_NOTE,
        "stale_plan_note": prompts.stale_plan_note("2026-W33"),
        # Rapatriés de `bot.py` le 2026-08-19 : ils citent des commandes, donc ils
        # entrent ici comme les autres.
        "canonical_plan_note": prompts.canonical_plan_note(""),
        "active_revision_note": prompts.active_revision_note(""),
        "missing_cells_note": prompts.missing_cells_note(
            [{"date": "2026-08-18", "type": "easy", "missing": ["RPE", "D-"]}],
            [{"date": "2026-08-12", "type": "easy", "missing": ["Plaisir"]}]),
    }
    textes.update({f"DEPTH_NOTES[{k}]": v for k, v in prompts.DEPTH_NOTES.items()})
    textes.update({f"PROACTIVE_TRIGGERS[{k}]": v.replace("{detail}", "42")
                   for k, v in prompts.PROACTIVE_TRIGGERS.items()})
    return textes


def _commandes() -> list[tuple[str, str, str, tuple[str, ...]]]:
    """(origine, script, sous-commandes, options) pour chaque commande citée.

    Le découpage sur les tubes n'est pas un détail : sans lui, le `--dplus` de
    `descent.py` était attribué à `strava.py` dans le pipe du débrief, et le test
    accusait le mauvais script d'ignorer une option qu'il n'a jamais eue.
    """
    trouvees = []
    for origine, texte in _prompts().items():
        plat = " ".join(texte.split())
        for span in SPAN.findall(plat):
            for segment in span.split("|"):
                correspondance = SCRIPT.search(segment)
                if not correspondance:
                    continue
                script = "scripts/" + correspondance.group(1)
                suite = correspondance.group(2)
                options = tuple(sorted(set(re.findall(r"(--[a-z][a-z-]+)", segment))))
                trouvees.append((origine, script, suite.strip(), options))
    return trouvees


COMMANDES = _commandes()


def test_les_prompts_citent_bien_des_commandes():
    """Garde-fou du garde-fou : si l'extraction casse, tout le fichier passerait au
    vert en ne vérifiant rien."""
    assert len(COMMANDES) >= 10
    scripts = {script for _, script, _, _ in COMMANDES}
    assert "scripts/strava.py" in scripts
    assert "scripts/week-plan.py" in scripts


@pytest.mark.parametrize("origine,script,suite,options", COMMANDES,
                         ids=[f"{o}:{s}" for o, s, _, _ in COMMANDES])
def test_chaque_commande_citee_est_executable(origine, script, suite, options):
    commande = cli_command(script.removeprefix("scripts/").removesuffix(".py"))
    assert importlib.util.find_spec(commande[-1]), f"{origine} cite {script}, qui n'existe pas"

    aide = subprocess.run([*commande, "--help"], cwd=ENGINE_ROOT,
                          capture_output=True, text=True, check=False)
    assert aide.returncode == 0, (
        f"{origine} cite {script}, dont --help échoue : {aide.stderr[:200]}")

    texte_aide = aide.stdout + aide.stderr
    for sous_commande in suite.split():
        # `show/check` et consorts sont écrits en toutes lettres dans les prompts ;
        # une sous-commande absente de l'aide est une commande que le coach ne peut
        # pas lancer.
        assert sous_commande in texte_aide, (
            f"{origine} cite `{script} {sous_commande}`, absent de son aide")

    for option in options:
        detaillee = subprocess.run(
            [*commande, *suite.split(), "--help"], cwd=ENGINE_ROOT,
            capture_output=True, text=True, check=False)
        aide_complete = texte_aide + detaillee.stdout + detaillee.stderr
        assert option in aide_complete, (
            f"{origine} cite `{option}` pour {script}, qui ne la connaît pas")


def test_toute_commande_passe_par_le_lanceur_autorise():
    """Le prompt et ``dontAsk`` doivent décrire exactement le même chemin.

    Le 26/08, la doctrine disait ``scripts/ledger-add.py`` alors que Bash n'autorisait
    que ``redline-agent-cli``. Le refus a ensuite été raconté comme une panne de
    sauvegarde. Toute mention d'une CLI métier est donc normalisée, pas seulement les
    exemples qui portent des options.
    """
    fautes = []
    for origine, texte in _prompts().items():
        plat = " ".join(texte.split())
        for span in SPAN.findall(plat):
            for segment in span.split("|"):
                if ".py" not in segment or "coaching/" in segment:
                    continue
                if ("scripts/" in segment or re.search(r"\b[a-z-]+\.py\b", segment)) \
                        and prompts.AGENT_CLI not in segment:
                    fautes.append(f"{origine} : `{span[:90]}`")
    assert not fautes, "commandes hors lanceur :\n  " + "\n  ".join(fautes)


def test_toute_commande_citee_est_dans_la_politique_du_lanceur():
    autorisees = {
        line for line in (ENGINE_ROOT / "deploy" / "agent-cli-tools.txt").read_text().splitlines()
        if line and not line.startswith("#")
    }
    citees = {script.removeprefix("scripts/") for _, script, _, _ in COMMANDES}
    assert citees <= autorisees
