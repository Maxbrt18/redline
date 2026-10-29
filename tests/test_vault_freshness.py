"""The staleness contract: a sync that brings new commits while a model session
is alive must make the bot ask for a hot-cache re-read (STALE_VAULT_NOTE), and
ONLY in that case — a fresh session re-reads the vault anyway.
"""

import time

from conftest import git

from redline.service.prompts import STALE_VAULT_NOTE
from redline.service.vault import VaultRepo
from redline.service.worker import TurnWorker


class TestVaultRepoSync:
    def test_sync_without_new_commits_returns_false(self, git_clone):
        _, clone = git_clone
        repo = VaultRepo(str(clone), "origin", "main")
        assert repo.sync() is False

    def test_sync_with_new_vault_commits_returns_true(self, git_clone):
        origin, clone = git_clone
        (origin / "note.md").write_text("v2")
        git(origin, "commit", "-am", "v2")
        repo = VaultRepo(str(clone), "origin", "main")
        assert repo.sync() is True
        assert repo.sync() is False


class TestWithFreshness:
    def _bot(self, store, make_config):
        # Only _state and _config are exercised by _with_freshness.
        config = make_config()
        return TurnWorker(client=None, repo=None, agent=type("Agent", (), {"provider": "claude"})(), state=store,
                        config=config, outbox=None)

    def test_note_prepended_for_live_session_when_vault_moved(self, store, make_config):
        bot = self._bot(store, make_config)
        store.set_session("4242", "claude", "session-abc")
        prompt = bot._with_freshness("4242", "salut", vault_changed=True)
        assert prompt.startswith(STALE_VAULT_NOTE)
        assert prompt.endswith("salut")

    def test_no_note_when_vault_unchanged(self, store, make_config):
        bot = self._bot(store, make_config)
        store.set_session("4242", "claude", "session-abc")
        assert bot._with_freshness("4242", "salut", vault_changed=False) == "salut"

    def test_no_note_without_session(self, store, make_config):
        bot = self._bot(store, make_config)
        assert bot._with_freshness("4242", "salut", vault_changed=True) == "salut"

    def test_no_note_for_expired_session(self, store, make_config):
        bot = self._bot(store, make_config)
        store.set_session("4242", "claude", "session-abc")
        # Age the session past the TTL (6 h in the test defaults).
        store.section("sessions")["4242"]["last"] = time.time() - 7 * 3600
        assert bot._with_freshness("4242", "salut", vault_changed=True) == "salut"
