"""The `redline signals` command: see `redline.analysis.signals`."""

from redline.analysis.signals import main
from redline.model import entrypoint

if __name__ == "__main__":
    entrypoint.run(main)
