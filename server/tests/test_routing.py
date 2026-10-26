"""Politique unique de routage et fallback borné."""

from dataclasses import replace

import pytest

from redline.agent import FailureKind, RunResult, SessionMode
from redline.routing import Route, RoutePlan, RoutingConfig
from redline.turns import TurnExecutor
from redline.vault import PersistenceResult


@pytest.mark.parametrize("route", [Route.TRIAGE, Route.N0, Route.N1, Route.N2,
                                    Route.N3, Route.DEEP, Route.HEAVY])
def test_codex_defaults_match_the_canonical_matrix(route):
    config = RoutingConfig.default("codex")
    plan = config.plan(route)
    assert plan.primary.model != "gpt-6-sol" or plan.primary.effort in {"medium", "high"}
    assert all(
        spec.model in {"gpt-6-luna", "gpt-6-sol"}
        and not (spec.model == "gpt-6-sol" and spec.effort in {"max", "ultra"})
        for spec in plan.attempts
    )


@pytest.mark.parametrize("route", list(Route))
@pytest.mark.parametrize("suffix", ["MODEL", "FALLBACK_MODEL"])
@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6-astra-2026-09-01", "unknown"])
def test_forbidden_model_cannot_return_through_environment(monkeypatch, route, suffix, model):
    monkeypatch.setenv(f"AGENT_{route.value}_{suffix}", model)
    refused = ("pas de repli" if route is Route.TRIAGE and suffix == "FALLBACK_MODEL"
               else "seules Luna et Sol")
    with pytest.raises(SystemExit, match=refused):
        RoutingConfig.from_env(
            "codex", agent_timeout=600, agent_timeout_deep=1800,
            agent_timeout_heavy=5400, triage_timeout=30)


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


def test_a_route_can_be_overridden_without_touching_the_other_routes(monkeypatch):
    monkeypatch.setenv("AGENT_N1_MODEL", "gpt-6-sol")
    monkeypatch.setenv("AGENT_N1_EFFORT", "high")
    config = RoutingConfig.from_env(
        "codex", agent_timeout=600, agent_timeout_deep=1800,
        agent_timeout_heavy=5400, triage_timeout=30)
    assert config.plan(Route.N1).primary.model == "gpt-6-sol"
    assert config.plan(Route.N2).primary.model == "gpt-6-sol"


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
    """La frontière reste effective quand Codex tourne en danger-full-access."""
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


def test_route_validation_rejects_sol_max_in_a_manual_config():
    base = RoutingConfig.default("codex")
    n3 = base.plan(Route.N3)
    forbidden = replace(n3.primary, model="gpt-6-sol", effort="max")
    plans = dict(base.plans)
    plans[Route.N3] = RoutePlan(Route.N3, forbidden, n3.fallbacks)
    with pytest.raises(SystemExit, match="Sol Max"):
        RoutingConfig("codex", plans).validate()


@pytest.mark.parametrize("effort", ["none", "minimal", "ultra"])
def test_route_validation_rejects_efforts_unknown_to_claude(effort):
    base = RoutingConfig.default("claude")
    n1 = base.plan(Route.N1)
    plans = dict(base.plans)
    plans[Route.N1] = replace(
        n1, primary=replace(n1.primary, effort=effort))
    with pytest.raises(SystemExit, match="invalid pour claude"):
        RoutingConfig("claude", plans).validate()


def test_route_validation_rejects_ultra_for_luna():
    base = RoutingConfig.default("codex")
    n1 = base.plan(Route.N1)
    plans = dict(base.plans)
    plans[Route.N1] = replace(
        n1, primary=replace(n1.primary, effort="ultra"))
    with pytest.raises(SystemExit, match=r"gpt-6-luna"):
        RoutingConfig("codex", plans).validate()



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
    """La réponse qui a travaillé part telle quelle : aucune seconde rédaction."""
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
