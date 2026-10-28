#!/usr/bin/env python3
"""Strength session card -- the exercise list to follow while training.

Deliberately NOT a chart: this is a checklist you keep open on your phone between
sets, so legibility at arm's length beats any visual cleverness.

Input: SELECTION_SCHEMA in src/redline/model/programme.py, from --input (stdin outside a
turn). What arrives is a SELECTION of the programme -- exercises, doses, title and
footer rule all live in `coaching/exercises/*.md` and `coaching/workouts/renfo-*.md`,
and the caller picks and modulates rather than retyping every exercise.

The `gate` field of that schema is the day's pain guardrail -- {zone?, pain_max, drop, say},
applied by the interactive page if the pain moved between the message and the moment
the athlete opens the link. It is OPTIONAL, so omitting it raises nothing and simply leaves
the session unguarded: name it whenever the session carries impact or the pain trend
is not flat.

Two outputs from that one selection: the PNG, and a `.link` sidecar holding the exact
interactive snapshot. After the image is delivered, the service stores that snapshot
and turns its opaque capability into a button. Both views therefore cannot disagree.

Usage:  python3 scripts/chart-strength.py -o strength-example.png < selection.json
"""

from pathlib import Path

from redline.cards import cli, render, strength_card
from redline.model import (
    entrypoint,
    paths,
)
from redline.model.errors import InputError
from redline.model.notes import NoteError
from redline.model.schema import SchemaError

# The only cross-tree import in the repo: the card mints the signed link with the
# very code the server verifies it with, so the two can never disagree.
# `redline.service.strength` is a declared leaf (standard library only, imports nothing
# from its own package), which is what makes reaching into the service safe here.
from redline.service import strength


def write_link(out: Path, built: dict) -> None:
    if not strength.sidecar_enabled():
        return
    strength.write_sidecar(
        out, strength_card.subtitle(built), strength_card.snapshot(built, root=paths.vault_root()))


def main() -> None:
    args = cli.parse_args(__doc__.splitlines()[0])

    out = Path(args.out)
    try:
        target, built = strength_card.render_card(
            cli.read_input(args.input), out, root=paths.vault_root(), html_only=args.html)
    except (NoteError, SchemaError) as exc:
        raise InputError(f"Séance non générée — {exc}") from exc
    except render.CardOverflow as exc:
        raise InputError(f"Card non générée — {exc}") from exc
    if not args.html:
        write_link(out, built)
    print(target)


if __name__ == "__main__":
    entrypoint.run(main)
