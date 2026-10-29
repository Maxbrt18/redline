"""`python -m redline` starts the service."""

from redline.model import entrypoint
from redline.service.app import main

if __name__ == "__main__":
    entrypoint.run(main)
