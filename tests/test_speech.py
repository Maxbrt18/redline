"""Speech to text: one contract, providers chosen by name, hints built from the instance."""

from __future__ import annotations

import pytest
import requests
from conftest import TEST_VAULT

from redline.integrations.speech import openai_api, providers
from redline.integrations.speech.contract import AudioClip, Hints
from redline.integrations.speech.openai_api import OpenAITranscriber
from redline.model import instance as instance_model
from redline.model.errors import ConfigError, ExternalError, InputError
from redline.service import transcription


class Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class Session:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        name, handle, media_type = kwargs["files"]["file"]
        self.calls.append({"url": url, "headers": kwargs["headers"], "data": kwargs["data"],
                           "file": (name, handle.read(), media_type)})
        if self.error is not None:
            raise self.error
        return self.response


HINTS = Hints(language="fr", context="Message vocal.", vocabulary=("VMA", "seuil"))


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "inbound-key-0.m4a"
    path.write_bytes(b"\x00\x00\x00\x18ftypM4A voice")
    return AudioClip(path, "audio/mp4")


def transcriber(session):
    return OpenAITranscriber("groq", base_url="https://api.example.test/v1/",
                             model="whisper-large-v3", api_key="key-123", session=session)


def test_the_openai_api_receives_the_audio_the_model_and_the_hints(clip):
    session = Session(Response(payload={"text": "  J'ai fait ma VMA.  "}))

    assert transcriber(session).transcribe(clip, HINTS) == "J'ai fait ma VMA."

    call = session.calls[0]
    assert call["url"] == "https://api.example.test/v1/audio/transcriptions"
    assert call["headers"] == {"Authorization": "Bearer key-123"}
    assert call["data"]["model"] == "whisper-large-v3"
    assert call["data"]["language"] == "fr"
    assert call["data"]["prompt"] == "Message vocal. VMA, seuil."
    assert call["file"] == ("inbound-key-0.m4a", b"\x00\x00\x00\x18ftypM4A voice", "audio/mp4")


@pytest.mark.parametrize("status", [400, 413, 415, 422])
def test_a_refused_audio_is_an_input_error_a_retry_would_not_change(clip, status):
    session = Session(Response(status, text="file must be one of the supported types"))
    with pytest.raises(InputError, match="supported types"):
        transcriber(session).transcribe(clip, HINTS)


@pytest.mark.parametrize("session", [
    Session(Response(429)),
    Session(Response(503)),
    Session(error=requests.ConnectionError("down")),
    Session(Response(payload={"words": []})),
    Session(Response(payload={"text": 42})),
], ids=["rate-limited", "unavailable", "unreachable", "no-text", "not-text"])
def test_an_unavailable_provider_is_an_external_error_worth_a_retry(clip, session):
    with pytest.raises(ExternalError):
        transcriber(session).transcribe(clip, HINTS)


def test_the_prompt_keeps_the_context_and_only_whole_words():
    words = tuple(f"mot{index:03d}" for index in range(500))
    prompt = openai_api.prompt(Hints("fr", "Message vocal.", words))

    assert prompt.startswith("Message vocal. mot000, mot001,")
    assert len(prompt) <= openai_api.PROMPT_MAX_CHARS
    assert prompt.endswith(".") and prompt.rsplit(" ", 1)[-1].startswith("mot")
    assert openai_api.prompt(Hints("fr", "Message vocal.")) == "Message vocal."


@pytest.mark.parametrize("name", sorted(providers.PROVIDERS))
def test_every_provider_builds_a_named_transcriber_on_its_default_model(name):
    built = providers.build(name, api_key="key")
    other = providers.build(name, api_key="key", model="other")
    assert isinstance(built, OpenAITranscriber) and isinstance(other, OpenAITranscriber)
    assert built.provider == name
    assert built.model == providers.PROVIDERS[name].default_model
    assert other.model == "other"


def test_an_unknown_provider_or_a_missing_key_stops_the_build():
    with pytest.raises(ConfigError, match="unknown transcription provider 'nope'"):
        providers.build("nope", api_key="key")
    with pytest.raises(ConfigError, match="needs an API key"):
        providers.build("groq", api_key="")


def test_the_hints_put_the_athletes_own_words_first_and_name_each_word_once():
    loaded = instance_model.load(TEST_VAULT)
    hints = transcription.hints(loaded)

    assert hints.language == "fr"
    assert hints.vocabulary[0] == "Sam"
    assert hints.vocabulary.index("Genou") < hints.vocabulary.index("VMA")
    assert hints.vocabulary.index("VMA") < hints.vocabulary.index("Sortie longue")
    folded = [word.casefold() for word in hints.vocabulary]
    assert len(folded) == len(set(folded))


def test_the_declared_vocabulary_comes_right_after_the_name():
    text = (TEST_VAULT / instance_model.FILENAME).read_text(encoding="utf-8")
    loaded = instance_model.parse(
        text + '\n[transcription]\nvocabulary = ["Trail des Crêtes", "col du Loup"]\n')

    assert transcription.hints(loaded).vocabulary[:3] == ("Sam", "Trail des Crêtes",
                                                          "col du Loup")


def test_no_provider_means_no_transcriber(make_config):
    assert transcription.build_transcriber(make_config()) is None
    built = transcription.build_transcriber(make_config(
        transcription_provider="openai", transcription_api_key="key"))
    assert built is not None and built.provider == "openai"
