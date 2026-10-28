"""Reading-depth triage: classification parsing and profile routing."""

import ast
import inspect
import subprocess
import textwrap
from unittest.mock import patch

import pytest
from conftest import FakeAgent

from redline.agent import SessionMode, Triage
from redline.bot import CoachBot
from redline.claude import ClaudeCodeRunner
from redline.prompts import DEPTH_NOTES, EMOTION_NOTE

# ---------------------------------------------------------------- classify()

def _runner(make_config, store, **overrides):
    return ClaudeCodeRunner(make_config(agent_provider="claude", **overrides), store)


def _completed(stdout, returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


@pytest.mark.parametrize("raw,expected", [
    ("N0", "N0"),
    ("N3", "N3"),
    ("n2", "N2"),
    ("  N1\n", "N1"),
    ("Niveau : N3", "N3"),
    # The v2 format, and the v1 one it must keep tolerating (the small model drifts).
    ("N2 | - | Deux secondes, je regarde ta semaine.", "N2"),
    ("N1 | E | Raconte moi ça.", "N1"),
])
def test_classify_parses_the_level(make_config, store, raw, expected):
    runner = _runner(make_config, store)
    with patch("subprocess.run", return_value=_completed(raw)):
        assert runner.classify("peu importe").depth == expected


@pytest.mark.parametrize("stdout", ["", "je ne sais pas", "N9", "blah"])
def test_unparseable_answer_falls_back(make_config, store, stdout):
    runner = _runner(make_config, store)
    with patch("subprocess.run", return_value=_completed(stdout)):
        assert runner.classify("peu importe") == Triage()


@pytest.mark.parametrize("boom", [
    OSError("claude not found"),
    subprocess.TimeoutExpired(cmd="claude", timeout=30),
])
def test_triage_failure_never_blocks_a_reply(make_config, store, boom):
    runner = _runner(make_config, store)
    with patch("subprocess.run", side_effect=boom):
        assert runner.classify("peu importe") == Triage()


def test_triage_can_be_disabled(make_config, store):
    runner = _runner(make_config, store, triage_enabled=False)
    with patch("subprocess.run") as run:
        assert runner.classify("peu importe") == Triage()
    run.assert_not_called()


# ------------------------------------------------- the two extra fields (v2)

def test_the_emotional_flag_is_read(make_config, store):
    runner = _runner(make_config, store)
    with patch("subprocess.run", return_value=_completed("N1 | E | Raconte.")):
        assert runner.classify("j'ai plus envie").emotional is True


@pytest.mark.parametrize("raw", ["N1", "N1 | - | Deux secondes.", "N1 | x | Deux secondes."])
def test_absent_or_unknown_second_field_is_not_emotional(raw):
    assert ClaudeCodeRunner._parse_triage(raw).emotional is False


def test_the_holding_line_is_carried(make_config, store):
    runner = _runner(make_config, store)
    with patch("subprocess.run",
               return_value=_completed("N2 | - | Ah, le vélo à la place de samedi.")):
        assert runner.classify("je fais du vélo ?").holding == "Ah, le vélo à la place de samedi."


def test_a_level_without_a_holding_line_still_parses():
    """A malformed field must cost only that field. The depth is what buys the
    reading; losing it because the third field is missing would be the tail wagging
    the dog."""
    triage = ClaudeCodeRunner._parse_triage("N3")
    assert (triage.depth, triage.holding) == ("N3", None)


def test_an_overlong_holding_line_is_dropped():
    """The small model answering the question instead of announcing the wait: sending
    a triage-grade answer as a "hang on" line would be worse than the silence."""
    triage = ClaudeCodeRunner._parse_triage("N2 | - | " + "bla " * 100)
    assert triage.depth == "N2"
    assert triage.holding is None


def test_the_holding_line_cannot_move_the_rung():
    """Depth is read from the FIRST field only: a waiting line that happens to quote a
    level ("je regarde si c'est du N3") must not reclassify the message."""
    assert ClaudeCodeRunner._parse_triage("N1 | - | je regarde si c'est du N3").depth == "N1"


@pytest.mark.parametrize("raw,change,asked", [
    ("N2 | - | Deux secondes. | P | -", True, False),
    ("N1 | - | Je regarde. | - | D", False, True),
    ("N2 | - | Deux secondes. | P | D", True, True),
    # Une ligne au format d'avant les cinq champs ne réclame rien : le pire défaut
    # serait qu'un parseur trop souple envoie un programme sur un champ absent.
    ("N2 | - | Deux secondes. | P", True, False),
    ("N1", False, False),
])
def test_the_two_plan_flags_are_read_apart(raw, change, asked):
    triage = ClaudeCodeRunner._parse_triage(raw)
    assert (triage.plan_change, triage.plan_asked) == (change, asked)


def test_triage_uses_the_small_model(make_config, store):
    runner = _runner(make_config, store)
    with patch("subprocess.run", return_value=_completed("N1")) as run:
        runner.classify("salut")
    command = run.call_args[0][0]
    assert "--model" in command and command[command.index("--model") + 1] == "claude-haiku-5-5"


def test_triage_carries_the_low_effort_guardrail(make_config, store):
    runner = _runner(make_config, store)
    with patch("subprocess.run", return_value=_completed("N1")) as run:
        runner.classify("salut")
    command = run.call_args[0][0]
    assert command[command.index("--effort") + 1] == "low"


def test_triage_has_no_fallback_profile(make_config, store):
    """Un seul classifieur rapide : un échec retombe sur le défaut, sans second appel
    qui ajoutait jusqu'à une minute de « Vu » muet."""
    runner = _runner(make_config, store)
    with patch("subprocess.run", return_value=_completed("", returncode=1)) as run:
        assert runner.classify("salut") == Triage()
    assert run.call_count == 1


def test_triage_timeout_falls_back_to_the_default_at_once(make_config, store):
    runner = _runner(make_config, store)
    with patch("subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=10)) as run:
        assert runner.classify("je change la séance") == Triage()
    assert run.call_count == 1


# ------------------------------------------------------------------ routing

def _bot(make_bot, depth, emotional=False, holding=None, **config):
    agent = FakeAgent(depth=depth, emotional=emotional, holding=holding)
    return make_bot(agent=agent, **config), agent


@pytest.mark.parametrize("depth,model,effort", [
    ("N0", "gpt-6-luna", "medium"),
    ("N1", "gpt-6-luna", "high"),
    ("N2", "gpt-6-sol", "medium"),
    ("N3", "gpt-6-sol", "high"),
])
def test_depth_selects_the_declared_profile(make_bot, depth, model, effort):
    bot, agent = _bot(make_bot, depth)
    bot._respond("4242", "un message")
    assert agent.calls[0]["model"] == model
    assert agent.calls[0]["effort"] == effort
    assert agent.calls[0]["session_mode"] is SessionMode.CONTINUE


def test_the_reactive_path_passes_a_route_plan_instead_of_raw_settings():
    """Le bot transmet l'abstraction commune, jamais des paramètres épars."""
    reactif = (CoachBot._respond_turn, CoachBot._run_proactive_agent)
    for fonction in reactif:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fonction)))
        named = [keyword.arg for node in ast.walk(tree) if isinstance(node, ast.Call)
                 for keyword in node.keywords]
        assert "plan" in named, f"aucun plan de route dans {fonction.__name__}"


