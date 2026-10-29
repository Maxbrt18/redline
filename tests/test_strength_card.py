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


def succeeds(process: subprocess.CompletedProcess, cue: str) -> subprocess.CompletedProcess:
    assert process.returncode == 0, f"{cue} a échoué : {process.stderr or process.stdout}"
    return process


def carte(selection: dict, output) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*cli_command("chart-strength"), "--html", "-o", str(output)],
        cwd=ENGINE_ROOT, input=json.dumps(selection),
        capture_output=True, text=True, check=False)


def test_the_strength_card_is_built_from_a_typed_selection(tmp_path):
    """The morning note orders this card on strength days. The coach writes neither
        exercises nor doses: it sends a SELECTION, and the script resolves it from
        `coaching/exercises/` and `coaching/workouts/`. A slug renamed in the vault
        would thus break the card without any rendering test noticing.

        `--html` mode: the same resolution chain, without requiring Chromium in the fast job.
        The PNG rendering and the signed link have their own job (`test_charts.py`).

    """
    for session in ("a", "b"):
        output = tmp_path / f"renfo-{session}.html"
        process = carte({"session": session, "date": "2026-08-14"}, output)
        succeeds(process, f"carte renfo {session}")
        assert output.exists() and output.stat().st_size > 1000


def test_the_card_accepts_the_modulations_the_morning_prescribes(tmp_path):
    """Half dose, one-off removal and pain guard: the three levers the morning trigger
        names. If they stop being accepted, the coach falls back on a full-dose card on a
        day when the injury speaks."""
    output = tmp_path / "renfo-module.html"
    # The `gate` removes CONDITIONALLY what `drop` would remove right away:
    # combining both is refused, rightly -- one does not make the removal conditional on
    # something that has already disappeared from the session.
    selection = {
        "session": "a", "date": "2026-08-14", "half_dose": True,
        "gate": {"zone": "knee", "pain_max": 2, "drop": ["plyometrics"],
                 "say": "si ça tire, on retire les bonds"},
        "note": "demi-dose, blessure surveillée",
    }
    process = carte(selection, output)
    succeeds(process, "carte modulée")
    assert output.exists()


def test_a_faulty_selection_is_refused_and_names_the_field(tmp_path):
    """The refusal must name what is wrong: it is the only thing that lets the coach
        correct itself in the same turn instead of giving up the session."""
    process = carte({"session": "z"}, tmp_path / "ko.html")
    assert process.returncode != 0
    refusal = process.stderr + process.stdout
    assert "session" in refusal
    # The accepted values are enumerated: the coach corrects without reading the code.
    assert "a, assimilation, b" in refusal
