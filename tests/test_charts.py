"""Tests for the chart scripts: sample JSON -> a produced PNG.

Skipped if Playwright is not installed (only on the VPS and in the dev venv): the
rest of the suite must not depend on it.

The point of the stress cases below is NOT that they render prettily -- it is that
they render AT ALL without anything leaving its box. `cards/render.py` inspects
the laid-out page before writing a single pixel and refuses to produce a card that
overflows, so for every payload here `returncode == 0` IS the no-overflow
assertion. That is the regression net the matplotlib cards never had: they were
calibrated by eye on one sample each, and broke the first time a string grew.
"""

import json
import os
import struct
import subprocess
from pathlib import Path

import pytest
from conftest import ENGINE_ROOT, cli_command

pytest.importorskip("playwright")

SAMPLES_DIR = ENGINE_ROOT / "src" / "redline" / "cards" / "samples"

WORKOUT = {
    "title": "4 x 400 m", "date": "2026-07-18", "target_pace": "3:40",
    "reps": [
        {"label": "R1", "time_s": 88, "dist_m": 400, "hr": 168},
        {"label": "R2", "time_s": 89, "dist_m": 400, "hr": 172},
        {"label": "R3", "time_s": 88, "dist_m": 400},
        {"label": "R4", "time_s": 83, "dist_m": 400, "hr": 184},
    ],
}
WEEK = {
    "week": "W29",
    "days": [{"day": d, "planned_km": 8, "done_km": 7.5} for d in
             ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")],
    "planned_km": 56, "done_km": 52.5, "gauge_level": 60, "gauge_ratio": 1.1,
    "pain": "stable", "plaisir": 7,
}
MONTH = {
    "month": "Juillet 2026", "km": 148, "dplus": 1900, "sessions": 16,
    "weeks": [{"label": "W27", "km": 23}, {"label": "W28", "km": 20}],
    "capabilities": {"Durabilité": 2, "Descente": 1, "Nuit": 0,
                     "Digestion": 1, "Mental": 2, "Allure": 3},
    "race_name": "TargetRace", "race_date": "2026-11-28",
}
# A SELECTION of the programme, not a session: the exercises and their doses
# live in coaching/exercises/ and coaching/workouts/ (2026-07-25).
STRENGTH = {
    "session": "a", "date": "mardi 22 juillet", "duration_min": 30,
    "note": "Douleur à 0/10 : version complète.",
}

CARDS = [("chart-workout.py", WORKOUT), ("chart-week.py", WEEK),
         ("chart-month.py", MONTH), ("chart-strength.py", STRENGTH)]


def run_script(name: str, payload: dict, out: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    # The link variables are cleared unless a test asks for them: a developer with
    # COACH_BASE_URL exported must not silently change what these tests exercise.
    environment = dict(os.environ)
    environment.pop("COACH_BASE_URL", None)
    environment.update(env or {})
    return subprocess.run(
        [*cli_command(name.removesuffix(".py")), "-o", str(out)],
        input=json.dumps(payload), capture_output=True, text=True, env=environment,
    )


def png_size(path: Path) -> tuple[int, int]:
    """(width, height) straight from the IHDR chunk."""
    return struct.unpack(">II", path.read_bytes()[16:24])


# ------------------------------------------------------------------- nominal

@pytest.mark.parametrize("script,payload", CARDS)
def test_script_produces_a_png(tmp_path, script, payload):
    out = tmp_path / "chart.png"
    process = run_script(script, payload, out)
    assert process.returncode == 0, process.stderr
    assert out.is_file() and out.stat().st_size > 10_000
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("script,payload", CARDS)
def test_cards_are_rendered_at_2x(tmp_path, script, payload):
    """1080 CSS px at device_scale_factor 2. Messenger shrinks the image into the
    bubble, so the athlete needs the extra pixels when tapping to zoom."""
    out = tmp_path / "chart.png"
    assert run_script(script, payload, out).returncode == 0
    width, height = png_size(out)
    assert width == 2160
    assert height <= 2 * 2400, f"card too tall for a Messenger bubble: {height // 2} CSS px"


@pytest.mark.parametrize("name", [p.name for p in sorted(SAMPLES_DIR.glob("*.json"))])
def test_sample_payloads_still_render(tmp_path, name):
    """The samples are the design-loop fixtures; they must never rot."""
    script = f"chart-{Path(name).stem}.py"
    payload = json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))
    process = run_script(script, payload, tmp_path / "chart.png")
    assert process.returncode == 0, process.stderr


