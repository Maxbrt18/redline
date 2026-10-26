"""Shared fixtures. `redline` and `model` are importable through the `pythonpath`
setting of the root pyproject.toml.

Everything a test needs to STAND UP a bot lives here, and nowhere else. It used to be
copied: `_bot(...)` had four near-identical definitions, the fake Claude three, `_git`
two, and the vault root six sites in two idioms. The cost is not the ~90 lines -- it is
that adding a parameter to `CoachBot.__init__` meant finding four call sites, and that
a test file quietly wiring its own bot differently from the others is a difference
nobody sees (audit 2026-07-29).
"""

import importlib.util
import subprocess
import sys
from datetime import time as dtime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from redline.agent import RunResult, RunSpec, Triage
from redline.config import Config
from redline.messenger import DeliveryResult
from redline.routing import RoutingConfig
from scripts.model import instance as instance_model

# `SERVER_DIR.parent`, not `parent.parent.parent`: it survives moving `tests/`.
SERVER_DIR = Path(__file__).resolve().parent.parent
VAULT_ROOT = SERVER_DIR.parent
# L'instance fictive des tests (redline.toml) : le seul endroit où son prénom est écrit.
INSTANCE_DIR = SERVER_DIR / "tests" / "fixtures" / "instance"

# Les cibles de RPE telles que `coaching/workouts/*.md` les déclare aujourd'hui.
# LITTÉRALES ici, et déclarées une seule fois pour toute la suite : les tests de jauge
# doivent mesurer la FORMULE, pas le contenu du vault, qui bouge plusieurs fois par jour
# (même raison que `test_the_checker_runs_on_the_real_vault_and_reports`). Qu'elles
# correspondent encore aux fiches est vérifié à part, une fois, par
# `test_the_targets_come_from_the_workout_notes`.
RPE_TARGETS = {"EF": 3.0, "SL": 4.5, "seuil": 6.0, "côtes": 6.0, "lignes": 3.0,
               "cadence": 3.0, "vélo": 3.0, "renfoA": 6.0, "renfoB": 6.0}

_DEFAULTS = {
    "page_token": "page-token",
    "verify_token": "verify-token",
    "app_secret": "",
    "allowed_psid": "4242",
    "graph_version": "v21.0",
    "webhook_port": 8000,
    "vault_path": ".",
    "agent_provider": "codex",
    "claude_bin": "claude",
    "codex_bin": None,
    "agent_timeout": 600,
    "agent_timeout_deep": 1800,
    "agent_timeout_heavy": 5400,
    "triage_enabled": True,
    "triage_timeout": 10,
    "git_remote": "origin",
    "git_branch": "main",
    "database_url": "",
    "session_ttl_hours": 6,
    "debounce_seconds": 5,
    "debounce_max_seconds": 20,
    "proactive_enabled": True,
    "proactive_active_start": dtime(7, 30),
    "proactive_min_gap_hours": 4,
    "proactive_silence_days": 3,
    "proactive_morning": dtime(7, 45),
    "proactive_weekly": dtime(19, 0),
    "proactive_monthly": dtime(18, 30),
    "strava_debrief_delay": 600,
    "strava_verify_token": "",
    "strava_owner_id": "",
    "healthcheck_ping_url": "",
    "coach_base_url": "https://coach.test",
    "strength_reports_per_day": 8,
    "attachment_dir": "server/attachments",
    "attachment_max_bytes": 12 * 1024 * 1024,
    "attachment_total_bytes": 20 * 1024 * 1024,
    "instance": instance_model.load(INSTANCE_DIR),
}


@pytest.fixture
def make_config():
    """Test Config factory: make_config(proactive_min_gap_hours=0, ...)."""
    def factory(**overrides) -> Config:
        values = {**_DEFAULTS, **overrides}
        if "routing" not in overrides:
            values["routing"] = RoutingConfig.default(
                values["agent_provider"],
                agent_timeout=values["agent_timeout"],
                agent_timeout_deep=values["agent_timeout_deep"],
                agent_timeout_heavy=values["agent_timeout_heavy"],
                triage_timeout=values["triage_timeout"],
            )
        return Config(**values)
    return factory


@pytest.fixture
def store(tmp_path):
    from fake_state import FakeStateStore
    return FakeStateStore()


