"""VaultRepo: what a turn records before its delivery, and nothing fails silently.

A failure came down to one table cell (`FCmoy: 156.6`) and two architecture gaps that
these tests close:
  - **Two opposite policies on the same event.** `regenerate_views` says a failing
    generator must never block a commit, and `test_regen_views.py` proves it -- on a
    test repository WITHOUT a hook. In production, the vault's `pre-commit` hook ran under
    `set -e` and aborted the commit. The test was green and production failed: coverage
    stopped exactly where the contradiction lived.
  - **A persistence failure was mute.** The day's Strava debrief was written, thrown
    away, and nobody was warned.
The boundary is split in two: `persist_turn` (PostgreSQL and local commit, no network)
precedes delivery; `publish` (views, commit, push) follows it.
"""

import os
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
        assert "source.md" in _committed(git_repo)         # the session is saved
        assert any("regeneration" in alert.text for alert in repo.drain_alerts())

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
        assert not repo.publish()  # this test repository has no origin

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

    def test_code_written_by_a_turn_is_removed_and_its_notes_still_commit(self, git_repo):
        """Python, a hook or a script runs later, outside the turn: a turn writes notes."""
        (git_repo / ".githooks").mkdir()
        (git_repo / ".githooks" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
        (git_repo / "scripts").mkdir()
        script = git_repo / "scripts" / "deploy"
        script.write_text("#!/bin/sh\necho deploy\n", encoding="utf-8")
        script.chmod(0o755)
        git(git_repo, "add", "-A")
        git(git_repo, "commit", "-m", "scripts")
        base = _head(git_repo)

        (git_repo / "redline").mkdir()
        (git_repo / "redline" / "__init__.py").write_text("import os\n", encoding="utf-8")
        (git_repo / ".githooks" / "pre-commit").write_text("#!/bin/sh\ncurl x\n",
                                                           encoding="utf-8")
        # Rewritten without its execute bit, the script is still recognised from HEAD.
        script.write_text("#!/bin/sh\ncurl x\n", encoding="utf-8")
        script.chmod(0o644)
        (git_repo / "notes.md").write_text("le débrief", encoding="utf-8")

        result = VaultRepo(str(git_repo), "origin", "main").persist_turn(base_head=base)

        assert result.writes_applied
        assert result.restored == (".githooks/pre-commit", "redline/__init__.py",
                                   "scripts/deploy")
        assert not (git_repo / "redline" / "__init__.py").exists()
        assert script.read_text(encoding="utf-8") == "#!/bin/sh\necho deploy\n"
        assert (git_repo / ".githooks" / "pre-commit").read_text() == "#!/bin/sh\n"
        committed = _committed(git_repo)
        assert "notes.md" in committed and "redline/" not in committed


class TestTheHookAgreesWithTheCode:
    """The contradiction that took production down: the hook aborted the commit that
        `regenerate_views` explicitly decided to let through. No test exercised it,
        because test repositories have no `core.hooksPath`."""

    def test_the_real_hook_does_not_abort_a_commit_on_a_failing_generator(
            self, git_repo, tmp_path):
        generator = tmp_path / "fake" / "redline" / "cli"
        generator.mkdir(parents=True)
        (generator.parent / "__init__.py").write_text("", encoding="utf-8")
        (generator / "__init__.py").write_text("", encoding="utf-8")
        (generator / "regen_views.py").write_text(
            "import sys; sys.exit('registre illisible')\n", encoding="utf-8")
        git(git_repo, "config", "core.hooksPath", str(ENGINE_ROOT / "templates" / "vault" / ".githooks"))
        (git_repo / "notes.md").write_text("le travail d'une session")
        git(git_repo, "add", "-A")
        done = subprocess.run(["git", "commit", "-m", "session"], cwd=git_repo,
                              capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": str(tmp_path / "fake")})
        assert done.returncode == 0, done.stderr
        assert "notes.md" in _committed(git_repo)

    def test_the_hook_stages_what_it_regenerates_and_nothing_else(self, git_repo, tmp_path):
        """`git add -u` swept up every unstaged edit: no targeted commit."""
        generator = tmp_path / "fake" / "redline" / "cli"
        generator.mkdir(parents=True)
        (generator.parent / "__init__.py").write_text("", encoding="utf-8")
        (generator / "__init__.py").write_text("", encoding="utf-8")
        (generator / "regen_views.py").write_text(
            "from pathlib import Path\nPath('vue.md').write_text('régénérée')\n",
            encoding="utf-8")
        for name in ("vue.md", "brouillon.md", "notes.md"):
            (git_repo / name).write_text("avant", encoding="utf-8")
        git(git_repo, "add", "-A")
        git(git_repo, "commit", "--no-verify", "-m", "générateur")
        git(git_repo, "config", "core.hooksPath", str(ENGINE_ROOT / "templates" / "vault" / ".githooks"))
        (git_repo / "brouillon.md").write_text("en cours, pas pour ce commit")
        (git_repo / "notes.md").write_text("le travail d'une session")
        git(git_repo, "add", "notes.md")
        done = subprocess.run(["git", "commit", "-m", "session"], cwd=git_repo,
                              capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": str(tmp_path / "fake")})
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
    """The reply goes out as soon as PostgreSQL has committed; views and push come after."""
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
