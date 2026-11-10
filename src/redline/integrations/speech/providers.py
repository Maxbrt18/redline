"""The speech-to-text providers an instance can choose from, by name.

Switching provider is a configuration change: `TRANSCRIPTION_PROVIDER`, its key, and
optionally its model. Adding one is an entry in `PROVIDERS`, plus a module next to
`openai_api.py` when its API is not one already served.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from redline.model.errors import ConfigError

from .contract import Transcriber
from .openai_api import OpenAITranscriber


@dataclass(frozen=True)
class Provider:
    #: The model used when the configuration names none.
    default_model: str
    #: Builds the client from a model and an API key.
    build: Callable[[str, str], Transcriber]


def _openai_api(name: str, base_url: str) -> Callable[[str, str], Transcriber]:
    def build(model: str, api_key: str) -> Transcriber:
        return OpenAITranscriber(name, base_url=base_url, model=model, api_key=api_key)
    return build


PROVIDERS = {
    "groq": Provider("whisper-large-v3",
                     _openai_api("groq", "https://api.groq.com/openai/v1")),
    "openai": Provider("gpt-4o-transcribe",
                       _openai_api("openai", "https://api.openai.com/v1")),
}


def build(name: str, *, api_key: str, model: str = "") -> Transcriber:
    """The client of provider `name`, on `model` or its default."""
    provider = PROVIDERS.get(name)
    if provider is None:
        raise ConfigError(f"unknown transcription provider {name!r} "
                          f"(expected one of {', '.join(PROVIDERS)})")
    if not api_key:
        raise ConfigError(f"transcription provider {name!r} needs an API key")
    return provider.build(model or provider.default_model, api_key)
