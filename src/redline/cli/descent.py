"""The `descent.py` command: see `redline.analysis.descent`."""

from redline.analysis.descent import main
from redline.model import entrypoint

if __name__ == "__main__":
    entrypoint.run(main)
