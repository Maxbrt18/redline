"""Voice notes: the speech-to-text provider selected at startup, and what it is told
about the athlete before it listens."""

from __future__ import annotations

from babel import Locale

from redline.coach.fr import session_labels
from redline.coach.fr import transcription as wording
from redline.integrations.speech import providers
from redline.integrations.speech.contract import Hints, Transcriber
from redline.model import session_kinds
from redline.model.instance import Instance

from .config import Config


def build_transcriber(config: Config) -> Transcriber | None:
    """None when no provider is configured: voice notes are then refused like videos."""
    if not config.transcription_provider:
        return None
    return providers.build(config.transcription_provider,
                           api_key=config.transcription_api_key,
                           model=config.transcription_model)


def hints(instance: Instance) -> Hints:
    """The athlete's own words first, then the coach's jargon, then the session kinds:
    a provider that keeps only the head of the list keeps the rarest words."""
    words = [instance.athlete_name, *instance.vocabulary,
             *(injury.label for injury in instance.injuries),
             *(capability.label for capability in instance.capabilities),
             *wording.TERMS,
             *(session_labels.name(slug) for slug in session_kinds.PLANNED)]
    seen: set[str] = set()
    vocabulary = []
    for word in words:
        if word.casefold() not in seen:
            seen.add(word.casefold())
            vocabulary.append(word)
    return Hints(language=Locale.parse(instance.locale).language,
                 context=wording.CONTEXT, vocabulary=tuple(vocabulary))
