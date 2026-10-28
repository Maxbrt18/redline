"""The failures the engine raises on purpose, grouped by what the caller does next.

Every category also derives from the built-in it replaces (`ValueError` or
`RuntimeError`), so an existing `except ValueError` keeps catching what it caught
before. Programming errors stay plain built-ins and are never raised from here.

A module may keep a private subclass when one of its callers needs to tell it apart;
it never declares a new public category.
"""

from __future__ import annotations


class RedlineError(Exception):
    """Root of every failure the engine raises on purpose."""


class InputError(RedlineError, ValueError):
    """Data was refused: fix the input and try again."""


class ConfigError(RedlineError, ValueError):
    """The instance is misconfigured: fix `redline.toml`, `rulebook.toml` or the environment."""


class ExternalError(RedlineError, RuntimeError):
    """A third-party service failed or answered something unusable; a retry may succeed."""


class StorageError(RedlineError, RuntimeError):
    """A database operation failed; the message never carries connection secrets."""