def test_the_fonts_are_committed():
    """A missing woff2 silently falls back to the host's fonts, and the whole
    "same render on the Mac and on the VPS" guarantee goes with it."""
    from redline.cards import render

    for _, filename, _ in render.FONT_FACES:
        assert (render.FONTS_DIR / filename).is_file(), f"police absente : {filename}"


# -------------------------------------------------------------------- stress
# Every case below used to be a broken card under matplotlib.

LONG_NAME = "Mollet unipodal genou fléchi ~30° sur marche, charge additionnelle"
LONG_TARGET = "soléaire — le muscle clé du mollet, et le trou n°1 d'avant"
LONG_NOTE = ("1ère séance depuis avril : demi-dose (une série en moins partout), "
             "SANS pliométrie cette semaine — 2320 m D+ samedi dernier, les jambes "
             "ont déjà bien encaissé, on ne rajoute pas d'impact par-dessus.")

STRESS = [
    pytest.param(
        "chart-strength.py",
        {"session": "a", "add": [{"name": LONG_NAME, "target": LONG_TARGET,
                                  "dose": {"measure": "amrap", "sets": [2, 3],
                                           "side": "each", "rest_s": 90}}],
         "note": LONG_NOTE},
        id="strength-long-strings"),
    pytest.param(
        "chart-strength.py",
        # QUINZE, pas « treize + six » : le nombre testé est le total, et la
        # séance A a gagné 4 exercices de contrôle moteur le 2026-08-21 (9 -> 13).
        # L'ajout suit donc la séance au lieu d'être un 6 figé, sinon ce cas dérive
        # vers des totaux que le programme ne peut pas produire.
        {"session": "a", "add": [{"name": f"Exercice {i}",
                                  "dose": {"measure": "reps", "sets": 3, "reps": 10}}
                                 for i in range(15 - 13)]},
        id="strength-fifteen-exercises"),
    pytest.param(
        "chart-strength.py", {"session": "assimilation"},
        id="strength-every-optional-missing"),
    pytest.param(
        "chart-week.py",
        dict(WEEK, pain="très irritable depuis mardi, réveil douloureux",
             pain_flag="bad", cmj_cm=31.5,
             dates="du lundi 13 au dimanche 19 juillet 2026"),
        id="week-long-tile-text"),
    pytest.param(
        "chart-week.py",
        {"days": [{"day": "lun", "done_km": 0}], "week": "W1"},
        id="week-single-empty-day"),
    pytest.param(
        "chart-month.py",
        dict(MONTH, capabilities={"Durabilité sur sentier technique en descente": 4,
                                  "Gestion digestive à l'effort prolongé": 1},
             km=1234.5, dplus=45678),
        id="month-long-capability-names"),
    pytest.param(
        "chart-month.py", {"month": "Un mois au titre particulièrement long 2026"},
        id="month-title-only"),
    pytest.param(
        "chart-workout.py",
        dict(WORKOUT, title="12 x 400 m en côte à 8 % avec récupération descendante",
             note="cible SV2, récupération 1'30 en trottinant",
             reps=[{"label": f"Rep {i + 1}", "time_s": 90 - i, "dist_m": 400,
                    "hr": 165 + i} for i in range(12)]),
        id="workout-twelve-reps-long-labels"),
    pytest.param(
        "chart-workout.py",
        {"reps": [{"time_s": 88, "dist_m": 400}, {"time_s": 88, "dist_m": 400}]},
        id="workout-no-target-no-hr"),
]


