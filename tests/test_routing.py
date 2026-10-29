"""Single routing policy and bounded fallback."""

from dataclasses import replace

import pytest

from redline.service.agent import FailureKind, RunResult, SessionMode
from redline.service.routing import Route, RoutingConfig, RoutingError
from redline.service.turns import TurnExecutor
from redline.service.vault import PersistenceResult


def _from_file(tmp_path, text, provider="codex"):
    path = tmp_path / "routing.toml"
    path.write_text(text, encoding="utf-8")
    return RoutingConfig.from_file(provider, path, agent_timeout=600,
                                   agent_timeout_deep=1800, agent_timeout_heavy=5400,
                                   triage_timeout=30)


def test_the_triage_is_one_fast_classifier_without_fallback():
    plan = RoutingConfig.default("codex").plan(Route.TRIAGE)
    assert (plan.primary.model, plan.primary.effort, plan.primary.timeout) == (
        "gpt-6-luna", "low", 10)
    assert plan.fallbacks == ()
    assert RoutingConfig.default("claude").plan(Route.TRIAGE).fallbacks == ()


def test_all_existing_efforts_and_timeouts_are_preserved():
    config = RoutingConfig.default("codex")
    for route, effort, fallback, timeout in (
            (Route.N0, "medium", "high", 600),
            (Route.N1, "high", "high", 600),
            (Route.N2, "medium", "max", 1800),
            (Route.N3, "high", "max", 1800),
            (Route.DEEP, "high", "max", 1800),
            (Route.HEAVY, "high", "max", 5400)):
        plan = config.plan(route)
        assert plan.primary.effort == effort
        assert plan.fallbacks[0].effort == fallback
        assert all(spec.timeout == timeout for spec in plan.attempts)


def test_codex_matrix_has_luna_n1_and_sol_n2_n3():
    config = RoutingConfig.default("codex")
    assert (config.plan(Route.N1).primary.model,
            config.plan(Route.N1).primary.effort) == ("gpt-6-luna", "high")
    assert (config.plan(Route.N2).primary.model,
            config.plan(Route.N2).primary.effort) == ("gpt-6-sol", "medium")
    assert (config.plan(Route.N3).primary.model,
            config.plan(Route.N3).primary.effort) == ("gpt-6-sol", "high")


def test_a_route_can_be_overridden_without_touching_the_other_routes(tmp_path):
    config = _from_file(tmp_path, '[routing.N1]\nmodel = "gpt-6-sol"\neffort = "high"\n')
    assert config.plan(Route.N1).primary.model == "gpt-6-sol"
    assert config.plan(Route.N1).fallbacks == RoutingConfig.default("codex").plan(
        Route.N1).fallbacks
    assert config.plan(Route.N2) == RoutingConfig.default("codex").plan(Route.N2)


def test_a_fallback_is_overridden_on_its_own(tmp_path):
    config = _from_file(tmp_path, '[routing.N3]\nfallback = { effort = "xhigh" }\n')
    assert config.plan(Route.N3).fallbacks[0].effort == "xhigh"
    assert config.plan(Route.N3).primary == RoutingConfig.default("codex").plan(
        Route.N3).primary


def test_no_file_keeps_the_defaults():
    config = RoutingConfig.from_file("claude", None, agent_timeout=600,
                                     agent_timeout_deep=1800, agent_timeout_heavy=5400,
                                     triage_timeout=10)
    assert config.plans == RoutingConfig.default("claude").plans


@pytest.mark.parametrize("text,refused", [
    ('[routing.TRIAGE]\nfallback = { model = "x" }\n', "has none"),
    ('[routing.N7]\nmodel = "x"\n', "unknown route"),
    ('[routing.N1]\nmodele = "x"\n', "allowed keys"),
    ('[routing.N1]\nfallback = { temperature = "x" }\n', "allowed keys"),
    ('[routing.N1]\nmodel = ""\n', "non-empty string"),
    ('[routing.N1]\neffort = "turbo"\n', "not a codex effort"),
    ('[agent]\nmodel = "x"\n', r"only \[routing"),
    ('[routing.N1\n', "unreadable"),
])
def test_an_unusable_file_stops_startup(tmp_path, text, refused):
    with pytest.raises(RoutingError, match=refused):
        _from_file(tmp_path, text)


def test_the_engine_refuses_no_model(tmp_path):
    """What a route may cost is the operator's choice, not a list in the engine."""
    config = _from_file(tmp_path, '[routing.N3]\nmodel = "gpt-6-astra"\neffort = "max"\n')
    assert config.plan(Route.N3).primary.model == "gpt-6-astra"


def test_turn_executor_tries_the_fallback_before_persisting(make_config):
    from unittest.mock import MagicMock

    agent = MagicMock(provider="codex")
    agent.run.side_effect = [
        RunResult(error="modèle indisponible", retry_at=123.0,
                  failure=FailureKind.PREFLIGHT),
        RunResult(full_text="réponse de secours"),
    ]
    repo = MagicMock()
    repo.head.return_value = "base"
    repo.is_clean.return_value = True
    repo.persist_turn.return_value = PersistenceResult(True)
    state = MagicMock()
    plan = RoutingConfig.default("codex").plan(Route.N1)

    outcome = TurnExecutor(agent, repo, state).execute("4242", "salut", plan=plan)

    assert outcome.result.full_text == "réponse de secours"
    assert agent.run.call_count == 2
    assert agent.run.call_args_list[0].kwargs["spec"] == plan.primary
    assert agent.run.call_args_list[1].kwargs["spec"] == plan.fallbacks[0]
    repo.persist_turn.assert_called_once()