class FakeAgent:
    """An AgentRunner stand-in that records calls instead of spawning anything.

    The three copies it replaces differed only in what they returned and whether they
    could classify -- both parameters here.
    """

    def __init__(self, result=None, depth="N1", emotional=False, holding=None,
                 plan_change=False, plan_asked=False, plan_scope="none"):
        self.provider = "codex"
        self.calls: list[dict] = []
        self._result = result if result is not None else RunResult(full_text="ok")
        self._depth = depth
        self._emotional = emotional
        self._holding = holding
        self._plan_change = plan_change
        self._plan_asked = plan_asked
        self._plan_scope = plan_scope

    def classify(self, message, *, spec=None):
        return Triage(self._depth, self._emotional, self._holding, self._plan_change,
                      self._plan_asked, self._plan_scope)

    def run(self, psid, message, *, spec: RunSpec, turn_id=None):
        self.calls.append({"psid": psid, "message": message,
                           "turn_id": turn_id,
                           "spec": spec, "model": spec.model,
                           "session_mode": spec.session_mode,
                           "effort": spec.effort, "timeout": spec.timeout,
                           "read_only": spec.read_only})
        return self._result

    def close(self):
        pass

    def activity(self, psid):
        return None

    def cancel(self, psid):
        return False


class RecordingBot:
    """A CoachBot stand-in for the HTTP routes: records what a route asks of the
    bot, without running anything.

    Here rather than in a test file because it was copied in two, and `web.py`
    reaching for one more attribute (`repo`, for /health) broke the copy that had
    not been updated — the exact failure mode this module exists to prevent.
    """

    def __init__(self, head: str = "0123456789abcdef0123"):
        self.inbound: list = []
        self.acknowledged: list = []
        self.attachments: list = []
        self.repo = SimpleNamespace(head=lambda: head, runtime_tree=lambda: "0123456789ab")

    def submit_inbound(self, psid, text, *, message_id=""):
        del message_id
        self.inbound.append((psid, text))

    def submit_event(self, psid, kind, text, *, message_id):
        if kind in {"text", "postback"}:
            self.submit_inbound(psid, text, message_id=message_id)
        elif kind == "sticker":
            self.acknowledge(psid)
        else:
            self.submit_attachment(psid, text, message_id=message_id)

    def acknowledge(self, psid):
        self.acknowledged.append(psid)

    def submit_attachment(self, psid, payload="", *, message_id=""):
        self.attachments.append((psid, payload, message_id))

    def is_running(self):
        return True


@pytest.fixture
def make_bot(make_config, store):
    """Bot factory: make_bot(agent=FakeAgent(), proactive_min_gap_hours=0).

    The mocks are the ones every caller wired by hand: a repo that pulled nothing and
    an outbox that held nothing.
    """
    def factory(agent=None, repo=None, outbox=None, client=None, attachments=None, **config):
        if agent is None:
            agent = FakeAgent()
        if repo is None:
            from redline.vault import PersistenceResult
            repo = MagicMock()
            repo.sync.return_value = False
            repo.is_clean.return_value = True
            # Rend une LISTE : sur un MagicMock nu, « rien à jeter » serait un mock
            # truthy, et toute la suite journaliserait un résidu imaginaire.
            repo.ensure_clean.return_value = []
            repo.weekly_plan_context.return_value = ""
            # `weekly_plan_message` rend une CHAÎNE, et depuis le 2026-08-11 elle est
            # assemblée avec le texte du coach au lieu de le remplacer : un MagicMock
            # truthy passait tant qu'on ne faisait que l'affecter, plus depuis qu'on
            # le concatène.
            repo.weekly_plan_message.return_value = "Aujourd'hui : EF, plafond 9 km."
            repo.persist_turn.return_value = PersistenceResult(True)
            # Une seule lecture du registre par tour : (réclamables, hors fenêtre).
            repo.ledger_cells.return_value = ([], [])
            repo.publish.return_value = True
            repo.drain_alerts.return_value = []
            repo.head.return_value = "0123456789abcdef"
        if outbox is None:
            outbox = MagicMock()
            outbox.snapshot.return_value = set()
            outbox.collect_new.return_value = []
            outbox.link_for.return_value = None
            # Rend une LISTE : sur un MagicMock nu, « rien à solder » serait truthy et
            # chaque bot de la suite signalerait un résidu d'outbox imaginaire.
            outbox.leftovers.return_value = []
        if client is None:
            client = MagicMock()
            client.send_text.return_value = DeliveryResult.success()
            client.send_burst.return_value = DeliveryResult.success()
            client.send_image.return_value = DeliveryResult.success()
            client.send_button.return_value = DeliveryResult.success()
            client.action.return_value = DeliveryResult.success()
        from redline.bot import CoachBot
        return CoachBot(client=client,
                        repo=repo, agent=agent, state=store,
                        config=make_config(**config), outbox=outbox,
                        attachments=attachments)
    return factory


