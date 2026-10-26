"""Package entry point: ``python3 -m redline``.

The systemd unit exposes both the repository root and ``server/`` on PYTHONPATH.
"""

from .app import main

if __name__ == "__main__":
    main()