@pytest.mark.parametrize("depth", ["N0", "N1", "N2", "N3"])
def test_depth_note_is_prepended_to_the_prompt(make_bot, depth):
    bot, agent = _bot(make_bot, depth)
    bot._respond("4242", "un message")
    sent = agent.calls[0]["message"]
    assert DEPTH_NOTES[depth] in sent
    assert "un message" in sent


@pytest.mark.parametrize("depth", ["N0", "N1", "N2", "N3"])
def test_la_date_du_jour_ouvre_le_prompt_a_tous_les_niveaux(make_bot, depth):
    """« aujourd'hui repos, demain le renfo » arrive en N0 comme en N2, et c'est en N0
    que le coach lit le moins. L'ancre passe donc avant le niveau de lecture."""
    bot, agent = _bot(make_bot, depth)
    bot._respond("4242", "un message")

    assert agent.calls[0]["message"].startswith("[DATE DU JOUR,")


def _typing_kwargs(make_bot, depth, text, **triage):
    """Le signe de vie démarre avant le triage ; il reçoit ensuite rang et attente."""
    bot, _ = _bot(make_bot, depth, **triage)
    with patch("redline.bot.TypingIndicator") as typing:
        typing.return_value.__enter__ = lambda self: self
        typing.return_value.__exit__ = lambda self, *a: None
        bot._respond("4242", text)
    return typing.return_value.configure.call_args.kwargs


def _armed(make_bot, depth, text, **triage):
    return _typing_kwargs(make_bot, depth, text, **triage)["deep"]


def test_deep_question_arms_the_immediate_patience_line(make_bot):
    assert _armed(make_bot, "N3", "t'es sûr de toi là ?") is True


def test_an_engaging_judgement_arms_it_too(make_bot):
    """N2 is where being wrong costs the most -- "je fais du vélo à la place demain ?"
    changes the week's load -- and it used to be answered at the speed of a debrief.
    The patience line is what lets the coach go and read instead of answering on
    reflex: it is the cheap half of the model upgrade, and the half the diagnosis in
    corrections.md actually supports (2026-07-29)."""
    assert _armed(make_bot, "N2", "je décale ma séance à jeudi ?") is True


@pytest.mark.parametrize("depth", ["N0", "N1"])
def test_routine_messages_do_not(make_bot, depth):
    assert _armed(make_bot, depth, "j'ai fait 6 km ce matin") is False


def test_the_holding_line_reaches_the_typing_indicator(make_bot):
    kwargs = _typing_kwargs(make_bot, "N2", "je fais du vélo ?", holding="Ah, le vélo.")
    assert kwargs["holding"] == "Ah, le vélo."


# ----------------------------------------------------------- emotional route

