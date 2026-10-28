#!/usr/bin/env python3
"""Real preflight of a candidate Claude Code binary, with the service identity.

Checks the version, makes each model routed by `redline.routing` answer and prints
the identifier its alias resolves to, then resumes with the candidate a session
created by the active binary: the coach resumes its conversations with `--resume`.

Finally, the candidate must let through, with the exact permissions of an autonomous
turn, the CLI call shape the prompt dictates (`--input` pointing to the turn file).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

PROMPT = "Réponds exactement OK. N'utilise aucun outil."
TIMEOUT = 180


def _ask(binary: str, prompt: str, model: str, cwd: str, *, resume: str | None = None) -> dict:
    from redline.service.claude import CONFINEMENT

    command = [binary, "-p", prompt, "--model", model, "--effort", "low",
               "--output-format", "json", *CONFINEMENT,
               "--permission-mode", "dontAsk", "--tools", ""]
    if resume:
        command += ["--resume", resume]
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True,
                            timeout=TIMEOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"{model}: code {result.returncode}")
    payload = json.loads(result.stdout)
    if payload.get("is_error") or not str(payload.get("result", "")).strip():
        raise RuntimeError(f"{model}: réponse vide ou en erreur")
    return payload


def _cli_call_allowed(binary: str, model: str, cwd: str) -> None:
    """The candidate runs the coach's CLI call instead of refusing it.

    Without `REDLINE_TURN_ID`, the launcher stops before any script (« aucun tour en
    cours »): its answer proves the command passed the permissions, without reading
    or writing anything in the database.
    """
    from redline.model import agent_input
    from redline.service.claude import AUTONOMOUS_PERMISSIONS

    turn = str(uuid.uuid4())
    path = agent_input.turn_path(turn)
    (Path(cwd) / path).parent.mkdir(parents=True, exist_ok=True)
    (Path(cwd) / path).write_text("{}", encoding="utf-8")
    cli = ("/usr/local/bin/redline-agent-cli week-plan.py preview "
           f"--week 2030-W01 --input {path}")
    prompt = (f"Lance exactement cette commande avec l'outil Bash, une seule fois, sans "
              f"la modifier : {cli}\nPuis réponds FIN.")
    env = {key: value for key, value in os.environ.items() if key != "REDLINE_TURN_ID"}
    result = subprocess.run(
        [binary, "-p", prompt, "--model", model, "--effort", "low",
         "--output-format", "stream-json", "--verbose", *AUTONOMOUS_PERMISSIONS],
        cwd=cwd, env=env, text=True, capture_output=True, timeout=TIMEOUT, check=False,
        stdin=subprocess.DEVNULL)
    calls: dict[str, str] = {}
    outputs: list[str] = []
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result" and event.get("permission_denials"):
            raise RuntimeError("appel de CLI refusé par les permissions")
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else ():
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                calls[block["id"]] = str((block.get("input") or {}).get("command", ""))
            elif block.get("type") == "tool_result" and "--input" in calls.get(
                    block.get("tool_use_id"), ""):
                body = block.get("content")
                outputs.append(body if isinstance(body, str) else json.dumps(body))
    if not outputs:
        raise RuntimeError("appel de CLI jamais lancé par le modèle")
    if not any("agent-cli:" in output for output in outputs):
        raise RuntimeError(f"appel de CLI non exécuté : {outputs[-1][:200]}")


def _resolved(alias: str, payload: dict) -> str:
    # Claude Code also bills its internal tasks (Haiku): keep the requested alias.
    models = sorted((payload.get("modelUsage") or {}).keys())
    return ", ".join([model for model in models if alias in model] or models) or "inconnu"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--previous")
    args = parser.parse_args()

    try:
        from redline.routing import default_models
    except Exception as exc:
        print(f"prévol Claude: routage illisible ({type(exc).__name__})", file=sys.stderr)
        return 1

    try:
        version = subprocess.run([args.binary, "--version"], text=True, capture_output=True,
                                 timeout=30, check=True).stdout.split()
        if not version or version[0] != args.version:
            raise RuntimeError(f"version {version[:1]} au lieu de {args.version}")
        with tempfile.TemporaryDirectory(prefix="redline-claude-smoke-") as cwd:
            models = sorted(default_models("claude"))
            for model in models:
                payload = _ask(args.binary, PROMPT, model, cwd)
                print(f"prévol Claude {args.version}: {model} → {_resolved(model, payload)}")
            # A session written by the active binary must stay resumable by the candidate.
            origin = _ask(args.previous or args.binary, PROMPT, models[0], cwd)
            _ask(args.binary, PROMPT, models[0], cwd, resume=origin["session_id"])
            print(f"prévol Claude {args.version}: reprise de session acceptée")
            _cli_call_allowed(args.binary, models[0], cwd)
            print(f"prévol Claude {args.version}: appel de CLI par --input accepté")
    except Exception as exc:
        print(f"prévol Claude {args.version}: échec ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
