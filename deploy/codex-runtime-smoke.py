#!/usr/bin/env python3
"""Real preflight, without a shell, of the Codex models Redline requires."""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--cwd", default="/home/ubuntu/redline")
    args = parser.parse_args()

    try:
        from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox
        from openai_codex.types import ReasoningEffort

        from redline.routing import default_models
    except Exception as exc:
        print(f"prévol Codex: import impossible ({type(exc).__name__})", file=sys.stderr)
        return 1

    codex = None
    try:
        config = CodexConfig(
            cwd=args.cwd,
            config_overrides=("features.shell_tool=false",),
            env={"PATH": os.environ.get("PATH", "")},
        )
        codex = Codex(config=config)
        for model in sorted(default_models("codex")):
            thread = codex.thread_start(
                ephemeral=True,
                approval_mode=ApprovalMode.deny_all,
                cwd=args.cwd,
                developer_instructions=None,
                model=model,
                sandbox=Sandbox.workspace_write,
            )
            result = thread.turn(
                "Réponds exactement OK. N'utilise aucun outil.",
                approval_mode=ApprovalMode.deny_all,
                cwd=args.cwd,
                effort=ReasoningEffort("low"),
                sandbox=Sandbox.workspace_write,
            ).run()
            response = getattr(result, "final_response", "")
            if not isinstance(response, str) or not response.strip():
                raise RuntimeError(f"réponse vide pour {model}")
            print(f"prévol Codex {args.version}: {model} accepté")
    except Exception as exc:
        print(f"prévol Codex {args.version}: échec ({type(exc).__name__})", file=sys.stderr)
        return 1
    finally:
        if codex is not None:
            codex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