def test_turn_executor_never_relaunches_work_that_already_started():
    from unittest.mock import MagicMock

    agent = MagicMock(provider="codex")
    agent.run.return_value = RunResult(
        error="watchdog", failure=FailureKind.WATCHDOG, work_started=True)
    repo = MagicMock()
    repo.head.return_value = "base"
    repo.is_clean.return_value = True
    state = MagicMock()

    outcome = TurnExecutor(agent, repo, state).execute(
        "4242", "travail", plan=RoutingConfig.default("codex").plan(Route.N2))

    assert outcome.result.failure is FailureKind.WATCHDOG
    assert agent.run.call_count == 1
    repo.persist_turn.assert_not_called()


def test_a_read_only_turn_discards_database_and_git_changes():
    """The boundary stays effective when Codex runs in danger-full-access."""
    from unittest.mock import MagicMock

    agent = MagicMock(provider="codex")
    agent.run.return_value = RunResult(full_text="observation")
    repo = MagicMock()
    repo.head.side_effect = ["base", "commit-inattendu"]
    state = MagicMock()
    plan = RoutingConfig.default("codex").plan(Route.N1).with_read_only()

    result = TurnExecutor(agent, repo, state).read_only(
        "4242", "analyse", plan=plan, turn_id="turn-read-only")

    assert result.full_text == "observation"
    repo.discard_turn.assert_called_once_with("turn-read-only")
    repo.abandon_turn.assert_called_once_with("base")


def test_a_failed_fork_does_not_destroy_the_interactive_session():
    from unittest.mock import MagicMock

    agent = MagicMock(provider="codex")
    agent.run.return_value = RunResult(error="indisponible")
    repo = MagicMock()
    repo.head.return_value = "base"
    repo.is_clean.return_value = True
    state = MagicMock()
    plan = RoutingConfig.default("codex").plan(Route.DEEP)

    TurnExecutor(agent, repo, state).execute("4242", "creuse", plan=plan)

    state.clear_session.assert_not_called()


@pytest.mark.parametrize("effort", ["none", "minimal", "ultra"])
def test_route_validation_rejects_efforts_unknown_to_claude(effort):
    base = RoutingConfig.default("claude")
    n1 = base.plan(Route.N1)
    plans = dict(base.plans)
    plans[Route.N1] = replace(
        n1, primary=replace(n1.primary, effort=effort))
    with pytest.raises(RoutingError, match="not a claude effort"):
        RoutingConfig("claude", plans).validate()


def test_route_plan_keeps_session_and_read_only_flags_in_sync():
    base = RoutingConfig.default("claude").plan(Route.N1)
    isolated = base.with_session_mode(SessionMode.ISOLATED).with_read_only()
    assert all(spec.session_mode is SessionMode.ISOLATED for spec in isolated.attempts)
    assert all(spec.read_only for spec in isolated.attempts)


def _executor(results, persisted=None):
    from unittest.mock import MagicMock

    agent = MagicMock(provider="codex")
    agent.run.side_effect = list(results)
    repo = MagicMock()
    repo.head.return_value = "base"
    repo.is_clean.return_value = True
    repo.persist_turn.return_value = persisted or PersistenceResult(True)
    return TurnExecutor(agent, repo, MagicMock()), agent, repo


@pytest.mark.parametrize("applied", [0, 3])
def test_a_turn_is_one_model_call_whatever_it_writes(applied):
    """The reply that did the work goes out as is: no second rewrite."""
    executor, agent, repo = _executor(
        [RunResult(full_text="Noté, et bravo pour la régularité.")],
        PersistenceResult(True, applied_commands=applied))

    outcome = executor.execute("4242", "RPE 5, douleur 1",
                               plan=RoutingConfig.default("codex").plan(Route.N2))

    assert agent.run.call_count == 1
    assert outcome.model_calls == 1
    assert outcome.result.full_text == "Noté, et bravo pour la régularité."
    repo.persist_turn.assert_called_once()
    repo.discard_turn.assert_not_called()


def test_an_empty_reply_gets_one_continuation_in_the_same_turn_and_session():
    executor, agent, _ = _executor([
        RunResult(full_text=""),
        RunResult(full_text="Voilà ma réponse."),
    ])

    outcome = executor.execute("4242", "question",
                               plan=RoutingConfig.default("codex").plan(Route.N1))

    assert outcome.result.full_text == "Voilà ma réponse."
    assert outcome.model_calls == 2
    first, second = agent.run.call_args_list
    assert first.kwargs["turn_id"] == second.kwargs["turn_id"]
    assert second.kwargs["spec"].session_mode is SessionMode.CONTINUE
    assert "sans texte" in second.args[1]


def test_a_structured_initiative_is_never_continued_after_choosing_silence():
    executor, agent, _ = _executor([RunResult(full_text="", silent=True)])

    plan = (RoutingConfig.default("codex").plan(Route.N1)
            .with_session_mode(SessionMode.FORK).with_structured_reply())
    outcome = executor.execute("4242", "mot du matin", plan=plan)

    assert agent.run.call_count == 1
    assert outcome.result.silent


def test_refused_writes_discard_the_turn_and_its_session():
    executor, agent, repo = _executor(
        [RunResult(full_text="brouillon")], PersistenceResult(False))
    state = executor._state

    outcome = executor.execute("4242", "déplace",
                               plan=RoutingConfig.default("codex").plan(Route.N2))

    assert agent.run.call_count == 1
    assert not outcome.persistence.writes_applied
    repo.discard_turn.assert_called_once_with(outcome.turn_id)
    state.clear_session.assert_called_once_with("4242", "codex")
