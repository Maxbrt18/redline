"""scripts/regen-views.py + VaultRepo.regenerate_views: drift in generated views
can no longer be committed.

The bot used to append a row to the ledger and commit without anyone rerunning the
generators. A `pre-commit` hook only covered clones where someone had set
`git config core.hooksPath` -- never a fresh clone on the server, never a web session.
The only place that covers everyone is the code that commits.
"""

import importlib.util
import re
import subprocess
import sys

from conftest import ENGINE_ROOT, VAULT_ROOT, load_script

from redline import cli
from redline.model import hot_cache, programme, strength_tables, views, weekly_plan
from redline.service.vault import VaultRepo

MARKER = re.compile(r"<!-- ([A-Z_]+)_START -->")



class TestTheEntryPoint:
    def test_every_generator_it_names_exists(self):
        """A generator renamed without being renamed here would go unnoticed: the script
        would exit with an error at the bot's next commit, which swallows and logs it."""
        regen = load_script('regen-views')
        for name in regen.GENERATORS:
            assert importlib.util.find_spec(cli.module(name)), name

    def test_no_generated_block_in_the_vault_is_an_orphan(self):
        """THE test of the list. An earlier version looked for
        `"refresh(" in text` in the scripts: `decisions-index.py` wrote its block
        with a direct `re.sub`, so the heuristic missed it -- a guard with a false
        negative on the only case that existed.

        It now queries the VAULT, not the shape of the code: every marker
        `<!-- X_START -->` present in a note must have a generator that
        claims it. This is the invariant we want (no block without an owner), and it
        does not depend on how the generators are written."""
        owned = {hot_cache.BLOCK, hot_cache.STATE_BLOCK,
                 weekly_plan.BLOCK,
                 strength_tables.LOG_BLOCK,
                 *strength_tables.blocks(programme.load(VAULT_ROOT))}
        found = {name for note in ENGINE_ROOT.rglob("*.md")
                 if ".git" not in note.parts
                 for name in MARKER.findall(note.read_text(encoding="utf-8"))}
        assert found, "aucun marqueur trouvé : le test ne prouverait rien"
        assert found <= owned, f"bloc sans générateur : {sorted(found - owned)}"

    def test_check_reports_a_drift_without_repairing_it(self, tmp_path):
        """`--check` must never repair what it is supposed to report. On a throwaway
        vault: an earlier version ran the real generators on the real vault and
        only asserted "nothing moved" -- it passed just as well if `--check`
        worked as if they both crashed before writing."""
        note = tmp_path / "note.md"
        start, end = views.markers("BLOC")
        note.write_text(f"avant\n{start}\npérimé\n{end}\naprès\n", encoding="utf-8")
        assert views.apply(note, "BLOC", "à jour", check=True) is True
        assert "périmé" in note.read_text(encoding="utf-8")
        assert views.apply(note, "BLOC", "à jour") is True
        assert "à jour" in note.read_text(encoding="utf-8")


def _persist_and_publish(git_repo):
    """The turn commits its notes before delivery; the views follow after."""
    generator = git_repo.parent / "generator.py"
    repo = VaultRepo(str(git_repo), "origin", "main",
                     regen_command=(sys.executable, str(generator)) if generator.exists() else ())
    repo.persist_turn()
    repo.publish()
    return repo


class TestTheBotRegeneratesAfterDelivery:
    def _install(self, clone, body):
        (clone.parent / "generator.py").write_text(body, encoding="utf-8")

    def test_the_generated_view_is_refreshed_and_committed(self, git_repo):
        """What matters: what the generator writes ends up IN the commit,
        not in the working directory left dangling until the next one."""
        self._install(git_repo, "open('derived.md', 'w').write('frais')\n")
        (git_repo / "source.md").write_text("nouvelle ligne")
        _persist_and_publish(git_repo)
        committed = subprocess.run(["git", "log", "-2", "--name-only", "--format="],
                                   cwd=git_repo, capture_output=True, text=True).stdout
        assert "derived.md" in committed and "source.md" in committed
        assert not subprocess.run(["git", "status", "--porcelain"], cwd=git_repo,
                                  capture_output=True, text=True).stdout.strip()

    def test_a_broken_generator_does_not_swallow_the_session(self, git_repo, caplog):
        """Losing a session's notes because a ledger row is malformed
        would be much worse than a stale block: log it, commit anyway."""
        self._install(git_repo, "import sys; sys.exit('registre illisible')\n")
        (git_repo / "source.md").write_text("notes de la session")
        _persist_and_publish(git_repo)
        assert "source.md" in subprocess.run(
            ["git", "show", "--name-only", "HEAD"],
            cwd=git_repo, capture_output=True, text=True).stdout
        assert "registre illisible" in caplog.text

    def test_a_clone_without_the_script_still_commits(self, git_repo):
        """Test repositories (and any clone that is not the vault) do not have the
        script: its absence must not be a failure."""
        (git_repo / "source.md").write_text("x")
        _persist_and_publish(git_repo)
        assert "source.md" in subprocess.run(
            ["git", "show", "--name-only", "HEAD"],
            cwd=git_repo, capture_output=True, text=True).stdout

    def test_nothing_dirty_regenerates_nothing(self, git_repo):
        """The bot calls this after EVERY message: two python subprocesses on a
        clean vault, several times an hour, for nothing."""
        self._install(git_repo, "open('derived.md', 'w').write('ne devrait pas exister')\n")
        _persist_and_publish(git_repo)
        assert not (git_repo / "derived.md").exists()