def test_an_emotional_message_gets_its_note(make_bot):
    """L'accueil précède les lectures sans remplacer la demande concrète."""
    bot, agent = _bot(make_bot, "N2", emotional=True)
    bot._respond("4242", "je me demande si je vais y arriver")
    sent = agent.calls[0]["message"]
    assert sent.startswith(EMOTION_NOTE)
    assert DEPTH_NOTES["N2"] in sent


def test_an_emotional_message_is_not_answered_with_hang_on(make_bot):
    """"Attends, je vérifie deux trois trucs" is the wrong reply to someone saying he
    is discouraged. The rung is unchanged (the reading still happens), only the fast
    announcement is dropped."""
    assert _armed(make_bot, "N2", "j'ai plus envie en ce moment", emotional=True) is False


def test_emotional_correction_keeps_deeper_reading_and_replan_notes(make_bot):
    from redline.prompts import plan_change_note

    bot, agent = _bot(make_bot, "N3")
    agent._emotional = True
    agent._plan_change = True
    bot._respond("4242", "Tu te trompes, je doute et je décale ma séance à jeudi")

    sent = agent.calls[0]["message"]
    assert sent.startswith(EMOTION_NOTE)
    assert DEPTH_NOTES["N3"] in sent
    assert plan_change_note() in sent
    assert agent.calls[0]["model"] == "gpt-6-sol"


def test_a_factual_message_mentioning_pain_is_not_emotional(make_bot):
    """Guard on the criterion, not on the model: what makes E is that the state of
    mind IS the subject, not that a feeling is mentioned."""
    bot, agent = _bot(make_bot, "N1", emotional=False)
    bot._respond("4242", "j'ai eu un peu mal au genou mais c'est passé")
    assert not agent.calls[0]["message"].startswith(EMOTION_NOTE)


def test_the_n2_note_covers_any_change_of_plan_not_only_an_increase():
    """Le cas d'usage de Sam (« je fais un fractionné pas prévu ce soir ») est une
    séance AJOUTÉE : le protocole du 17/07 la liste, mais la note N2 ne déclenchait la
    charge de la preuve que « si c'est une hausse qu'il propose ». Substituer, déplacer
    ou allonger changent la charge de la semaine autant qu'une hausse assumée."""
    assert "Toute modification du plan" in DEPTH_NOTES["N2"]
    assert "charge de la preuve inversée" in DEPTH_NOTES["N2"]


def test_un_message_qui_touche_au_programme_le_dit_au_coach(make_bot):
    """Le 25/08, « P » était calculé à chaque message et n'atteignait jamais le coach.

    Il ne servait qu'à charger le programme, ce que N1 et N2 font déjà, et le seul texte
    du dépôt qui parlait de P était le prompt du classifieur. Résultat : Sam annonce
    trois changements, le tour se termine sur « ok », et la révision n'existe que deux
    tours plus tard, quand il demande lui-même si c'est consigné.
    """
    bot, agent = _bot(make_bot, "N2")
    agent._plan_change = True
    bot._respond("4242", "Finalement aujourd'hui repos, je fais le renfo demain")

    envoye = agent.calls[0]["message"]
    assert "CE MESSAGE TOUCHE AU PROGRAMME" in envoye
    assert "n'impose ni révision ni question automatique" in envoye


def test_un_message_ordinaire_ne_porte_pas_lobligation(make_bot):
    bot, agent = _bot(make_bot, "N2")
    bot._respond("4242", "c'était quoi déjà ma FC max")

    assert "CE MESSAGE TOUCHE AU PROGRAMME" not in agent.calls[0]["message"]


def test_un_tour_plan_change_sans_revision_laisse_une_trace(make_bot, caplog):
    """La signature exacte du 21h09 : le service la voit passer, il le dit."""
    bot, agent = _bot(make_bot, "N2")
    agent._plan_change = True

    with caplog.at_level("WARNING", logger="redline"):
        bot._respond("4242", "Finalement aujourd'hui repos")

    assert "terminé SANS nouvelle révision" in caplog.text
    assert "Finalement aujourd'hui repos" in caplog.text


def test_short_confirmation_is_classified_with_its_conversation_context(make_bot, monkeypatch):
    import json
    from unittest.mock import MagicMock

    from redline.agent import Triage

    bot, agent = _bot(make_bot, "N2")
    agent.classify = MagicMock(return_value=Triage("N2", plan_change=True))
    monkeypatch.setattr(bot._state, "relationship_context", lambda psid, **_: "Proposition : déplacer jeudi.")
    monkeypatch.setattr(bot._state, "reply_context", lambda ids: "Tu confirmes ce déplacement ?")

    bot._respond("4242", "ok", message_ids=("confirmation",))

    agent.classify.assert_called_once()
    payload = json.loads(agent.classify.call_args.args[0].split("\n")[-1])
    assert payload["message"] == "ok"
    assert payload["recent_context"] == "Proposition : déplacer jeudi."
    assert payload["reply_to"] == "Tu confirmes ce déplacement ?"
