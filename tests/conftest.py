"""Shared fixtures. `redline` is importable through the `pythonpath` setting of the root
pyproject.toml.

Everything a test needs to STAND UP a bot lives here, and nowhere else. It used to be
copied: `_bot(...)` had four near-identical definitions, the fake Claude three, `_git`
two, and the vault root six sites in two idioms. The cost is not the ~90 lines -- it is
that adding a parameter to `CoachBot.__init__` meant finding four call sites, and that
a test file quietly wiring its own bot differently from the others is a difference
nobody sees.
"""

import atexit
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import time as dtime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from redline import cli
from redline.model import instance as instance_model
from redline.service.agent import RunResult, RunSpec, Triage
from redline.service.config import Config
from redline.service.messenger import DeliveryResult
from redline.service.routing import RoutingConfig

TESTS_DIR = Path(__file__).resolve().parent
ENGINE_ROOT = TESTS_DIR.parent
#: Units, launcher, allowlist and scripts of a deployment.
DEPLOY_DIR = ENGINE_ROOT / "deploy"
# The fictional test vault: `redline.toml` (the only place the fictional athlete's
# first name is written), the strength program, session sheets, capabilities and the
# decision log. Nobody real: it exists so that tests reading vault content run in this
# repository, which ships only the engine.
TEST_VAULT = TESTS_DIR / "fixtures" / "vault"
# The suite runs like the service: the engine on one side, the vault on the other,
# designated by `VAULT_PATH`. A COPY, so a test writing into the vault does not dirty the
# versioned fixture; it is made before any import that would read `VAULT_PATH`.
VAULT_ROOT = Path(tempfile.mkdtemp(prefix="redline-vault-")) / "vault"
shutil.copytree(TEST_VAULT, VAULT_ROOT)
atexit.register(shutil.rmtree, VAULT_ROOT.parent, ignore_errors=True)
os.environ["VAULT_PATH"] = str(VAULT_ROOT)

# RPE targets as `coaching/workouts/*.md` declares them. LITERAL here, and declared once
# for the whole suite: gauge tests must measure the FORMULA, not the vault content,
# which changes several times a day (same reason as
# `test_the_checker_runs_on_the_real_vault_and_reports`). That they still match the
# sheets is checked separately, once, by `test_the_targets_come_from_the_workout_notes`.
RPE_TARGETS = {("easy", None): 3.0, ("long", None): 4.5, ("threshold", None): 6.0,
               ("hills", None): 6.0, ("strides", None): 3.0, ("cadence", None): 3.0,
               ("bike", None): 3.0, ("strength", "a"): 6.0, ("strength", "b"): 6.0,
               ("strength", "assimilation"): 3.0}

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
    "attachment_dir": "var/attachments",
    "attachment_max_bytes": 12 * 1024 * 1024,
    "attachment_total_bytes": 20 * 1024 * 1024,
    "instance": instance_model.load(TEST_VAULT),
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
            from redline.service.vault import PersistenceResult
            repo = MagicMock()
            repo.sync.return_value = False
            repo.is_clean.return_value = True
            # Returns a LIST: on a bare MagicMock, "nothing to discard" would be a truthy
            # mock, and the whole suite would log an imaginary residue.
            repo.ensure_clean.return_value = []
            repo.weekly_plan_context.return_value = ""
            # `weekly_plan_message` returns a STRING, assembled with the coach text
            # instead of replacing it: a truthy MagicMock was enough when it was only
            # assigned, not once it is concatenated.
            repo.weekly_plan_message.return_value = "Aujourd'hui : EF, plafond 9 km."
            repo.persist_turn.return_value = PersistenceResult(True)
            # A single registry read per turn: (claimable, out of window).
            repo.ledger_cells.return_value = ([], [])
            repo.publish.return_value = True
            repo.drain_alerts.return_value = []
            repo.head.return_value = "0123456789abcdef"
        if outbox is None:
            outbox = MagicMock()
            outbox.snapshot.return_value = set()
            outbox.collect_new.return_value = []
            outbox.link_for.return_value = None
            # Returns a LIST: on a bare MagicMock, "nothing to settle" would be truthy and
            # every bot in the suite would report an imaginary outbox residue.
            outbox.leftovers.return_value = []
        if client is None:
            client = MagicMock()
            client.send_text.return_value = DeliveryResult.success()
            client.send_burst.return_value = DeliveryResult.success()
            client.send_image.return_value = DeliveryResult.success()
            client.send_button.return_value = DeliveryResult.success()
            client.action.return_value = DeliveryResult.success()
        from redline.service.bot import CoachBot
        return CoachBot(client=client,
                        repo=repo, agent=agent, state=store,
                        config=make_config(**config), outbox=outbox,
                        attachments=attachments)
    return factory


#: Commands run by hand by the operator, never by the coach nor in its sandbox.
OPERATOR_TOOLS = frozenset({"evaluate_coaching.py", "evaluate_conversation.py",
                            "notebook_import.py", "setup_messenger_profile.py"})


def load_script(name: str):
    """The module of the command `name` (`vault-check` -> `redline.cli.vault_check`)."""
    return importlib.import_module(cli.module(name))


def cli_command(name: str) -> list[str]:
    """How a test runs the command `name` in a subprocess."""
    return [sys.executable, "-m", cli.module(name)]


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
    # A CLONE DOES NOT INHERIT ITS ORIGIN'S `.git/config`: without these two lines, any
    # test committing in the clone passes on a laptop (global identity) and dies on CI
    # with "Author identity unknown". Same family as tests reading the wall clock: an
    # implicit environment assumed to be controlled. The fixture makes it explicit.
    git(clone, "config", "user.email", "t@t")
    git(clone, "config", "user.name", "t")
    return git_repo, clone


# The watched zone of the fictional instance (`[[injuries]]` in redline.toml): pain
# tests read this zone, never a pain without a zone.
ZONE = "injury"


def zoned(value):
    """`pains` of a registry row: the pain of the fictional zone, or nothing."""
    return {} if value is None else {ZONE: value}
