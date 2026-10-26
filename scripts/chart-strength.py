#!/usr/bin/env python3
"""Strength session card -- the exercise list to follow while training.

Deliberately NOT a chart: this is a checklist you keep open on your phone between
sets, so legibility at arm's length beats any visual cleverness.

Input: SELECTION_SCHEMA in scripts/model/programme.py, from --input (stdin outside a
turn). What arrives is a SELECTION of the programme -- exercises, doses, title and footer rule all live in
`coaching/exercises/*.md` and `coaching/workouts/renfo-*.md`, and the morning run
picks and modulates rather than retyping nine exercises.

The `gate` field of that schema is the day's pain guardrail -- {zone?, pain_max, drop, say},
applied by the interactive page if the injury moved between the message and the moment
L'athlète opens the link. It is OPTIONAL, so omitting it raises nothing and simply leaves
the session unguarded: name it whenever the session carries impact or the pain trend
is not flat.

Two outputs from that one selection: the PNG, and a `.link` sidecar holding the exact
interactive snapshot. After the image is delivered, the service stores that snapshot
and turns its opaque capability into a button. Both views therefore cannot disagree.

Usage:  python3 scripts/chart-strength.py -o server/outbox/strength-2026-07-25.png < selection.json
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cards import cli, render, strength_card  # noqa: E402
from scripts.model import paths  # noqa: E402
from scripts.model.notes import NoteError  # noqa: E402
from scripts.model.schema import SchemaError  # noqa: E402

# The only cross-tree import in the repo: the card mints the signed link with the
# very code the server verifies it with, so the two can never disagree.
# `redline.strength` is a declared leaf (standard library only, imports nothing
# from its own package), which is what makes reaching into server/ safe here.
sys.path.insert(0, str(ROOT / "server"))
from redline import strength  # noqa: E402


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
        sys.exit(f"Séance non générée — {exc}")
    except render.CardOverflow as exc:
        sys.exit(f"Card non générée — {exc}")
    if not args.html:
        write_link(out, built)
    print(target)


if __name__ == "__main__":
    main()
