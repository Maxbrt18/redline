"""VaultRepo : ce qu'un tour enregistre avant sa livraison, et rien n'échoue en silence.

La panne du 2026-08-10 tenait dans une cellule (`FCmoy: 156.6`) et dans deux trous
d'architecture que ces tests ferment :

  - **Deux politiques contraires sur le même événement.** `regenerate_views` dit qu'un
    générateur en échec ne doit jamais bloquer un commit, et `test_regen_views.py` le
    prouve depuis toujours — sur un dépôt de test SANS hook. En prod, `.githooks/
    pre-commit` tournait en `set -e` et avortait le commit. Le test était vert et la
    prod tombait : la couverture s'arrêtait exactement là où la contradiction vivait.
  - **L'échec de persistance était muet.** Le débrief Strava du jour a été rédigé,
    jeté, et personne n'a été prévenu. Sam l'a appris en trouvant son coach cassé.

Depuis le 2026-09-25, la frontière est coupée en deux : `persist_turn` (PostgreSQL et
commit local, sans réseau) précède la livraison ; `publish` (vues, commit, push) la suit.
"""

import subprocess
import sys
from unittest.mock import MagicMock

from conftest import ENGINE_ROOT, git

from redline.service.vault import VaultRepo


def _head(repo) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()


def _committed(repo) -> str:
    return subprocess.run(["git", "show", "--name-only", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout


class TestTheLedgerIsSealedBeforeStaging:
    def test_a_clone_without_a_ledger_commits_all_the_same(self, git_repo):
        (git_repo / "source.md").write_text("notes")
        assert VaultRepo(str(git_repo), "origin", "main").persist_turn().writes_applied
        assert "source.md" in _committed(git_repo)


class TestNothingFailsSilently:
    def test_a_broken_generator_is_reported_to_alex_not_just_logged(self, git_repo):
        (git_repo / "source.md").write_text("notes de la session")
        repo = VaultRepo(str(git_repo), "origin", "main", regen_command=(
            sys.executable, "-c", "import sys; sys.exit('registre illisible')"))
        repo.persist_turn()
        repo.publish()
        assert "source.md" in _committed(git_repo)         # la session est sauvée
        assert any("régénération" in alert.text for alert in repo.drain_alerts())

    def test_a_commit_created_during_the_turn_is_taken_over_by_the_service(self, git_repo):
        base = _head(git_repo)
        (git_repo / "source.md").write_text("notes", encoding="utf-8")
        git(git_repo, "add", "source.md")
        git(git_repo, "commit", "-m", "commit intermédiaire")

        repo = VaultRepo(str(git_repo), "origin", "main")
        result = repo.persist_turn(base_head=base)

        assert result.writes_applied
        assert "source.md" in _committed(git_repo)
        assert "coach (messenger)" in subprocess.run(
            ["git", "log", "-1", "--format=%s"], cwd=git_repo,
            capture_output=True, text=True).stdout
        assert not repo.publish()  # ce dépôt de test n'a pas d'origin

    def test_a_code_commit_created_during_the_turn_is_restored_not_the_whole_turn(
            self, git_repo):
        """RWF-003 : l'effet dangereux est le fichier de code, pas les notes du tour."""
        base = _head(git_repo)
        path = git_repo / "server" / "redline" / "surprise.py"
        path.parent.mkdir(parents=True)
        path.write_text("BROKEN = True\n", encoding="utf-8")
        (git_repo / "notes.md").write_text("une note du coach", encoding="utf-8")
        git(git_repo, "add", "-A")
        git(git_repo, "commit", "-m", "code inattendu")

        repo = VaultRepo(str(git_repo), "origin", "main")
        result = repo.persist_turn(base_head=base)

        assert result.writes_applied
        assert result.restored == ("src/redline/service/surprise.py",)
        assert not path.exists()
        committed = _committed(git_repo)
        assert "notes.md" in committed and "surprise.py" not in committed
        assert any("restaurés" in alert.text for alert in repo.drain_alerts())

    def test_every_runtime_family_is_restored_without_a_second_list(self, git_repo):
        for relative in ("migrations/versions/surprise.py", "alembic.ini", "pyproject.toml"):
            base = _head(git_repo)
            path = git_repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("runtime inattendu\n", encoding="utf-8")

            result = VaultRepo(str(git_repo), "origin", "main").persist_turn(
                base_head=base)

            assert result.writes_applied, relative
            assert result.restored == (relative,)
            assert not path.exists(), relative

    def test_an_edited_projection_is_put_back_and_the_facts_still_apply(self, git_repo):
        (git_repo / "athlete").mkdir()
        (git_repo / "athlete" / "daily.csv").write_text("date;sommeil\n", encoding="utf-8")
        git(git_repo, "add", "-A")
        git(git_repo, "commit", "-m", "projection")
        base = _head(git_repo)
        (git_repo / "athlete" / "daily.csv").write_text("édité à la main\n", encoding="utf-8")
        store = MagicMock()
        store.apply_turn_with_receipts.return_value = (2, ())
        repo = VaultRepo(str(git_repo), "origin", "main", operational_store=store)

        result = repo.persist_turn(base_head=base, turn_id="tour")

        assert result.writes_applied and result.applied_commands == 2
        assert result.restored == ("athlete/daily.csv",)
        assert (git_repo / "athlete" / "daily.csv").read_text() == "date;sommeil\n"


class TestTheHookAgreesWithTheCode:
    """La contradiction qui a fait tomber la prod : le hook avortait le commit que
    `regenerate_views` a explicitement décidé de laisser passer. Aucun test ne
    l'exerçait, parce que les dépôts de test n'ont pas de `core.hooksPath`."""

    def test_the_real_hook_does_not_abort_a_commit_on_a_failing_generator(self, git_repo):
        scripts = git_repo / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "regen-views.py").write_text(
            "import sys; sys.exit('registre illisible')\n", encoding="utf-8")
        # Le générateur entre AVANT que le hook ne soit branché : le commit à tester ne
        # doit stager aucun `.py`, sans quoi c'est la porte ruff/mypy/pytest du hook
        # qu'on mesurerait, et pas la ligne de régénération.
        git(git_repo, "add", "-A")
        git(git_repo, "commit", "--no-verify", "-m", "générateur cassé")
        git(git_repo, "config", "core.hooksPath", str(ENGINE_ROOT / ".githooks"))
        (git_repo / "notes.md").write_text("le travail d'une session")
        git(git_repo, "add", "-A")
        done = subprocess.run(["git", "commit", "-m", "session"], cwd=git_repo,
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        assert "notes.md" in _committed(git_repo)

    def test_the_hook_stages_what_it_regenerates_and_nothing_else(self, git_repo):
        """`git add -u` embarquait toute édition non stagée : aucun commit ciblé."""
        scripts = git_repo / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "regen-views.py").write_text(
            "from pathlib import Path\nPath('vue.md').write_text('régénérée')\n",
            encoding="utf-8")
        for name in ("vue.md", "brouillon.md", "notes.md"):
            (git_repo / name).write_text("avant", encoding="utf-8")
        git(git_repo, "add", "-A")
        git(git_repo, "commit", "--no-verify", "-m", "générateur")
        git(git_repo, "config", "core.hooksPath", str(ENGINE_ROOT / ".githooks"))
        (git_repo / "brouillon.md").write_text("en cours, pas pour ce commit")
        (git_repo / "notes.md").write_text("le travail d'une session")
        git(git_repo, "add", "notes.md")
        done = subprocess.run(["git", "commit", "-m", "session"], cwd=git_repo,
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        committed = _committed(git_repo)
        assert "notes.md" in committed and "vue.md" in committed
        assert "brouillon.md" not in committed


def test_a_readback_failure_preserves_the_committed_database_receipt(git_repo):
    store = MagicMock()
    receipt = {"kind": "plan.revise", "payload": {"week": "2026-W37"}}
    store.apply_turn_with_receipts.return_value = (1, (receipt,))
    store.load_plan.side_effect = RuntimeError("lecture indisponible")
    repo = VaultRepo(str(git_repo), "origin", "main", operational_store=store)

    result = repo.persist_turn(turn_id="confirmed")

    assert result.writes_applied
    assert result.applied_commands == 1
    assert result.receipts == (receipt,)
    assert result.invalid == ("2026-W37",)
    store.apply_turn_with_receipts.assert_called_once_with("confirmed")


def test_actual_revised_week_is_checked_without_a_triage_flag(git_repo):
    from redline.service.plans import PlanStatus

    store = MagicMock()
    store.apply_turn_with_receipts.return_value = (
        1, ({"kind": "plan.revise", "payload": {"week": "2026-W38"}},))
    repo = VaultRepo(str(git_repo), "origin", "main", operational_store=store)
    repo.validate_weekly_plan = MagicMock(return_value=PlanStatus.AWAITING)

    result = repo.persist_turn(turn_id="changed")

    repo.validate_weekly_plan.assert_called_once_with("2026-W38")
    assert result.awaiting == ("2026-W38",)


def test_the_persistence_boundary_never_touches_the_network(git_repo, monkeypatch):
    """RWF-010 : la réponse part dès que PostgreSQL a validé ; vues et push après."""
    store = MagicMock()
    store.apply_turn_with_receipts.return_value = (1, ())
    store.projection_pending.return_value = True
    repo = VaultRepo(str(git_repo), "origin", "main", operational_store=store)
    calls = []
    real_git = repo._git

    def recording_git(*args, **kwargs):
        calls.append(args[0])
        return real_git(*args, **kwargs)

    monkeypatch.setattr(repo, "_git", recording_git)
    repo.regenerate_views = MagicMock()
    (git_repo / "note.md").write_text("v2")

    repo.persist_turn(turn_id="tour")

    assert not {"push", "fetch"} & set(calls)
    repo.regenerate_views.assert_not_called()
    repo.publish()
    repo.regenerate_views.assert_called_once()
    assert "push" in calls


def test_a_transient_database_failure_is_retried_before_refusing(git_repo, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from redline.storage import StorageError

    def transient():
        try:
            raise OperationalError("select", {}, Exception("server closed the connection"))
        except OperationalError as exc:
            raise StorageError("transaction PostgreSQL refusée") from exc

    outcomes = iter([transient, lambda: (1, ())])
    store = MagicMock()
    store.apply_turn_with_receipts.side_effect = lambda _turn_id: next(outcomes)()
    monkeypatch.setattr("redline.service.vault.time.sleep", lambda _seconds: None)
    repo = VaultRepo(str(git_repo), "origin", "main", operational_store=store)

    result = repo.persist_turn(turn_id="tour")

    assert result.writes_applied and result.applied_commands == 1
    assert store.apply_turn_with_receipts.call_count == 2


def test_a_definitive_refusal_names_the_commands_that_were_lost(git_repo):
    from redline.storage import StorageError

    store = MagicMock()
    store.apply_turn_with_receipts.side_effect = StorageError("mesure déjà connue")
    store.pending_turn_kinds.return_value = ("measurement.complete",)
    repo = VaultRepo(str(git_repo), "origin", "main", operational_store=store)

    result = repo.persist_turn(turn_id="tour")

    assert not result.writes_applied
    assert result.refused == ("measurement.complete",)
    store.apply_turn_with_receipts.assert_called_once()
