"""The one place where a command-line failure becomes an exit code.

A CLI's `main` returns its exit code or raises a `RedlineError`; `run` prints the
message on stderr and exits with 1, exactly as `raise SystemExit("message")` did.
Anything else is a bug and keeps its traceback.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import NoReturn

from .errors import RedlineError

FAILURE = 1


def run(main: Callable[[], int | None]) -> NoReturn:
    try:
        code = main()
    except RedlineError as exc:
        print(exc, file=sys.stderr)
        code = FAILURE
    raise SystemExit(code)
