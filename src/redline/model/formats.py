"""The text formats schemas, routes and commands validate, declared once."""

from __future__ import annotations

#: A persisted identifier: an English slug of at most 40 characters.
SLUG = r"[a-z0-9][a-z0-9-]{0,39}"

#: The id of a planned session or a logical key: a slug without a length limit.
KEY = r"[a-z0-9][a-z0-9-]+"

#: A calendar day, YYYY-MM-DD.
DATE = r"20\d{2}-\d{2}-\d{2}"

#: A local wall-clock time, `07:30`.
TIME = r"(?:[01]\d|2[0-3]):[0-5]\d"
