"""Le contrat de runtime du service, vérifié avant toute configuration externe."""

import pytest

from redline.app import require_supported_python


def test_python_312_is_supported():
    require_supported_python((3, 12, 0))


def test_python_older_than_312_is_rejected():
    with pytest.raises(SystemExit, match=r"Python 3\.12 ou plus récent"):
        require_supported_python((3, 11, 9))
