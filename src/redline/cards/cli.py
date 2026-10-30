"""The CLI head the four card scripts share, as `render.emit` is their shared tail.

Each script used to repeat the same two argparse arguments and the same hand-rolled
"JSON invalide" exit, and describe its input in prose that nothing enforced.
"""

from __future__ import annotations

import argparse
import json
import os

from redline.model import agent_input, paths
from redline.model.errors import InputError
from redline.model.schema import Schema, SchemaError


def output_path(raw: str) -> str:
    """`week-29.png` lands in the service's outbox, so the coach never spells where it
    lives; a path with a directory is taken as is."""
    if os.sep in raw:
        return raw
    paths.outbox_dir().mkdir(parents=True, exist_ok=True)
    return str(paths.outbox_dir() / raw)


def parse_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("-o", "--out", required=True, type=output_path,
                        help="the PNG to produce; a bare name puts it in the outbox")
    parser.add_argument("--html", action="store_true",
                        help="write the standalone HTML instead of rendering (design loop)")
    parser.add_argument("--input", help="the card JSON (default: stdin outside a turn)")
    return parser.parse_args()


def read_input(path: str | None) -> dict:
    """The raw payload, for the caller that validates it itself -- chart-strength
    fills `sid` from the PNG name before the program's own schema runs.

    A coach turn hands it over with `--input`, never with `<` (agent_input.py)."""
    try:
        return agent_input.read_json(path)
    except json.JSONDecodeError as exc:
        raise InputError(f"unreadable JSON: {exc}") from exc
    except agent_input.AgentInputError as exc:
        raise InputError(str(exc)) from exc


def read_input_json(schema: Schema, path: str | None) -> dict:
    """The card's input, validated against `schema` and completed with its defaults.

    Refusing is the point: a card built from a payload nobody checked is a card the
    athlete reads as true. Every message names the field and what was expected.
    """
    try:
        return schema.validate(read_input(path), schema.name)
    except SchemaError as exc:
        raise InputError(f"Card not rendered: {exc}") from exc
