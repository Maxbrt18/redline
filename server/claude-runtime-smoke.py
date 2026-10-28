#!/usr/bin/env python3
"""Prévol réel d'un binaire Claude Code candidat, avec l'identité du service.

Vérifie la version, fait répondre chaque modèle routé par `redline.routing` et
affiche l'identifiant que l'alias résout, puis reprend avec le candidat une session
créée par le binaire actif : le coach reprend ses conversations par `--resume`.

Enfin, le candidat doit laisser passer, avec les permissions exactes d'un tour
autonome, la forme d'appel de CLI que dicte le prompt (`--input` vers le fichier du
tour). La 2.1.286 a été promue le 09/10 sans ce contrôle ; la 2.1.287 refusait
`… < /tmp/x.json`, et le bilan W41 n'a jamais pu écrire W42.
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
    command = [binary, "-p", prompt, "--model", model, "--effort", "low",
               "--output-format", "json", "--permission-mode", "dontAsk", "--tools", ""]
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
    """Le candidat exécute l'appel de CLI du coach au lieu de le refuser.

    Sans `REDLINE_TURN_ID`, le lanceur s'arrête avant tout script (« aucun tour en
    cours ») : sa réponse prouve que la commande a passé les permissions, sans rien
    lire ni écrire en base.
    """
    from redline.model import agent_input
    from redline.service.claude import AUTONOMOUS_PERMISSIONS

    turn = str(uuid.uuid4())
    path = agent_input.turn_path(turn)
    (Path(cwd) / path).parent.mkdir(parents=True, exist_ok=True)
    (Path(cwd) / path).write_text("{}", encoding="utf-8")
    cli = ("/usr/local/bin/redline-agent-cli week-plan.py preview "
           f"--week 2026-W01 --input {path}")
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
    # Claude Code facture aussi ses tâches internes (Haiku) : on garde l'alias demandé.
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
            # Une session écrite par l'actif doit rester reprenable par le candidat.
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
