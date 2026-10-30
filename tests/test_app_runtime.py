"""The service runtime contract, checked before any external configuration."""

import pytest

from redline.service.app import require_supported_python


def test_python_312_is_supported():
    require_supported_python((3, 12, 0))


def test_python_older_than_312_is_rejected():
    with pytest.raises(SystemExit, match=r"Python 3\.12 or later"):
        require_supported_python((3, 11, 9))
