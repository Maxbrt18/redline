"""The strength card, from the typed selection to the page -- no database or browser.

The morning note orders this card on strength days, and the coach writes neither
exercises nor doses: it sends a SELECTION that the script resolves from
`coaching/exercises/` and `coaching/workouts/`. A slug renamed in the vault thus
breaks the card without any logic test noticing.

The PNG rendering and the signed link have their own CI job, with Chromium
(`test_charts.py`). Here we replay the RESOLUTION CHAIN, in `--html` mode, so that it
also runs in the fast job -- the one that blocks deployment.
"""

from __future__ import annotations

import json
import subprocess

from conftest import ENGINE_ROOT, cli_command


def reussit(process: subprocess.CompletedProcess, geste: str) -> subprocess.CompletedProcess:
    assert process.returncode == 0, f"{geste} a échoué : {process.stderr or process.stdout}"
    return process


def carte(selection: dict, sortie) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*cli_command("chart-strength"), "--html", "-o", str(sortie)],
        cwd=ENGINE_ROOT, input=json.dumps(selection),
        capture_output=True, text=True, check=False)


def test_la_carte_de_renfo_se_genere_depuis_une_selection_typee(tmp_path):
    """The morning note orders this card on strength days. The coach writes neither
        exercises nor doses: it sends a SELECTION, and the script resolves it from
        `coaching/exercises/` and `coaching/workouts/`. A slug renamed in the vault
        would thus break the card without any rendering test noticing.

        `--html` mode: the same resolution chain, without requiring Chromium in the fast job.
        The PNG rendering and the signed link have their own job (`test_charts.py`).

    """
    for session in ("a", "b"):
        sortie = tmp_path / f"renfo-{session}.html"
        process = carte({"session": session, "date": "2026-08-14"}, sortie)
        reussit(process, f"carte renfo {session}")
        assert sortie.exists() and sortie.stat().st_size > 1000


def test_la_carte_accepte_les_modulations_que_le_matin_prescrit(tmp_path):
    """Half dose, one-off removal and pain guard: the three levers the morning trigger
        names. If they stop being accepted, the coach falls back on a full-dose card on a
        day when the injury speaks."""
    sortie = tmp_path / "renfo-module.html"
    # The `gate` removes CONDITIONALLY what `drop` would remove right away:
    # combining both is refused, rightly -- one does not make the removal conditional on
    # something that has already disappeared from the session.
    selection = {
        "session": "a", "date": "2026-08-14", "half_dose": True,
        "gate": {"zone": "injury", "pain_max": 2, "drop": ["plyometrics"],
                 "say": "si ça tire, on retire les bonds"},
        "note": "demi-dose, blessure surveillée",
    }
    process = carte(selection, sortie)
    reussit(process, "carte modulée")
    assert sortie.exists()


def test_une_selection_fautive_est_refusee_en_nommant_le_champ(tmp_path):
    """The refusal must name what is wrong: it is the only thing that lets the coach
        correct itself in the same turn instead of giving up the session."""
    process = carte({"session": "z"}, tmp_path / "ko.html")
    assert process.returncode != 0
    refus = process.stderr + process.stdout
    assert "séance" in refus
    # The accepted values are enumerated: the coach corrects without reading the code.
    assert "a, assimilation, b" in refus