@pytest.mark.parametrize("script,payload", STRESS)
def test_stress_payloads_never_overflow(tmp_path, script, payload):
    out = tmp_path / "chart.png"
    process = run_script(script, payload, out)
    assert process.returncode == 0, process.stderr
    assert out.is_file()


def test_the_overflow_guard_actually_fires(tmp_path):
    """Without this, the guard could rot into dead code and nobody would notice."""
    payload = dict(STRENGTH, add=[
        {"name": f"Exercice {i}", "dose": {"measure": "reps", "sets": 3, "reps": 10}}
        for i in range(40)])
    process = run_script("chart-strength.py", payload, tmp_path / "chart.png")
    assert process.returncode != 0
    assert "Card non générée" in process.stderr
    assert "card trop haute" in process.stderr
    assert not (tmp_path / "chart.png").exists(), "aucun PNG ne doit être écrit"


# --------------------------------------------------------------- input errors

def test_invalid_json_fails_cleanly(tmp_path):
    process = subprocess.run(
        [*cli_command("chart-workout"),
         "-o", str(tmp_path / "x.png")],
        input="not json", capture_output=True, text=True,
    )
    assert process.returncode != 0
    assert "JSON illisible" in process.stderr
    assert not (tmp_path / "x.png").exists()


@pytest.mark.parametrize("script,payload,expected", [
    # A key nobody validated is a card the athlete reads as true. Every refusal
    # names the field -- these assertions are on the MESSAGE, not just the exit code.
    ("chart-week.py", dict(WEEK, gauge_lvl=72), "gauge_lvl"),
    ("chart-week.py", dict(WEEK, pain_flag="rouge"), "pain_flag"),
    ("chart-week.py", dict(WEEK, plaisir=42), "plaisir"),
    ("chart-week.py", {"dates": "sans semaine"}, "week"),
    ("chart-month.py", dict(MONTH, race_date="28/11/2026"), "race_date"),
    ("chart-workout.py", dict(WORKOUT, reps=WORKOUT["reps"][:1]), "reps"),
    ("chart-workout.py",
     dict(WORKOUT, reps=[{"time_s": 90, "dist_m": 0}, {"time_s": 90, "dist_m": 400}]),
     "dist_m"),
])
def test_a_malformed_payload_is_refused_by_name(tmp_path, script, payload, expected):
    out = tmp_path / "chart.png"
    process = run_script(script, payload, out)
    assert process.returncode != 0
    assert expected in process.stderr, process.stderr
    assert not out.exists(), "un payload refusé ne doit produire aucun PNG"


@pytest.mark.parametrize("kind", ["a", "b", "assimilation"])
def test_strength_card_renders_every_session_type(tmp_path, kind):
    out = tmp_path / f"strength-{kind}.png"
    process = run_script("chart-strength.py", {"session": kind}, out)
    assert process.returncode == 0, process.stderr
    assert out.is_file()


def test_strength_card_rejects_an_unknown_session(tmp_path):
    process = run_script("chart-strength.py", {"session": "c"}, tmp_path / "x.png")
    assert process.returncode != 0
    assert "connues : a, assimilation, b" in process.stderr


def test_strength_card_refusal_names_the_field(tmp_path):
    """A refusal Claude can act on without opening the source."""
    process = run_script("chart-strength.py", {"session": "a", "half_dose": "oui"},
                         tmp_path / "x.png")
    assert process.returncode != 0
    assert "champ « half_dose » : booléen attendu" in process.stderr
    assert not (tmp_path / "x.png").exists()


def test_a_derogation_must_carry_a_typed_dose(tmp_path):
    """Off-programme is allowed; a free-form dose string is not."""
    process = run_script("chart-strength.py",
                         {"session": "a", "add": [{"name": "Gainage", "dose": "3 x 30 s"}]},
                         tmp_path / "x.png")
    assert process.returncode != 0
    assert "objet attendu" in process.stderr


# ------------------------------------------------------------- the design loop