def load_script(name: str):
    """Charge un `scripts/<name>.py` : un nom à tiret n'est pas un module importable.

    Le module est enregistré dans `sys.modules` AVANT d'être exécuté, sinon un
    `@dataclass` défini dedans échoue à résoudre son propre module (`vault-check.py`).
    """
    path = SERVER_DIR.parent / "scripts" / f"{name}.py"
    module_name = name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"impossible de charger {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    """A one-commit git repo, the shape both `VaultRepo` suites start from."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "note.md").write_text("v1")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "v1")
    return repo


@pytest.fixture
def git_clone(tmp_path, git_repo):
    """(origin, clone), for the pull tests: an 'origin' and a local clone, like
    GitHub vs the VPS."""
    # Lets a future test push into this non-bare origin.
    git(git_repo, "config", "receive.denyCurrentBranch", "ignore")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(git_repo), str(clone))
    # UN CLONE N'HÉRITE PAS DU `.git/config` DE SON ORIGINE : sans ces deux lignes, tout
    # test qui commite dans le clone passe sur le Mac (identité globale) et meurt sur la
    # CI en « Author identity unknown » (2026-08-12). Même famille que les deux tests qui
    # lisaient l'heure du mur le 2026-08-11 : un environnement implicite qu'on croit
    # maîtrisé. La fixture le rend explicite une fois pour toutes.
    git(clone, "config", "user.email", "t@t")
    git(clone, "config", "user.name", "t")
    return git_repo, clone


# Tests that exercise the CONTENT of a populated vault (strength programme, workout notes,
# decisions log, capability notes...) rather than the engine. This repository ships the
# engine and templates only, so they are skipped until you provide a vault with matching
# content (`coaching/workouts/`, `coaching/exercises/`, `coaching/decisions.md`, ...).
_VAULT_CONTENT_MODULES = {
    "test_programme.py", "test_carte_renfo.py", "test_charts.py", "test_weekly_plan.py",
    "test_rulebook.py", "test_ledger.py", "test_decisions_index.py",
    "test_coaching_scenarios.py", "test_capabilities.py",
}
_VAULT_CONTENT_TESTS = {
    "test_active_strength_variant_must_exist_in_the_programme",
    "test_active_strength_drop_must_name_an_exercise_in_the_programme",
    "test_every_rule_names_an_owner_that_exists",
    "test_the_targets_come_from_the_workout_notes",
    "test_toutes_les_rubriques_du_vault_reel_sont_indexables",
    "test_a_scenario_born_from_a_correction_names_a_real_owner",
    "test_the_real_analysis_notes_conform_without_operational_state",
    "test_the_weekly_dossier_is_computed_from_the_same_functions_as_the_clis",
    "test_notebook_current_resolves_the_same_typed_workout_and_rechecks_claims",
}


def pytest_collection_modifyitems(config, items):
    if (VAULT_ROOT / "coaching" / "workouts").is_dir():
        return
    skip = pytest.mark.skip(reason="needs a populated vault (coaching/workouts, exercises...)")
    for item in items:
        module = Path(str(item.fspath)).name
        name = item.name.split("[")[0]
        if name in _VAULT_CONTENT_TESTS or (
                module in {"test_programme.py", "test_carte_renfo.py"}
                or (module == "test_charts.py" and _is_strength(item))):
            item.add_marker(skip)


def _is_strength(item) -> bool:
    return "strength" in item.nodeid or item.name in {
        "test_no_link_without_configuration", "test_a_derogation_must_carry_a_typed_dose",
        "test_the_overflow_guard_actually_fires",
        "test_the_worst_payload_the_schema_allows_still_gets_a_sidecar"}


# La zone surveillée de l'instance fictive (`[[injuries]]` de redline.toml) : les tests
# de douleur lisent cette zone, jamais une douleur sans zone.
ZONE = "injury"


def zoned(value):
    """`pains` d'une ligne du registre : la douleur de la zone fictive, ou rien."""
    return {} if value is None else {ZONE: value}
