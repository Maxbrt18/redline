"""La carte de renfo, de la sélection typée à la page — sans base ni navigateur.

Le mot du matin ordonne cette carte les jours de renfo, et le coach n écrit ni
exercices ni doses : il envoie une SÉLECTION que le script résout depuis
`coaching/exercises/` et `coaching/workouts/`. Un slug renommé dans le vault casse
donc la carte sans qu aucun test de logique s en aperçoive.

Le rendu PNG et le lien signé ont leur propre job CI, avec Chromium
(`test_charts.py`). Ici c est la CHAÎNE DE RÉSOLUTION qu on rejoue, en mode `--html`,
pour qu elle tourne aussi dans le job rapide — celui qui bloque le déploiement.
"""

from __future__ import annotations

import json
import subprocess
import sys

from conftest import VAULT_ROOT


def reussit(process: subprocess.CompletedProcess, geste: str) -> subprocess.CompletedProcess:
    assert process.returncode == 0, f"{geste} a échoué : {process.stderr or process.stdout}"
    return process


def carte(selection: dict, sortie) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/chart-strength.py", "--html", "-o", str(sortie)],
        cwd=VAULT_ROOT, input=json.dumps(selection),
        capture_output=True, text=True, check=False)


def test_la_carte_de_renfo_se_genere_depuis_une_selection_typee(tmp_path):
    """Le mot du matin ordonne cette carte les jours de renfo. Le coach n'écrit ni
    exercices ni doses : il envoie une SÉLECTION, et le script la résout depuis
    `coaching/exercises/` et `coaching/workouts/`. Un slug renommé dans le vault
    casserait donc la carte sans qu'aucun test de rendu ne s'en aperçoive.

    Mode `--html` : la même chaîne de résolution, sans exiger Chromium du job rapide.
    Le rendu PNG et le lien signé ont leur propre job (`test_charts.py`).
    """
    for session in ("a", "b"):
        sortie = tmp_path / f"renfo-{session}.html"
        process = carte({"session": session, "date": "2026-08-14"}, sortie)
        reussit(process, f"carte renfo {session}")
        assert sortie.exists() and sortie.stat().st_size > 1000


def test_la_carte_accepte_les_modulations_que_le_matin_prescrit(tmp_path):
    """Demi-dose, retrait ponctuel et garde-fou injury : les trois leviers que le
    déclencheur du matin nomme. S'ils cessent d'être acceptés, le coach se rabat sur
    une carte pleine dose un jour où le injury parle."""
    sortie = tmp_path / "renfo-module.html"
    # Le `gate` retire CONDITIONNELLEMENT ce que `drop` retirerait tout de suite :
    # cumuler les deux est refusé, et à raison — on ne conditionne pas le retrait de
    # ce qui a déjà disparu de la séance.
    selection = {
        "session": "a", "date": "2026-08-14", "half_dose": True,
        "gate": {"shin_max": 2, "drop": ["plyometrics"],
                 "say": "si ça tire, on retire les bonds"},
        "note": "demi-dose, injury surveillé",
    }
    process = carte(selection, sortie)
    reussit(process, "carte modulée")
    assert sortie.exists()


def test_une_selection_fautive_est_refusee_en_nommant_le_champ(tmp_path):
    """Le refus doit nommer ce qui cloche : c'est la seule chose qui permet au coach
    de se corriger dans le même tour au lieu de renoncer à la séance."""
    process = carte({"session": "z"}, tmp_path / "ko.html")
    assert process.returncode != 0
    refus = process.stderr + process.stdout
    assert "séance" in refus
    # Les valeurs acceptées sont énumérées : le coach corrige sans aller lire le code.
    assert "a, assimilation, b" in refus