@pytest.mark.parametrize("script,payload", CARDS)
def test_html_flag_writes_a_standalone_document(tmp_path, script, payload):
    """`--html` is how the card is designed: one self-contained file, openable in
    a browser, fonts included."""
    out = tmp_path / "chart.png"
    process = subprocess.run(
        [*cli_command(script.removesuffix(".py")), "-o", str(out), "--html"],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    assert process.returncode == 0, process.stderr
    document = (tmp_path / "chart.html").read_text(encoding="utf-8")
    assert document.startswith("<!doctype html>")
    assert "data:font/woff2;base64," in document
    assert 'id="card"' in document


# ------------------------------------------------- the session link (sidecar)

LINK_ENV = {"COACH_BASE_URL": "https://coach.test"}


def test_strength_card_writes_its_exact_session_snapshot(tmp_path):
    """One JSON, two outputs: the card and the snapshot of the same session. They
    cannot disagree because nothing regenerates one without the other."""
    from redline.service import strength

    out = tmp_path / "strength-2026-07-22.png"
    assert run_script("chart-strength.py", STRENGTH, out, env=LINK_ENV).returncode == 0

    text, session = strength.read_sidecar(out)
    assert text == "Renfo, séance unique, mardi 22 juillet"
    assert session["sid"].startswith("strength-2026-07-22-")
    assert len(session["sid"].rsplit("-", 1)[1]) == 10       # prescription digest
    assert session["title"] == "Renfo, séance unique"      # from the session note
    # The page gets the slots WALKED OUT, plus the already-rendered label.
    # L'exercice est visé par son NOM, pas par sa position : une position n'est pas
    # une identité.
    split = next(e for e in session["exercises"] if e["name"].startswith("Fente bulgare"))
    first = split["dose"]
    assert first["text"] == "2 × 8 / jambe"
    assert first["measure"] == "reps"
    assert first["slots"] == [{"n": 1, "of": 2, "s": "d", "t": 8},
                              {"n": 1, "of": 2, "s": "g", "t": 8},
                              {"n": 2, "of": 2, "s": "d", "t": 8},
                              {"n": 2, "of": 2, "s": "g", "t": 8}]
    assert "expires_at" not in session  # expiration owned by the service at issuance


def test_the_worst_payload_the_schema_allows_still_gets_a_sidecar(tmp_path):
    """The full snapshot is server-bound; its size can no longer delete the page."""
    from redline.service import strength

    worst = {
        "session": "a",                       # la séance la plus lourde
        "date": "mardi 25 novembre 2026",
        "duration_min": 38,
        "note": "N" * 300,                    # SELECTION_SCHEMA: maximum=300
        "gate": {"zone": "injury", "pain_max": 2,
                 "drop": ["plyometrics", "calf-raise-straight"],
                 "say": "S" * 200},           # GATE_SCHEMA: maximum=200
    }
    out = tmp_path / "worst.png"
    process = run_script("chart-strength.py", worst, out, env=LINK_ENV)
    assert process.returncode == 0, process.stderr

    _, session = strength.read_sidecar(out)
    assert session["note"] == "N" * 300
    assert session["gate"]["say"] == "S" * 200


def test_no_link_without_configuration(tmp_path):
    """On the Mac the variables are usually unset: the card is the deliverable,
    the link is what rides along."""
    out = tmp_path / "strength.png"
    assert run_script("chart-strength.py", STRENGTH, out).returncode == 0
    assert out.exists()
    assert not list(tmp_path.glob("*.link"))


def test_the_strength_sidecar_needs_only_the_public_base_url(tmp_path):
    out = tmp_path / "strength.png"
    done = run_script("chart-strength.py", STRENGTH, out, env=LINK_ENV)
    assert done.returncode == 0, done.stderr
    assert out.exists() and strength_sidecar(out).exists()


def test_other_cards_never_get_a_link(tmp_path):
    out = tmp_path / "week.png"
    assert run_script("chart-week.py", WEEK, out, env=LINK_ENV).returncode == 0
    assert not list(tmp_path.glob("*.link"))


def strength_sidecar(image: Path) -> Path:
    return image.with_name(image.name + ".link")
