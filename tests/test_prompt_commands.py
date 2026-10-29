"""Every command named in a prompt must exist and accept what it is told.

A prompt that cites a wrong command does not break at deployment: it breaks later,
mid-conversation. A debrief trigger that pipes into `redline descent` is outside the
single authorised launcher, so it is refused and disguised as an access outage. A test
scenario calling `fact-write daily --day` when the option is `--date` is the same
kind of fault.

Both are invisible to unit tests: prompts are strings, nobody executes them. Here they
are read as what they are -- ORDERS -- and each is checked to be executable: the script
exists, its `--help` passes, and the cited subcommands and options really appear in it.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess

import pytest
from conftest import ENGINE_ROOT, cli_command

from redline.service import prompts

# Commands live in backtick `spans`. The prose is not parsed: only what the prompt
# itself presents as a command is read.
SPAN = re.compile(r"`([^`]+)`")
SCRIPT = re.compile(
    r"/usr/local/bin/redline-agent-cli\s+([a-z][a-z-]*)((?:\s+[a-z][a-z-]*)*)")


def _prompts() -> dict[str, str]:
    """All the texts injected into the coach, gathered under a readable name."""
    textes = {
        "STYLE_PROMPT": prompts.STYLE_PROMPT,
        "TRIAGE_RULES": prompts.TRIAGE_RULES,
        "EMOTION_NOTE": prompts.EMOTION_NOTE,
        "STRENGTH_CARD_POINTER": prompts.STRENGTH_CARD_POINTER,
        "STRENGTH_REPORT_NOTE": prompts.STRENGTH_REPORT_NOTE,
        "stale_plan_note": prompts.stale_plan_note("2026-W33"),
        # These cite commands, so they enter here like the others.
        "canonical_plan_note": prompts.canonical_plan_note(""),
        "active_revision_note": prompts.active_revision_note(""),
        "missing_cells_note": prompts.missing_cells_note(
            [{"date": "2026-08-18", "type": "easy", "missing": ["rpe", "dminus"]}],
            [{"date": "2026-08-12", "type": "easy", "missing": ["enjoyment"]}]),
    }
    textes.update({f"DEPTH_NOTES[{k}]": v for k, v in prompts.DEPTH_NOTES.items()})
    textes.update({f"PROACTIVE_TRIGGERS[{k}]": v.replace("{detail}", "42")
                   for k, v in prompts.PROACTIVE_TRIGGERS.items()})
    return textes


def _commandes() -> list[tuple[str, str, str, tuple[str, ...]]]:
    """(origin, script, subcommands, options) for each cited command.

    Splitting on pipes matters: without it, the `--dplus` of `descent` would be
    attributed to `strava` in a debrief pipe, and the test would blame the wrong
    script for ignoring an option it never had.
    """
    trouvees = []
    for origine, texte in _prompts().items():
        plat = " ".join(texte.split())
        for span in SPAN.findall(plat):
            for segment in span.split("|"):
                correspondance = SCRIPT.search(segment)
                if not correspondance:
                    continue
                script = correspondance.group(1)
                suite = correspondance.group(2)
                options = tuple(sorted(set(re.findall(r"(--[a-z][a-z-]+)", segment))))
                trouvees.append((origine, script, suite.strip(), options))
    return trouvees


COMMANDES = _commandes()


def test_les_prompts_citent_bien_des_commandes():
    """Guard of the guard: if the extraction breaks, the whole file would pass green
    while checking nothing."""
    assert len(COMMANDES) >= 10
    scripts = {script for _, script, _, _ in COMMANDES}
    assert "strava" in scripts
    assert "week-plan" in scripts


@pytest.mark.parametrize("origine,script,suite,options", COMMANDES,
                         ids=[f"{o}:{s}" for o, s, _, _ in COMMANDES])
def test_chaque_commande_citee_est_executable(origine, script, suite, options):
    commande = cli_command(script.removeprefix("scripts/").removesuffix(".py"))
    assert importlib.util.find_spec(commande[-1]), f"{origine} cite {script}, qui n'existe pas"

    aide = subprocess.run([*commande, "--help"], cwd=ENGINE_ROOT,
                          capture_output=True, text=True, check=False)
    assert aide.returncode == 0, (
        f"{origine} cite {script}, dont --help échoue : {aide.stderr[:200]}")

    texte_aide = aide.stdout + aide.stderr
    for sous_commande in suite.split():
        # `show/check` and the like are written out in full in the prompts; a
        # subcommand absent from the help is a command the coach cannot run.
        assert sous_commande in texte_aide, (
            f"{origine} cite `{script} {sous_commande}`, absent de son aide")

    for option in options:
        detaillee = subprocess.run(
            [*commande, *suite.split(), "--help"], cwd=ENGINE_ROOT,
            capture_output=True, text=True, check=False)
        aide_complete = texte_aide + detaillee.stdout + detaillee.stderr
        assert option in aide_complete, (
            f"{origine} cite `{option}` pour {script}, qui ne la connaît pas")


def test_toute_commande_passe_par_le_lanceur_autorise():
    """The prompt and ``dontAsk`` must describe exactly the same path.

    If the doctrine says ``redline ledger-add`` while Bash only allows
    ``redline-agent-cli``, the refusal gets reported as a save outage. Every mention of a
    business CLI is therefore normalised, not only the examples that carry options.
    """
    fautes = []
    for origine, texte in _prompts().items():
        plat = " ".join(texte.split())
        for span in SPAN.findall(plat):
            for segment in span.split("|"):
                if re.match(r"(?:python3? )?redline [a-z]", segment.strip()) \
                        or (re.search(r"\bscripts/|\b[a-z-]+\.py\b", segment) \
                        and "coaching/" not in segment):
                    fautes.append(f"{origine} : `{span[:90]}`")
    assert not fautes, "commandes hors lanceur :\n  " + "\n  ".join(fautes)


def test_toute_commande_citee_est_dans_la_politique_du_lanceur():
    autorisees = {
        line for line in (ENGINE_ROOT / "deploy" / "agent-cli-tools.txt").read_text().splitlines()
        if line and not line.startswith("#")
    }
    citees = {script.removeprefix("scripts/") for _, script, _, _ in COMMANDES}
    assert citees <= autorisees
