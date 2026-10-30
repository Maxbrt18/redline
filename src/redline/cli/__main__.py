"""`python -m redline.cli <command>`, the same as the `redline` entry point."""

from redline.cli import main
from redline.model import entrypoint

entrypoint.exit_with(main())
