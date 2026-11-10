"""The OpenAI transcription API (`POST <base>/audio/transcriptions`), which several
providers serve under the same shape: Groq, OpenAI.

The hints become Whisper's `prompt`: the context sentence, then the vocabulary. The model
reads the prompt as the text that came before the audio, so the words it lists are
spelled the way they are written there.
"""

from __future__ import annotations

import requests

from redline.model.errors import ExternalError, InputError

from .contract import AudioClip, Hints

#: Whisper reads at most 224 tokens of prompt and silently keeps only the end of a
#: longer one, which would drop the context sentence first.
PROMPT_MAX_CHARS = 600
#: Connect, then read: a minute of audio takes a few seconds; the margin covers a queue.
TIMEOUT = (5, 120)
#: The provider refused the request itself: the same audio would be refused again.
REFUSED_STATUSES = frozenset({400, 413, 415, 422})
#: How much of a refusal's body reaches the operator's log.
DETAIL_MAX_CHARS = 300


def prompt(hints: Hints) -> str:
    """The context sentence, then as many whole vocabulary words as fit."""
    text = hints.context
    words: list[str] = []
    for word in hints.vocabulary:
        candidate = f"{text} {', '.join([*words, word])}."
        if len(candidate) > PROMPT_MAX_CHARS:
            break
        words.append(word)
    return f"{text} {', '.join(words)}." if words else text


class OpenAITranscriber:
    """One provider of the OpenAI transcription API, named by `provider`."""

    def __init__(self, provider: str, *, base_url: str, model: str, api_key: str,
                 session: requests.Session | None = None) -> None:
        self.provider = provider
        self.model = model
        self._url = base_url.rstrip("/") + "/audio/transcriptions"
        self._api_key = api_key
        self._session = session or requests.Session()

    def transcribe(self, clip: AudioClip, hints: Hints) -> str:
        data = {"model": self.model, "language": hints.language, "prompt": prompt(hints),
                "response_format": "json", "temperature": "0"}
        try:
            with clip.path.open("rb") as handle:
                response = self._session.post(
                    self._url, headers={"Authorization": f"Bearer {self._api_key}"},
                    data=data, files={"file": (clip.path.name, handle, clip.media_type)},
                    timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise ExternalError(
                f"{self.provider} transcription unreachable: {type(exc).__name__}") from exc
        if response.status_code in REFUSED_STATUSES:
            raise InputError(f"{self.provider} refused the audio (HTTP {response.status_code}): "
                             f"{response.text[:DETAIL_MAX_CHARS]}")
        if response.status_code != 200:
            raise ExternalError(
                f"{self.provider} transcription failed (HTTP {response.status_code})")
        try:
            text = response.json()["text"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ExternalError(f"{self.provider} answered without a transcript") from exc
        if not isinstance(text, str):
            raise ExternalError(f"{self.provider} answered a transcript that is not text")
        return text.strip()
