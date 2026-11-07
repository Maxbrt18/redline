"""The strength lineages: declared in the rulebook, read from the ledger, proposed only.

The fictional instance of `tests/fixtures/vault/` declares four lineages. The reports are
built as `OperationalStore.strength_history()` returns them.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess

import pytest
from conftest import ENGINE_ROOT, TEST_VAULT, cli_command, load_script, reset_database

from redline import cli
from redline.analysis import ledger, progression_strength, weekly_dossier
from redline.coach.fr import progression as text
from redline.model import program, rulebook
from redline.model.dose import MEASURES
from redline.model.progression import (
    STRENGTH_FAMILIES,
    Bound,
    Dose,
    Lineages,
    Move,
    Signal,
    Verdict,
    VetoKind,
    default,
)

TODAY = dt.date(2030, 3, 15)
ZONE = "knee"
RULEBOOK = (TEST_VAULT / "rulebook.toml").read_text(encoding="utf-8")


def book_with(progression: str) -> rulebook.Rulebook:
    """The fixture rulebook with `progression` in place of its `[progression]` table."""
    head, rest = RULEBOOK.split("[progression]", 1)
    tail = rest[rest.index("# ------------------------------------------------------------------ transitions"):]
    return rulebook.parse(head + progression + "\n" + tail, TEST_VAULT / "rulebook.toml")


PROGRESSION = """[progression]
rises_per_group_week = 1
difficulty_progress_up_to = 5
difficulty_regress_above = 7
"""


def report(on: dt.date, slug: str, counts, difficulty=6, pain=0.0, status=None):
    return {"session_key": f"strength-{on.isoformat()}", "performed_on": on,
            "pains": {ZONE: {"before": 0.0, "after": pain}},
            "exercises": [{"exercise_slug": slug, "difficulty": difficulty, "skipped": False,
                           "status": status,
                           "sets": [{"side": "both", "set_index": index + 1,
                                     "repetitions": count}
                                    for index, count in enumerate(counts)]}]}


class Store:
    def __init__(self, history):
        self.history = history

    def strength_history(self):
        return self.history


@pytest.fixture
def no_ledger(monkeypatch):
    """No run, one watched zone, no typed race: only the strength reports count."""
    monkeypatch.setattr(ledger, "parse_ledger", lambda store: [])
    monkeypatch.setattr(ledger, "pain_zones", lambda store: [ledger.PainZone(ZONE, "Genou")])
    monkeypatch.setattr(progression_strength.race_model, "upcoming", lambda store, today: None)


def by_lineage(found):
    return {proposal.lineage: proposal for proposal in found}


# ------------------------------------------------------------------ declaration

def test_the_fixture_declares_its_lineages_in_the_rulebook():
    declared = rulebook.load(TEST_VAULT).progression
    calf = declared.of("calf-raise-bent")
    assert calf is not None and calf.family == "strength-reps" and calf.group == "calves"
    assert calf.bound("reps") == Bound(12, 15, 1) and calf.bound("load_kg") == Bound(6, 16, 2)
    # The pain cap is the effort-pain scale's: the rulebook holds one value.
    assert calf.parameter("pain_cap") == rulebook.load(TEST_VAULT).scales.effort_pain.cap
    assert calf.parameter("difficulty_regress_above") == 7
    assert declared.rises_per_group_week == 1


def test_a_rulebook_without_progression_follows_no_lineage():
    assert book_with("").progression == Lineages()


def test_a_strength_family_follows_a_measure_of_the_dose():
    families = default()
    assert set(STRENGTH_FAMILIES) <= set(MEASURES)
    assert all(slug in families for slug in STRENGTH_FAMILIES.values())


@pytest.mark.parametrize(("entry", "problem"), [
    ('slug = "a"\nfamily = "strength-time"\nreps = { minimum = 1, maximum = 3, step = 1 }',
     "reps is not a variable of strength-time"),
    ('slug = "a"\nfamily = "strength-reps"\nload_kg = { minimum = 1, maximum = 3, step = 1 }',
     "strength-reps moves reps first"),
    ('slug = "a"\nfamily = "strength-time"\nseconds = { minimum = 30, maximum = 15, step = 5 }',
     "minimum 30 is above maximum 15"),
    ('slug = "a"\nfamily = "strength-time"\nseconds = { minimum = 15, maximum = 30, step = 0 }',
     "step 0 must be positive"),
    ('slug = "a"\nfamily = "strength-intervals"\nseconds = { minimum = 1, maximum = 3, step = 1 }',
     "'strength-intervals' unknown"),
])
def test_a_lineage_that_cannot_move_is_refused(entry, problem):
    with pytest.raises(rulebook.RulebookError, match=problem):
        book_with(PROGRESSION + "\n[[progression.lineages]]\n" + entry)


def test_a_lineage_declared_twice_is_refused():
    entry = ('\n[[progression.lineages]]\nslug = "a"\nfamily = "strength-time"\n'
             'seconds = { minimum = 15, maximum = 30, step = 5 }\n')
    with pytest.raises(rulebook.RulebookError, match="duplicate lineage: a"):
        book_with(PROGRESSION + entry + entry)


def test_the_difficulty_bands_must_be_ordered():
    with pytest.raises(rulebook.RulebookError, match="difficulty_progress_up_to 8 is above"):
        book_with(PROGRESSION.replace("progress_up_to = 5", "progress_up_to = 8")
                  + "lineages = []\n")


# ------------------------------------------------------------------ the sheets

def test_a_lineage_names_an_existing_sheet_of_its_measure():
    sheets = program.load(TEST_VAULT).exercises
    entries = "".join(
        f'\n[[progression.lineages]]\nslug = "{slug}"\nfamily = "strength-time"\n'
        'seconds = { minimum = 15, maximum = 30, step = 5 }\n'
        for slug in ("no-such-sheet", "step-up"))
    declared = book_with(PROGRESSION + entries).progression
    found = progression_strength.coverage(declared, sheets)
    assert found.missing == ("no-such-sheet",)
    assert found.mismatched == (("step-up", "strength-time", "reps"),)
    assert any("coaching/exercises/no-such-sheet.md" in line for line in found.refused)


def test_an_active_sheet_without_lineage_is_reported_and_a_retired_one_is_not():
    found = progression_strength.coverage(rulebook.load(TEST_VAULT).progression,
                                          program.load(TEST_VAULT).exercises)
    assert not found.refused
    assert "step-up" in found.unlisted and "calf-raise-bent" not in found.unlisted
    assert "soleus-lunge-iso" not in found.unlisted


def test_the_current_dose_is_the_sheets():
    sheets = program.load(TEST_VAULT).exercises
    # A range reads as its top, the target the page serves; the sheet's load is the load.
    assert progression_strength.current_dose(sheets["calf-raise-bent"], "calf-raise-bent") \
        == Dose.of(reps=15, load_kg=6)
    assert progression_strength.current_dose(sheets["calf-raise-straight"], "x") \
        == Dose.of(reps=22)
    assert progression_strength.current_dose(sheets["single-leg-stance"], "x") \
        == Dose.of(seconds=15)
    assert progression_strength.current_dose(sheets["plyometrics"], "x") == Dose.of(contacts=40)
    assert progression_strength.current_dose(sheets["hip-abduction"], "x") is None


# ------------------------------------------------------------------ the proposals

def test_two_sessions_at_target_propose_the_next_rung(no_ledger):
    history = [report(TODAY - dt.timedelta(days=7), "calf-raise-bent", (15, 15)),
               report(TODAY - dt.timedelta(days=3), "calf-raise-bent", (15, 16))]
    calf = by_lineage(progression_strength.proposals(Store(history), TEST_VAULT, TODAY))[
        "calf-raise-bent"]
    assert calf.move == Move.UP and calf.clause == "twice-at-target"
    # Double progression: the load rises one step, the reps go back to the bottom.
    assert calf.dose == Dose.of(reps=12, load_kg=8) and calf.ceiling == calf.dose
    assert {item.criterion: item.verdict for item in calf.judgements} == {
        "target-reached": Verdict.PASS, "not-too-hard": Verdict.PASS,
        "pain-under-cap": Verdict.PASS}


def test_a_session_without_counts_is_set_aside_and_asked_for(no_ledger):
    history = [report(TODAY - dt.timedelta(days=3), "calf-raise-bent", ())]
    calf = by_lineage(progression_strength.proposals(Store(history), TEST_VAULT, TODAY))[
        "calf-raise-bent"]
    assert calf.move == Move.HOLD and calf.clause == "no-evidence"
    assert Signal.SET_REPS_MIN in calf.missing
    assert calf.unread == (TODAY - dt.timedelta(days=3),)
    # Nothing read: the dose stands where the sheet puts it.
    assert calf.dose == Dose.of(reps=15, load_kg=6)


def test_a_group_rises_one_lineage_a_week(no_ledger):
    history = [report(TODAY - dt.timedelta(days=days), slug, counts)
               for days in (7, 3)
               for slug, counts in (("calf-raise-bent", (15, 15)),
                                    ("calf-raise-straight", (22, 22)))]
    found = by_lineage(progression_strength.proposals(Store(history), TEST_VAULT, TODAY))
    assert found["calf-raise-bent"].move == Move.UP
    held = found["calf-raise-straight"]
    assert held.rule_move == Move.UP and held.move == Move.HOLD
    assert [veto.kind for veto in held.vetoes] == [VetoKind.BUDGET]
    assert held.dose == Dose.of(reps=22)


def test_a_rise_never_passes_the_taper(no_ledger, monkeypatch):
    class Race:
        on = TODAY + dt.timedelta(days=10)
        taper_weeks = 3

    monkeypatch.setattr(progression_strength.race_model, "upcoming", lambda store, today: Race)
    history = [report(TODAY - dt.timedelta(days=days), "calf-raise-bent", (15, 15))
               for days in (7, 3)]
    calf = by_lineage(progression_strength.proposals(Store(history), TEST_VAULT, TODAY))[
        "calf-raise-bent"]
    assert calf.rule_move == Move.UP and calf.move == Move.HOLD
    assert [veto.kind for veto in calf.vetoes] == [VetoKind.TAPER]


def test_a_sheet_the_rulebook_names_but_the_vault_lacks_refuses_the_command(tmp_path):
    shutil.copytree(TEST_VAULT, tmp_path / "vault")
    (tmp_path / "vault" / "coaching" / "exercises" / "plyometrics.md").unlink()
    with pytest.raises(progression_strength.ConfigError, match="no sheet"):
        progression_strength.proposals(Store([]), tmp_path / "vault", TODAY)


# ------------------------------------------------------------------ what the coach reads

def test_the_coach_reads_the_move_the_clause_the_verdicts_and_the_veto(no_ledger):
    history = [report(TODAY - dt.timedelta(days=days), slug, counts)
               for days in (7, 3)
               for slug, counts in (("calf-raise-bent", (15, 15)),
                                    ("calf-raise-straight", (22, 22)))]
    lines = progression_strength.report(Store(history), TEST_VAULT, TODAY)
    out = "\n".join(lines)
    assert lines[0] == text.HEADER
    assert "- calf-raise-bent (strength-reps, groupe calves) : monter · clause twice-at-target" \
        in out
    assert "dose : reps=15, load_kg=6 → reps=12, load_kg=8" in out
    assert "target-reached tenu (15 pour 15)" in out
    assert "calf-raise-straight (strength-reps, groupe calves) : tenir" in out
    assert "(la règle disait monter)" in out
    assert "budget de hausses (rises_per_group_week : group calves, 1 a week)" in out
    # The contacts lineage waits for the next morning, which the ledger does not hold.
    assert "plyometrics (strength-contacts) : tenir" in out


def test_without_lineage_the_coach_is_told_so(tmp_path):
    assert progression_strength.render([], Lineages()) == [text.NONE_DECLARED]


def test_every_label_the_render_may_need_exists():
    assert set(text.SIGNALS) == {signal.value for signal in Signal}
    assert set(text.MOVES) == {move.value for move in Move}
    assert set(text.VERDICT_WORDS) == {verdict.value for verdict in Verdict}
    assert set(text.VETO_KINDS) == {kind.value for kind in VetoKind}


def test_the_review_dossier_reports_a_refusal_instead_of_failing(tmp_path, monkeypatch):
    shutil.copytree(TEST_VAULT, tmp_path / "vault")
    (tmp_path / "vault" / "coaching" / "exercises" / "plyometrics.md").unlink()
    [line] = weekly_dossier._progression(Store([]), tmp_path / "vault", "2030-W11", TODAY)
    assert line.startswith(text.UNAVAILABLE.split("{")[0]) and "plyometrics" in line


# ------------------------------------------------------------------ vault-check

def _checked(root):
    vault_check = load_script("vault-check")
    report = vault_check.Report(quiet=True)
    vault_check.check_progression_lineages(vault_check.Vault(root=root, files={}), report)
    return report


def test_vault_check_reports_the_active_sheets_no_lineage_follows():
    report = _checked(TEST_VAULT)
    assert report.failed == 0 and report.warned == 1


def test_vault_check_refuses_a_lineage_without_its_sheet(tmp_path):
    shutil.copytree(TEST_VAULT, tmp_path / "vault")
    (tmp_path / "vault" / "coaching" / "exercises" / "plyometrics.md").unlink()
    assert _checked(tmp_path / "vault").failed == 1


def test_vault_check_skips_an_instance_without_lineage():
    report = _checked(ENGINE_ROOT)
    assert report.failed == 0 and report.skipped == 1


# ------------------------------------------------------------------ the command

def test_the_command_is_listed_for_the_coach():
    assert "progression" in cli.COMMANDS
    tools = (ENGINE_ROOT / "deploy" / "agent-cli" / "agent-cli-tools.txt").read_text()
    assert "progression" in tools.splitlines()


def test_the_command_refuses_without_a_database():
    environment = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    process = subprocess.run(cli_command("progression"), capture_output=True, text=True,
                             env=environment, cwd=ENGINE_ROOT)
    assert process.returncode == 1 and "DATABASE_URL is required" in process.stderr


URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL absent")
def test_the_command_reads_the_counts_the_page_sends():
    from redline.storage import Database, OperationalStore

    reset_database()
    store = OperationalStore(Database(URL, application_name="pytest"))
    for days in (7, 3):
        on = dt.date.today() - dt.timedelta(days=days)
        with store.database.transaction() as connection:
            store._add_strength_session_in(connection, {
                **report(on, "calf-raise-bent", (15, 15, 15)), "workout": "a",
                "pains": {ZONE: {"before": 0, "after": 1}}}, actor="test")
    process = subprocess.run(
        [*cli_command("progression"), "--root", str(TEST_VAULT)], capture_output=True,
        text=True, cwd=ENGINE_ROOT, env={**os.environ, "DATABASE_URL": URL})
    assert process.returncode == 0, process.stderr
    assert "calf-raise-bent (strength-reps, groupe calves) : monter" in process.stdout
    assert "reps=12, load_kg=8" in process.stdout
