"""The `redline weather` command: see `redline.integrations.weather`."""

from redline.integrations.weather import main
from redline.model import entrypoint

if __name__ == "__main__":
    entrypoint.run(main)
