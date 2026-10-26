"""scripts/regen-views.py + VaultRepo.regenerate_views : la dérive des vues générées
ne peut plus être committée.

Deux CI rouges de suite (28 et 29/07) venaient du même trou : le bot ajoutait une
ligne au registre et committait, sans que personne relance les générateurs. Le hook
`pre-commit` ne couvrait que les clones où quelqu'un avait tapé
`git config core.hooksPath` — jamais le VPS après un reclone, jamais une session
Claude sur le web. Le seul endroit qui couvre tout le monde est le code qui commit.
"""

import re
import subprocess

from conftest import VAULT_ROOT, git, load_script

from redline.vault import VaultRepo
from scripts.model import hot_cache, strength_tables, views, weekly_plan

MARKER = re.compile(r"<!-- ([A-Z_]+)_START -->")


decisions_index = load_script("decisions-index")


class TestTheEntryPoint:
    def test_every_generator_it_names_exists(self):
        """Un générateur renommé sans être renommé ici passerait inaperçu : le script
        sortirait en erreur au prochain commit du bot, qui l'avale et le logge."""
        regen = load_script('regen-views')
        for name in regen.GENERATORS:
            assert (VAULT_ROOT / "scripts" / name).exists(), name

    def test_no_generated_block_in_the_vault_is_an_orphan(self):
        """LE test de la liste, réécrit le 2026-07-29. La version d'avant cherchait
        `"refresh(" in text` dans les scripts : `decisions-index.py` écrivait son bloc
        par un `re.sub` direct, donc l'heuristique le ratait — un garde-fou avec un
        faux négatif sur le seul cas qui existait.

        On interroge maintenant le VAULT, pas la forme du code : tout marqueur
        `<!-- X_START -->` présent dans une note doit avoir un générateur qui le
        revendique. C'est l'invariant qu'on veut (aucun bloc sans propriétaire), et il
        ne dépend d'aucune façon d'écrire les générateurs."""
        owned = {hot_cache.BLOCK, hot_cache.STATE_BLOCK, decisions_index.BLOCK,
                 weekly_plan.BLOCK,
                 strength_tables.LOG_BLOCK, *strength_tables.BLOCKS}
        found = {name for note in VAULT_ROOT.rglob("*.md")
                 if ".git" not in note.parts
                 for name in MARKER.findall(note.read_text(encoding="utf-8"))}
        assert found, "aucun marqueur trouvé : le test ne prouverait rien"
        assert found <= owned, f"bloc sans générateur : {sorted(found - owned)}"

    def test_check_reports_a_drift_without_repairing_it(self, tmp_path):
        """`--check` ne doit jamais réparer ce qu'il est censé signaler. Sur un vault
        jetable : la version d'avant lançait les vrais générateurs sur le vrai vault et
        n'assertait que « rien n'a bougé » — elle passait aussi bien si `--check`
        marchait que s'ils plantaient tous les deux avant d'écrire."""
        note = tmp_path / "note.md"
        start, end = views.markers("BLOC")
        note.write_text(f"avant\n{start}\npérimé\n{end}\naprès\n", encoding="utf-8")
        assert views.apply(note, "BLOC", "à jour", check=True) is True
        assert "périmé" in note.read_text(encoding="utf-8")
        assert views.apply(note, "BLOC", "à jour") is True
        assert "à jour" in note.read_text(encoding="utf-8")


def _persist_and_publish(git_repo):
    """Le tour committe ses notes avant la livraison ; les vues suivent après."""
    repo = VaultRepo(str(git_repo), "origin", "main")
    repo.persist_turn()
    repo.publish()
    return repo


class TestTheBotRegeneratesAfterDelivery:
    def _install(self, clone, body):
        scripts = clone / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "regen-views.py").write_text(body, encoding="utf-8")
        git(clone, "add", "-A")
        git(clone, "commit", "-m", "install generator")

    def test_the_generated_view_is_refreshed_and_committed(self, git_repo):
        """Le fait qui compte : ce que le générateur écrit se retrouve DANS le commit,
        pas dans le répertoire de travail à traîner jusqu'au prochain."""
        self._install(git_repo, "open('derived.md', 'w').write('frais')\n")
        (git_repo / "source.md").write_text("nouvelle ligne")
        _persist_and_publish(git_repo)
        committed = subprocess.run(["git", "log", "-2", "--name-only", "--format="],
                                   cwd=git_repo, capture_output=True, text=True).stdout
        assert "derived.md" in committed and "source.md" in committed
        assert not subprocess.run(["git", "status", "--porcelain"], cwd=git_repo,
                                  capture_output=True, text=True).stdout.strip()

    def test_a_broken_generator_does_not_swallow_the_session(self, git_repo, caplog):
        """Perdre les notes d'une session parce qu'une ligne du registre est malformée
        serait bien pire qu'un bloc périmé : on logge, on committe quand même."""
        self._install(git_repo, "import sys; sys.exit('registre illisible')\n")
        (git_repo / "source.md").write_text("notes de la session")
        _persist_and_publish(git_repo)
        assert "source.md" in subprocess.run(
            ["git", "show", "--name-only", "HEAD"],
            cwd=git_repo, capture_output=True, text=True).stdout
        assert "registre illisible" in caplog.text

    def test_a_clone_without_the_script_still_commits(self, git_repo):
        """Les dépôts de test (et tout clone qui n'est pas le vault) n'ont pas le
        script : son absence ne doit pas être une panne."""
        (git_repo / "source.md").write_text("x")
        _persist_and_publish(git_repo)
        assert "source.md" in subprocess.run(
            ["git", "show", "--name-only", "HEAD"],
            cwd=git_repo, capture_output=True, text=True).stdout

    def test_nothing_dirty_regenerates_nothing(self, git_repo):
        """Le bot appelle ça après CHAQUE message : deux sous-processus python sur un
        vault propre, plusieurs fois par heure, pour rien."""
        self._install(git_repo, "open('derived.md', 'w').write('ne devrait pas exister')\n")
        _persist_and_publish(git_repo)
        assert not (git_repo / "derived.md").exists()
