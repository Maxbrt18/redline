"""The `redline strava` command: see `redline.integrations.strava`."""

from redline.integrations.strava import main
from redline.model import entrypoint

if __name__ == "__main__":
    entrypoint.run(main)
