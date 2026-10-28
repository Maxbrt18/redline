"""The systemd startup barrier retries transient HTTP readiness failures."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import SERVER_DIR

WAIT_READY = SERVER_DIR / "wait-ready.sh"


def test_wait_ready_retries_connection_refused_and_http_503(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    counter = tmp_path / "curl-count"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
count=0
[[ ! -f "$CURL_COUNTER" ]] || count=$(<"$CURL_COUNTER")
count=$((count + 1))
printf '%s' "$count" > "$CURL_COUNTER"
IFS=, read -r -a responses <<< "$CURL_RESPONSES"
index=$((count - 1))
(( index < ${#responses[@]} )) || index=$((${#responses[@]} - 1))
printf '%s' "${responses[index]}"
[[ "${responses[index]}" != 000 ]]
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "CURL_COUNTER": str(counter),
        "CURL_RESPONSES": "000,503,200",
    })

    result = subprocess.run(
        [str(WAIT_READY), "10"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert "prêt" in result.stdout
    assert counter.read_text(encoding="utf-8") == "3"
