"""Tests for `redline vault-check`, the vault consistency checker.

The checker carries more than plain structure checks: assertions about vault content live
here instead of in pytest, because content the bot rewrites several times a day has no place
in a code gate. The checker therefore bears more weight while being checked by nothing else.

The checks are tested one by one on fabricated `Vault` objects, not the real vault: this
is what allows the RED cases to be exercised. A checker whose failure has never been seen
is a checker that may not know how to fail.
"""

import pytest
from conftest import ENGINE_ROOT, TEST_VAULT, load_script

from redline.model import instance

vault_check = load_script("vault-check")


@pytest.fixture
def report():
    return vault_check.Report(quiet=True)


def vault(files, root=None, loaded=None):
    return vault_check.Vault(root=root or ENGINE_ROOT, files=files, instance=loaded)


# ------------------------------------------------- the report itself

def test_a_long_failure_says_how_much_it_hid(report, capsys):
    """`model/schema.py` exists against the "fix, rerun, some are left" cycle:
    truncating at 8 without saying so recreates exactly that cycle."""
    report.fail("un contrôle", [f"problème {i}" for i in range(20)])
    out = capsys.readouterr().out
    assert "problème 7" in out
    assert "problème 8" not in out
    assert "… et 12 autre(s)" in out


def test_a_short_failure_adds_no_footer(report, capsys):
    report.fail("un contrôle", ["seul problème"])
    assert "autre(s)" not in capsys.readouterr().out


def test_a_skip_is_printed_even_when_quiet(report, capsys):
    """A check that vanishes for lack of a target reads like a check that passes:
    it must speak even with `--quiet`, where only failures are printed."""
    report.skip("un contrôle", "cible absente")
    assert "un contrôle" in capsys.readouterr().out


# --------------------------------------------- duplicated Strava facts

def test_a_strava_fact_copied_into_git_fails(report):
    check = vault_check.check_no_duplicate_facts
    check(vault({"athlete/journal/2026-w30.md": "cf. 90000000007"}), report)
    assert report.failed == 1


def test_a_decision_may_quote_the_evidence_it_rests_on(report):
    """`decisions.md` is append-only: a quotation frozen in time is not
    a second owner."""
    check = vault_check.check_no_duplicate_facts
    check(vault({"coaching/decisions.md": "justifiée par 90000000007"}), report)
    assert report.failed == 0


def test_finding_no_id_in_git_is_the_expected_state(report):
    vault_check.check_no_duplicate_facts(vault({"athlete/athlete.md": "aucun id ici"}), report)
    assert report.failed == 0


def test_the_id_pattern_follows_the_typed_ledger(report):
    """The pattern is cited from the typed ledger, not copied: a longer id must be seen."""
    check = vault_check.check_no_duplicate_facts
    check(vault({"concepts/seuil.md": "209876543210"}), report)
    assert report.failed == 1


# ------------------------------------------------------------ the graph

def test_a_link_to_nowhere_fails(report):
    vault_check.check_links(vault({"concepts/seuil.md": "voir [[note-qui-nexiste-pas]]"}),
                            report)
    assert report.failed == 1


def test_a_link_inside_backticks_is_not_a_link(report):
    """The weekly review writes `[[note-retirée]]` precisely to RECORD a removal:
    flagging it would be the checker barking at its own audit trail."""
    vault_check.check_links(vault({"athlete/journal/2026-w30.md":
                                   "lien supprimé : `[[note-retirée]]`"}), report)
    assert report.failed == 0


def test_an_alias_resolves_a_link(report):
    """One node, several surfaces: this is what allows linking in French."""
    files = {"concepts/threshold.md": "---\naliases: [seuil, travail au seuil]\n---\n",
             "coaching/current-plan.md": "séance de [[seuil]]"}
    vault_check.check_links(vault(files), report)
    assert report.failed == 0


# ------------------------------------------------- privacy (regression)

def test_a_note_type_the_instance_does_not_declare_fails(report):
    vault_check.check_frontmatter(
        vault({"episodes/source.md": "---\ntype: episode\n---\n"},
              loaded=instance.load(TEST_VAULT)), report)
    assert report.failed == 1


