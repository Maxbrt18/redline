"""Runtime-safe synchronization of the versioned vault."""

from types import SimpleNamespace

from redline.service import vault as vault_module
from redline.service.vault import RUNTIME_PATHS, VaultRepo


def _result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _repo(tmp_path, monkeypatch, *, changed):
    repo = VaultRepo(str(tmp_path), "origin", "main")
    calls = []
    heads = iter(("local", "remote"))

    def fake_git(*args, log_errors=True):
        calls.append(args)
        if args[:2] == ("rev-parse", "HEAD"):
            return _result(stdout=next(heads) + "\n")
        if args[:2] == ("rev-parse", "FETCH_HEAD"):
            return _result(stdout="remote\n")
        if args[0] == "merge-base":
            return _result(stdout="base\n")
        if args[0] == "diff":
            return _result(stdout="\n".join(changed))
        return _result()

    monkeypatch.setattr(repo, "_git", fake_git)
    return repo, calls


def test_vault_only_commit_is_integrated(tmp_path, monkeypatch):
    repo, calls = _repo(
        tmp_path, monkeypatch,
        changed=["athlete/journal/2026-w33.md", "coaching/decisions.md"],
    )

    assert repo.sync() is True
    assert ("rebase", "--autostash", "remote") in calls


def test_runtime_commit_waits_for_deployment(tmp_path, monkeypatch):
    repo, calls = _repo(tmp_path, monkeypatch, changed=["src/redline/service/bot.py"])

    assert repo.sync() is False
    assert not any(call[0] == "rebase" for call in calls)
    assert "déploiement" in repo.drain_alerts()[0].text.lower()


def test_code_and_migrations_are_runtime_files(tmp_path, monkeypatch):
    for path in ("src/redline/analysis/ledger.py", "migrations/versions/new.py", "pyproject.toml"):
        repo, calls = _repo(tmp_path, monkeypatch, changed=[path])
        assert repo.sync() is False
        assert not any(call[0] == "rebase" for call in calls)


def test_runtime_prefixes_cover_all_executable_trees():
    assert {"deploy/", "src/", "migrations/"}.issubset(RUNTIME_PATHS)


def test_fetch_failure_keeps_local_state(tmp_path, monkeypatch):
    repo = VaultRepo(str(tmp_path), "origin", "main")
    monkeypatch.setattr(repo, "_head", lambda: "local")
    monkeypatch.setattr(repo, "_git", lambda *args, **kwargs: _result(returncode=1))

    assert repo.sync() is False


def test_unrelated_remote_head_is_refused(tmp_path, monkeypatch):
    repo, _ = _repo(tmp_path, monkeypatch, changed=[])

    def no_base(*args, log_errors=True):
        if args[:2] == ("rev-parse", "HEAD"):
            return _result(stdout="local\n")
        if args[:2] == ("rev-parse", "FETCH_HEAD"):
            return _result(stdout="remote\n")
        if args[0] == "merge-base":
            return _result(stdout="")
        return _result()

    monkeypatch.setattr(repo, "_git", no_base)
    assert repo.sync() is False
    assert "historique" in repo.drain_alerts()[0].text


def test_exercise_sheets_are_vault_content_not_runtime(tmp_path, monkeypatch):
    """An exercise sheet is data re-read on every call, not code.
        The coach edits these sheets itself: classifying them as runtime froze the vault
        synchronisation on a write it is allowed to make.

    """
    repo, calls = _repo(
        tmp_path, monkeypatch,
        changed=["coaching/exercises/plank-toe.md", "coaching/exercises/calf-drop.md"],
    )

    assert repo.sync() is True
    assert ("rebase", "--autostash", "remote") in calls
    assert repo.drain_alerts() == []


def test_test_files_do_not_wait_for_a_deployment(tmp_path, monkeypatch):
    repo, _ = _repo(tmp_path, monkeypatch, changed=["tests/test_charts.py"])

    assert repo.sync() is True
    assert repo.drain_alerts() == []


def test_a_test_riding_along_with_code_still_waits(tmp_path, monkeypatch):
    repo, _ = _repo(tmp_path, monkeypatch,
                    changed=["tests/test_charts.py", "src/redline/service/bot.py"])

    assert repo.sync() is False
    assert "déploiement" in repo.drain_alerts()[0].text.lower()


def test_the_same_cause_is_queued_once(tmp_path, monkeypatch):
    repo, _ = _repo(tmp_path, monkeypatch, changed=["src/redline/service/bot.py"])

    repo.sync()
    repo._alert(vault_module.ALERT_DEPLOY, "un autre texte, même cause")

    assert len(repo.drain_alerts()) == 1


def test_a_push_blocked_by_a_pending_deployment_is_one_incident(tmp_path, monkeypatch):
    """The refused push is the CONSEQUENCE of the pending deployment, not a 2nd incident."""
    repo = VaultRepo(str(tmp_path), "origin", "main")
    heads = iter(("local", "remote", "local", "remote"))

    def fake_git(*args, log_errors=True):
        if args[:2] == ("rev-parse", "HEAD"):
            return _result(stdout=next(heads) + "\n")
        if args[:2] == ("rev-parse", "FETCH_HEAD"):
            return _result(stdout="remote\n")
        if args[0] == "merge-base":
            return _result(stdout="base\n")
        if args[0] == "diff":
            return _result(stdout="src/redline/service/bot.py")
        if args[0] == "push":
            return _result(returncode=1, stderr="non-fast-forward")
        return _result()

    monkeypatch.setattr(repo, "_git", fake_git)

    assert repo._push() is False
    alerts = repo.drain_alerts()
    assert len(alerts) == 1
    assert "déploiement" in alerts[0].text.lower()
    # and it says what that costs, otherwise it calls for no action
    assert "local" in alerts[0].text.lower()


def test_a_push_failing_on_its_own_still_says_so(tmp_path, monkeypatch):
    repo = VaultRepo(str(tmp_path), "origin", "main")

    def fake_git(*args, log_errors=True):
        if args[0] == "push":
            return _result(returncode=1, stderr="réseau")
        if args[:2] == ("rev-parse", "HEAD"):
            return _result(stdout="local\n")
        return _result(returncode=1)

    monkeypatch.setattr(repo, "_git", fake_git)

    assert repo._push() is False
    alerts = repo.drain_alerts()
    assert len(alerts) == 1
    assert "push" in alerts[0].text.lower()
