"""The `feasibility.py` command: see `redline.analysis.feasibility`."""

from redline.analysis.feasibility import main
from redline.model import entrypoint

if __name__ == "__main__":
    entrypoint.run(main)
