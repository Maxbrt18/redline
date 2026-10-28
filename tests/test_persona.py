"""Le prénom de l'athlète se rend à la sortie, jamais écrit dans le moteur."""

import re
import subprocess

import pytest
from conftest import ENGINE_ROOT

from redline.service import messenger, persona, prompts


@pytest.mark.parametrize("name, expected", [
    ("Marie", "la réponse de Marie, puis à Marie, que Marie lit"),
    ("Alex", "la réponse d'Alex, puis à Alex, qu'Alex lit"),
    ("Hugo", "la réponse d'Hugo, puis à Hugo, qu'Hugo lit"),
])
def test_the_name_replaces_the_token_and_french_elides(name, expected):
    text = (f"la réponse de {persona.TOKEN}, puis à {persona.TOKEN}, "
            f"que {persona.TOKEN} lit")
    assert persona.render(text, name) == expected


def test_the_style_prompt_renders_exactly_the_text_it_was_written_for():
    """Le gabarit ne change rien d'autre que le prénom : rendu avec un prénom sans
    élision, il redonne le texte d'origine, caractère par caractère."""
    rendered = persona.render(prompts.STYLE_PROMPT, "Marie")
    assert persona.TOKEN not in rendered
    assert rendered == prompts.STYLE_PROMPT.replace(persona.TOKEN, "Marie")
    assert rendered.startswith("Tu es Redline, coach course à pied de Marie sur Messenger.")


def test_both_adapters_hand_the_model_a_rendered_text(make_config):
    from redline.service.claude import ClaudeCodeRunner
    from redline.service.codex import CodexRunner
    config = make_config()
    name = config.instance.athlete_name
    for runner in (ClaudeCodeRunner.__new__(ClaudeCodeRunner), CodexRunner.__new__(CodexRunner)):
        runner._config = config
        assert runner._personal(f"invisible pour {persona.TOKEN}") == f"invisible pour {name}"
    command = ClaudeCodeRunner.__new__(ClaudeCodeRunner)
    command._config = config
    built = command._command(f"bonjour {persona.TOKEN}", None, turn_id=None)
    assert not any(persona.TOKEN in part for part in built if isinstance(part, str))


def test_nothing_reaches_the_phone_with_the_token(make_config):
    client = messenger.MessengerClient(make_config())
    payload = client._personal({"recipient": {"id": "1"}, "message": {
        "attachment": {"type": "template", "payload": {
            "template_type": "button", "text": f"Pour {persona.TOKEN}", "buttons": []}}}})
    assert payload["message"]["attachment"]["payload"]["text"] == "Pour Sam"
    assert client._personal({"recipient": {"id": "1"}, "message": {
        "text": f"Salut {persona.TOKEN}"}})["message"]["text"] == "Salut Sam"


def test_no_name_is_written_in_the_engine():
    """Le garde-fou : un prénom réintroduit en dur dans le code ou les gabarits se voit
    ici. Les tests, eux, nomment l'athlète fictif de leur instance."""
    tracked = subprocess.run(["git", "ls-files"], cwd=ENGINE_ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    offenders = []
    for relative in tracked:
        if relative.startswith("tests/") or relative == "README.md":
            continue
        path = ENGINE_ROOT / relative
        if path.suffix not in {".py", ".js", ".css", ".html", ".md", ".sh", ".toml"}:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bSam\b", line):
                offenders.append(f"{relative}:{number}")
    assert not offenders, "prénom écrit en dur : " + ", ".join(offenders)
