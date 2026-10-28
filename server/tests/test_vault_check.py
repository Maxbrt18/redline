"""`scripts/vault-check.py` : 632 lignes qui n'avaient aucun test.

C'était le seul module non couvert du dépôt, et il a GAGNÉ des responsabilités le
2026-07-29 : trois assertions sur le contenu du vault ont été retirées de pytest vers
ce script, parce qu'un contenu que le bot réécrit plusieurs fois par jour n'a rien à
faire dans un gate de code. Le contrôleur porte donc plus qu'avant tout en n'étant
lui-même contrôlé par rien.

On teste les checks un par un sur des `Vault` fabriqués, pas le vrai vault : c'est ce
qui permet d'exercer les cas ROUGES. Un contrôleur dont on n'a jamais vu l'échec est
un contrôleur dont on ne sait pas s'il sait échouer.
"""

import pytest
from conftest import ENGINE_ROOT, TEST_VAULT, load_script

from scripts.model import instance

vault_check = load_script("vault-check")


@pytest.fixture
def report():
    return vault_check.Report(quiet=True)


def vault(files, root=None, loaded=None):
    return vault_check.Vault(root=root or ENGINE_ROOT, files=files, instance=loaded)


# ------------------------------------------------- le rapport lui-même

def test_a_long_failure_says_how_much_it_hid(report, capsys):
    """`model/schema.py` existe contre le cycle « corrige, relance, il en reste » :
    tronquer à 8 sans le dire recrée exactement ce cycle (audit 2026-07-29)."""
    report.fail("un contrôle", [f"problème {i}" for i in range(20)])
    out = capsys.readouterr().out
    assert "problème 7" in out
    assert "problème 8" not in out
    assert "… et 12 autre(s)" in out


def test_a_short_failure_adds_no_footer(report, capsys):
    report.fail("un contrôle", ["seul problème"])
    assert "autre(s)" not in capsys.readouterr().out


def test_a_skip_is_printed_even_when_quiet(report, capsys):
    """Un contrôle qui disparaît faute de cible se lit comme un contrôle qui passe :
    il doit parler même en `--quiet`, où seuls les échecs sortent."""
    report.skip("un contrôle", "cible absente")
    assert "un contrôle" in capsys.readouterr().out


# --------------------------------------------- doublons de faits Strava

def test_a_strava_fact_copied_into_git_fails(report):
    check = vault_check.check_no_duplicate_facts
    check(vault({"athlete/journal/2026-w30.md": "cf. 90000000007"}), report)
    assert report.failed == 1


def test_a_decision_may_quote_the_evidence_it_rests_on(report):
    """`decisions.md` est en append-only : une citation figée dans le temps n'est pas
    un second propriétaire."""
    check = vault_check.check_no_duplicate_facts
    check(vault({"coaching/decisions.md": "justifiée par 90000000007"}), report)
    assert report.failed == 0


def test_finding_no_id_in_git_is_the_expected_state(report):
    vault_check.check_no_duplicate_facts(vault({"athlete/athlete.md": "aucun id ici"}), report)
    assert report.failed == 0


def test_the_id_pattern_follows_the_typed_ledger(report):
    """Le motif est cité au registre typé, pas recopié : un id plus long doit être vu."""
    check = vault_check.check_no_duplicate_facts
    check(vault({"concepts/seuil.md": "209876543210"}), report)
    assert report.failed == 1


# ------------------------------------------------------------ le graphe

def test_a_link_to_nowhere_fails(report):
    vault_check.check_links(vault({"concepts/seuil.md": "voir [[note-qui-nexiste-pas]]"}),
                            report)
    assert report.failed == 1


def test_a_link_inside_backticks_is_not_a_link(report):
    """Le bilan hebdo écrit `[[note-retirée]]` précisément pour ACTER un retrait :
    le signaler serait le contrôleur qui aboie sur sa propre trace d'audit."""
    vault_check.check_links(vault({"athlete/journal/2026-w30.md":
                                   "lien supprimé : `[[note-retirée]]`"}), report)
    assert report.failed == 0


def test_an_alias_resolves_a_link(report):
    """Un nœud, plusieurs surfaces : c'est ce qui permet de linker en français."""
    files = {"concepts/threshold.md": "---\naliases: [seuil, travail au seuil]\n---\n",
             "coaching/current-plan.md": "séance de [[seuil]]"}
    vault_check.check_links(vault(files), report)
    assert report.failed == 0


# ------------------------------------------------- vie privée (régression)

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


# ------------------------------------------------------ ordre des décisions

def test_decisions_out_of_order_fail(report):
    files = {"coaching/decisions.md": "## 2026-07-10 — vieille\n## 2026-07-25 — récente\n"}
    vault_check.check_decisions_order(vault(files), report)
    assert report.failed == 1


def test_decisions_newest_first_pass(report):
    files = {"coaching/decisions.md": "## 2026-07-25 — récente\n## 2026-07-10 — vieille\n"}
    vault_check.check_decisions_order(vault(files), report)
    assert report.failed == 0


def correction(date, correctif):
    return {"coaching/corrections.md":
            f"| {date} | affirmé | vrai | à lire | {correctif} |\n"}


# ------------------------------------------------------------- bout en bout

def test_the_checker_runs_on_the_real_vault_and_reports(capsys):
    """Le contrôleur tourne et rend un verdict — c'est une garantie de CODE (il
    s'exécute sans planter sur des données réelles), pas une assertion sur le contenu
    du vault, qui bouge plusieurs fois par jour."""
    files = vault_check.markdown_files(ENGINE_ROOT)
    report = vault_check.Report(quiet=True)
    for check in vault_check.CHECKS:
        # La parité des projections dépend d'un PostgreSQL peuplé et possède son test
        # d'intégration dans test_postgres_storage.py. Ici on teste les contrôles du vault.
        if check is vault_check.check_generated_views:
            continue
        check(vault(files), report)
    assert isinstance(report.failed, int)


def test_every_check_is_in_the_registry():
    """L'ordre du registre EST l'ordre de lecture du rapport, et il n'y a pas de
    seconde liste : un contrôle écrit mais jamais enregistré ne tourne jamais."""
    defined = {name for name in dir(vault_check) if name.startswith("check_")}
    registered = {check.__name__ for check in vault_check.CHECKS}
    assert defined == registered, f"jamais lancé : {sorted(defined - registered)}"


# ------------------------------------------------- dette de prédictions

def _predictions(monkeypatch, day, rows):
    class FrozenDate(vault_check.dt.date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(day)
    monkeypatch.setattr(vault_check.dt, "date", FrozenDate)
    monkeypatch.setattr(vault_check, "_read_postgres", lambda report, label, read: rows)


_DUE = {"prediction_key": "sl-20", "status": "open", "due_on": vault_check.dt.date(2026, 8, 30)}


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


