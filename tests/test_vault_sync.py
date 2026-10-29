"""Synchronization of the versioned vault."""

from types import SimpleNamespace

from redline.service.vault import VaultRepo


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


def test_a_commit_that_names_engine_paths_is_integrated_like_any_other(tmp_path, monkeypatch):
    """The vault is never the engine: a `src/` folder in it is notes, not code."""
    repo, calls = _repo(tmp_path, monkeypatch, changed=["src/notes.md", "deploy/plan.md"])

    assert repo.sync() is True
    assert ("rebase", "--autostash", "remote") in calls
    assert repo.drain_alerts() == []


def test_exercise_sheets_are_integrated(tmp_path, monkeypatch):
    repo, calls = _repo(
        tmp_path, monkeypatch,
        changed=["coaching/exercises/plank-toe.md", "coaching/exercises/calf-drop.md"],
    )

    assert repo.sync() is True
    assert ("rebase", "--autostash", "remote") in calls
    assert repo.drain_alerts() == []


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
