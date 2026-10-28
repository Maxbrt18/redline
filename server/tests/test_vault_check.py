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
from conftest import ENGINE_ROOT, load_script

vault_check = load_script("vault-check")


@pytest.fixture
def report():
    return vault_check.Report(quiet=True)


def vault(files, root=None):
    return vault_check.Vault(root=root or ENGINE_ROOT, files=files)


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

def test_a_known_leak_pattern_fails(report):
    """Liste noire de fuites DÉJÀ trouvées et nettoyées, pas une garantie : elle ne
    peut pas attraper une nouvelle, et son libellé le dit."""
    vault_check.check_privacy(vault({"athlete/athlete.md": "diagnostic de gastrite"}),
                              report)
    assert report.failed == 1


def test_the_blocklist_is_word_bounded(report):
    """`SECRET` ne doit pas matcher « secrètement » — un contrôle qui crie au loup est un
    contrôle qu'on finit par ignorer."""
    vault_check.check_privacy(vault({"athlete/ledger.md": "rando, il en parle secrètement"}),
                              report)
    assert report.failed == 0


def test_a_companion_name_in_a_projected_journal_fails(report):
    vault_check.check_operational_privacy(
        vault({"athlete/journal/2026-w34.md": "Rando faite avec Alexis."}), report)
    assert report.failed == 1


def test_a_numeric_wake_reconstructed_in_the_current_journal_fails(report, monkeypatch):
    class FrozenDate(vault_check.dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 24)

    monkeypatch.setattr(vault_check.dt, "date", FrozenDate)
    vault_check.check_active_safety_rules(
        vault({"athlete/journal/2026-w35.md": "reveil 0/10, donc feu vert"}), report)
    assert report.failed == 1


def test_frontmatter_rejects_a_noncanonical_episode_type(report):
    vault_check.check_frontmatter(
        vault({"episodes/source.md": "---\ntype: episode\naliases: [source]\n---\n"}), report)
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


# ------------------------------------------------------- fichiers retirés

def test_a_retired_file_coming_back_fails(report, tmp_path):
    """`fitness-status.md` a été supprimé parce qu'il était un second cache du même
    état, et il avait effectivement dérivé. Le voir revenir veut dire qu'une règle a
    été oubliée. Ce contrôle-là lit le DISQUE : un fichier ressuscité mais jamais
    indexé compterait quand même."""
    (tmp_path / "coaching").mkdir()
    (tmp_path / "coaching" / "fitness-status.md").write_text("état de forme")
    vault_check.check_retired(vault({}, root=tmp_path), report)
    assert report.failed == 1


def test_a_clean_tree_passes_the_retired_check(report, tmp_path):
    vault_check.check_retired(vault({}, root=tmp_path), report)
    assert report.failed == 0


# ------------------------------------------ règles de méta portées

def correction(date, correctif):
    return {"coaching/corrections.md":
            f"| {date} | affirmé | vrai | à lire | {correctif} |\n"}


def test_a_star_rule_naming_no_carrier_fails(report):
    """Une règle générale qui n'existe que dans `corrections.md` ne tournera jamais :
    le fichier n'est lu par aucun niveau de l'échelle et par aucun prompt."""
    vault_check.check_corrections_carriers(
        vault(correction("2026-08-09", "⭐ **Règle** : y penser le matin.")), report)
    assert report.failed == 1


def test_a_star_rule_naming_a_carrier_passes(report):
    vault_check.check_corrections_carriers(
        vault(correction("2026-08-09",
                         "⭐ **Règle** portée par `scripts/model/weekly_plan.py`.")), report)
    assert report.failed == 0


def test_a_retired_threads_file_is_not_a_carrier(report):
    vault_check.check_corrections_carriers(
        vault(correction("2026-08-09",
                         "⭐ **Règle** portée par l'ancien `ai/threads.md`.")), report)
    assert report.failed == 1


def test_a_retired_deadlines_file_is_not_a_carrier(report):
    vault_check.check_corrections_carriers(
        vault(correction("2026-08-09",
                         "⭐ **Règle** portée par `coaching/echeances.md`.")), report)
    assert report.failed == 1


def test_citing_a_note_is_not_citing_a_carrier(report):
    """Le piège exact : `decisions.md` et les fiches ne se lisent qu'à la demande en
    N2/N3. Y renvoyer une règle générale, c'est la rendormir."""
    vault_check.check_corrections_carriers(
        vault(correction("2026-08-09", "⭐ **Règle** : cf. [[decisions]] et la fiche.")), report)
    assert report.failed == 1


def test_a_content_fix_without_a_star_is_none_of_this_checks_business(report):
    """Corriger ce que dit une fiche n'appelle aucun porteur, et ces lignes-là ne
    portent pas de ⭐ : le contrôle ne doit pas inventer une dette."""
    vault_check.check_corrections_carriers(
        vault(correction("2026-08-09", "Fiche corrigée : le talon touche.")), report)
    assert report.failed == 0


def test_mentioning_the_marker_is_not_announcing_a_rule(report):
    """Le contrôle tirait sur sa propre documentation : la convention est
    `⭐ **libellé**`, pas le caractère nu."""
    vault_check.check_corrections_carriers(
        vault(correction("2026-08-09",
                         "Le ⭐ a été retiré : c'était une observation datée.")), report)
    assert (report.failed, report.warned) == (0, 0)


def test_older_entries_are_visible_debt_not_a_failure(report):
    vault_check.check_corrections_carriers(
        vault(correction("2026-07-31", "⭐ **Règle** : y penser le matin.")), report)
    assert (report.failed, report.warned) == (0, 1)


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

_DECISION = """## 2026-08-20 — Une décision datée

**Prédiction** : quelque chose de falsifiable. Revue : W35.
"""


def _frozen(monkeypatch, day):
    class FrozenDate(vault_check.dt.date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(day)
    monkeypatch.setattr(vault_check.dt, "date", FrozenDate)


def test_a_prediction_due_within_the_grace_period_is_only_a_warning(report, monkeypatch):
    _frozen(monkeypatch, "2026-09-05")                   # W35 close le 30/08 : +6 j
    vault_check.check_prediction_debt(vault({"coaching/decisions.md": _DECISION}), report)
    assert report.failed == 0 and report.warned == 1


def test_a_prediction_left_without_verdict_past_the_grace_fails(report, monkeypatch):
    """Réponse pré-engagée de la décision du 2026-08-16 : un contrôle rouge."""
    _frozen(monkeypatch, "2026-09-27")                   # +28 j
    vault_check.check_prediction_debt(vault({"coaching/decisions.md": _DECISION}), report)
    assert report.failed == 1


def test_a_dated_verdict_closes_the_debt(report, monkeypatch):
    _frozen(monkeypatch, "2026-09-27")
    closed = _DECISION + "\n**Verdict** (2026-09-27) : tenue — le fait qui tranche.\n"
    vault_check.check_prediction_debt(vault({"coaching/decisions.md": closed}), report)
    assert report.failed == 0 and report.warned == 0
