"""The `redline ledger` command: see `redline.analysis.ledger`."""

from redline.analysis.ledger import main
from redline.model import entrypoint

if __name__ == "__main__":
    entrypoint.run(main)
