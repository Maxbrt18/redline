"""The stream-json wire parser buffers until a terminal result."""

import json

from redline.service.claude import ClaudeCodeRunner


def assistant(*blocks) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def text(value: str) -> dict:
    return {"type": "text", "text": value}


def tool_use() -> dict:
    return {"type": "tool_use", "name": "Bash", "input": {}}


def result(value=None, is_error=False) -> str:
    return json.dumps({"type": "result", "result": value, "is_error": is_error})


def consume(lines):
    return ClaudeCodeRunner._consume(iter(lines))


def test_narration_before_action_is_dropped():
    state = consume([assistant(text("Je mets à jour le journal..."), tool_use(),
                               text("Séance encaissée."))])
    assert state.full == "Séance encaissée."


def test_narration_dropped_across_messages():
    state = consume([
        assistant(text("Je regarde Strava.")), assistant(tool_use()),
        assistant(text("Belle sortie hier.")),
    ])
    assert state.full == "Belle sortie hier."


def test_text_before_a_later_action_is_retractable():
    state = consume([
        assistant(text("Pas encore final.")),
        assistant(tool_use(), text("Nouveau texte")),
    ])
    assert state.full == "Nouveau texte"


def test_separator_between_assistant_messages():
    state = consume([assistant(text("Première.")), assistant(text("Deuxième."))])
    assert state.full == "Première.\n\nDeuxième."


def test_session_and_result_are_captured():
    state = consume([
        json.dumps({"type": "system", "session_id": "abc-123"}),
        assistant(text("Ok.")), result("Ok."),
    ])
    assert state.session == "abc-123"
    assert state.result_text == "Ok."
    assert state.is_error is False


def test_result_is_fallback_without_assistant_text():
    assert consume([result("SILENCE")]).full == "SILENCE"


def test_unreadable_lines_are_ignored():
    assert consume(["pas du json", "", assistant(text("Ça va ?"))]).full == "Ça va ?"


def test_the_model_that_answered_is_captured():
    state = consume([
        json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5-5"}),
        json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5",
                                                     "content": [text("Ok.")]}}),
        result("Ok."),
    ])
    assert state.model == "claude-opus-5-5"


def test_without_a_named_model_none_is_claimed():
    assert consume([assistant(text("Ok.")), result("Ok.")]).model is None