def test_without_declared_types_only_the_yaml_is_checked(report):
    vault_check.check_frontmatter(vault({"episodes/source.md": "---\ntype: episode\n---\n"}),
                                  report)
    assert report.failed == 0


def test_an_alias_with_a_comma_resolves_a_link(report):
    files = {"concepts/a.md": '---\naliases: ["b, c"]\n---\n', "x.md": "[[b, c]]"}
    vault_check.check_links(vault(files), report)
    assert report.failed == 0


def test_the_budget_is_skipped_without_declared_tiers(report):
    vault_check.check_startup_budget(vault({}), report)
    assert (report.failed, report.skipped) == (0, 1)


def test_the_declared_budget_names_the_file_over_its_ceiling(report):
    vault_check.check_startup_budget(
        vault({"CLAUDE.md": "mot " * 3000}, loaded=instance.load(TEST_VAULT)), report)
    assert report.failed == 1


# ------------------------------------------------------ decisions order

def test_decisions_out_of_order_fail(report):
    files = {"coaching/decisions.md": "## 2026-07-10 — vieille\n## 2026-07-25 — récente\n"}
    vault_check.check_decisions_order(vault(files), report)
    assert report.failed == 1


def test_decisions_newest_first_pass(report):
    files = {"coaching/decisions.md": "## 2026-07-25 — récente\n## 2026-07-10 — vieille\n"}
    vault_check.check_decisions_order(vault(files), report)
    assert report.failed == 0


def correction(date, fix):
    return {"coaching/corrections.md":
            f"| {date} | affirmé | vrai | à lire | {fix} |\n"}


# ------------------------------------------------------------- end to end

def test_the_checker_runs_on_the_real_vault_and_reports(capsys):
    """The checker runs and returns a verdict -- this is a guarantee about CODE (it
    runs without crashing on real data), not an assertion about vault content, which
    changes several times a day."""
    files = vault_check.markdown_files(ENGINE_ROOT)
    report = vault_check.Report(quiet=True)
    for check in vault_check.CHECKS:
        # Projection parity depends on a populated PostgreSQL and has its own
        # integration test in test_postgres_storage.py. Here the vault checks are tested.
        if check is vault_check.check_generated_views:
            continue
        check(vault(files), report)
    assert isinstance(report.failed, int)


def test_every_check_is_in_the_registry():
    """The registry order IS the report reading order, and there is no
    second list: a check that is written but never registered never runs."""
    defined = {name for name in dir(vault_check) if name.startswith("check_")}
    registered = {check.__name__ for check in vault_check.CHECKS}
    assert defined == registered, f"jamais lancé : {sorted(defined - registered)}"


# ------------------------------------------------- prediction debt

def _predictions(monkeypatch, day, rows):
    class FrozenDate(vault_check.dt.date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(day)
    monkeypatch.setattr(vault_check.dt, "date", FrozenDate)
    monkeypatch.setattr(vault_check, "_read_postgres", lambda report, label, read: rows)


#: As `prediction_rows()` returns it: plain values, the date as ISO text.
_DUE = {"prediction_key": "sl-20", "status": "open", "due_on": "2026-08-30"}


def test_a_prediction_due_within_the_grace_period_is_only_a_warning(report, monkeypatch):
    _predictions(monkeypatch, "2026-09-05", [_DUE])
    vault_check.check_prediction_debt(vault({}), report)
    assert report.failed == 0 and report.warned == 1


def test_a_prediction_left_without_verdict_past_the_grace_fails(report, monkeypatch):
    _predictions(monkeypatch, "2026-09-27", [_DUE])
    vault_check.check_prediction_debt(vault({}), report)
    assert report.failed == 1


def test_a_settled_prediction_is_no_debt(report, monkeypatch):
    _predictions(monkeypatch, "2026-09-27", [{**_DUE, "status": "resolved"}])
    vault_check.check_prediction_debt(vault({}), report)
    assert report.failed == 0 and report.warned == 0


