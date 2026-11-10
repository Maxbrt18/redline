"""What the service asks of a speech-to-text provider, whatever its API.

The service hands a provider a local audio file and typed hints, and gets back the
words spoken. Each provider turns the hints into what its API understands (a prompt, a
list of key terms); transport details stay in the provider modules, and `providers.py`
picks one by name at startup.

A provider raises `InputError` when it refuses the audio itself, since a retry would
get the same answer, and `ExternalError` when it failed or is unavailable, since a
retry may succeed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AudioClip:
    path: Path
    #: The container read from the bytes (`audio/mp4`, `audio/mpeg`…), not the label
    #: the file was served under.
    media_type: str


@dataclass(frozen=True)
class Hints:
    #: ISO 639-1 code of the spoken language, e.g. `fr`.
    language: str
    #: One sentence, in that language, saying who speaks and about what.
    context: str
    #: Words the speech is likely to contain that a generic model would misspell:
    #: jargon, names, places. The most specific first: a provider that can take only
    #: some keeps the head of the list.
    vocabulary: tuple[str, ...] = ()


class Transcriber(Protocol):
    #: The registry name of the provider (`groq`, `openai`…), for logs.
    provider: str

    def transcribe(self, clip: AudioClip, hints: Hints) -> str:
        """The words spoken in `clip`, empty when nothing intelligible was said."""
        ...
