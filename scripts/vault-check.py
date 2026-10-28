#!/usr/bin/env python3
"""Vault invariants -- turns "the audit" from a session into a command.

Ran at every weekly review (and on demand). The point is that structural drift gets
caught the week it appears, when it costs two minutes, instead of accumulating until
someone notices the vault has 7 copies of the same workout.

Each check maps to something that actually went wrong on 2026-07-17 or 2026-07-21:
duplication, stale caches, lying `maj` dates, private data, resurrected files.

Usage:  python3 scripts/vault-check.py            # from the vault root
        python3 scripts/vault-check.py --quiet    # only failures
Exit code 0 if everything passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model import (  # noqa: E402
    claims,
    correction_replay,
    entrypoint,
    instance,
    ledger,
    vault_layout,
)
from scripts.model import decisions as decision_log  # noqa: E402

# The programme layer needs PyYAML to read a note's frontmatter, which a bare vault
# checkout on the Mac may not have. `model.ledger` above has no dependency at all:
# the ledger stays checkable everywhere, which is the point of it being typed.
try:
    from scripts import ledger as ledger_gauge  # scripts/ledger.py : la jauge, pas le format
    from scripts.model import hot_cache, weekly_plan
    from scripts.model import startup_budget as budget
    from scripts.model.capabilities import load as load_capabilities
    from scripts.model.notes import NoteError
    from scripts.model.programme import load as load_programme
    from scripts.model.strength_tables import refresh
    from scripts.model.views import ViewDrift
except ImportError as exc:
    MODEL_UNAVAILABLE = str(exc)
else:
    MODEL_UNAVAILABLE = ""

SKIP_DIRS = {".git", ".obsidian", ".pytest_cache", ".venv", "inbox", "sources",
             "node_modules", "__pycache__", "server", "scripts", "templates"}

CAPABILITIES_DIR = "coaching/capabilities"
CAPABILITY_STALE_DAYS = 14     # a capability score not re-read in two weeks is stale
DECISIONS = str(vault_layout.DECISIONS)
CORRECTIONS = str(vault_layout.CORRECTIONS)
# What counts as a CARRIER for a ⭐ meta-rule: something that runs, or something the
# coach reads without choosing to. A fiche, a concept or `decisions.md` are read on
# demand at N2/N3 only -- citing one is how a rule goes back to sleep.
# `docs/vault-maintenance.md` counts because CLAUDE.md binds it to a TRIGGER ("à ne lire
# qu'au moment d'écrire une note, d'ingérer une source, de coder ou de commiter") -- which
# is exactly when a rule about HOW TO WRITE has to fire. It is not a general escape hatch:
# a rule that governs how the coach ANSWERS has no business there.
CARRIER = re.compile(r"scripts/|server/|prompts\.py|STYLE_PROMPT|vault-check|"
                     r"CLAUDE\.md|vault-maintenance|`gates`")
# The convention is `⭐ **libellé**`, not the bare character: a line that MENTIONS the
# marker ("le ⭐ a été retiré, c'était une observation") announces no rule at all, and
# matching on the character alone made the check fire on its own documentation.
STAR_RULE = re.compile(r"⭐\s*\*\*")
CARRIER_RULE_START = dt.date(2026, 8, 8)
# Le budget de lecture au démarrage (fichiers, plafonds, total) est déclaré une fois,
# dans model/startup_budget.py -- il n'a pas de seconde copie ici.

# Files retired by the 2026-07-21 restructure. If one comes back, a rule was forgotten.
RETIRED = ["athlete/journal/pain-log.md", "coaching/fitness-status.md",
           "coaching/load-gauge.md", "coaching/routines.md", "athlete/ledger.md",
           "ai/threads.md", "ai/proactive-log.md", "coaching/echeances.md",
           "coaching/race-capabilities.md"]

# A decision quotes the data that justified it, and decisions are append-only: a
# citation frozen in time is not a second owner.
CITES_EVIDENCE = {"coaching/decisions.md"}

# A REGRESSION GUARD on leaks already found and cleaned, not a privacy guarantee: a
# blocklist cannot catch a NEW one, and the report label says so. Word-boundary
# matched, because "SECRET" must not hit "secrètement".
FORBIDDEN = [r"\bSECRET\b", r"diagnostic de [a-zé]+ite\b"]

# Chiffres qui ont été ÉCRITS COMME DES FAITS sans avoir jamais été mesurés, retirés
# après correction de l'athlète. Distinct de FORBIDDEN (fuites de vie privée) : ici le
# risque n'est pas la divulgation, c'est la RÉAPPARITION — une valeur inventée qui a
# essaimé dans cinq fichiers reviendra par copie si rien ne la retient.
#   (no entry yet: add the regex of any number that was invented rather than measured)
UNMEASURED: list[str] = []

# Bounded to the ledger's Note column, the one place where a capitalised word is
# never a cited author: anywhere else this would fire on every cited researcher
# and a check that cries wolf gets ignored. The alternation is spelled in
# both cases rather than re.I, so the NAME still has to be capitalised.
COMPANION = re.compile(
    r"\b(?:[Aa]vec|[Pp]our|[Cc]hez|[Ee]t)\s+([A-ZÉÈÀÂÎÔÛ][\wÉÈÊÀÂÎÔÛéèêàâîôûç-]*)")
NOT_A_PERSON = {
    "Strava", "Paris", "SL", "EF", "FC", "RE", "D", "J1", "J2", "J3", "TargetRace",
    "Récup", "Sac", "Test", "Plafond", "Terrain", "Descente", "Dont", "Zéro", "Très",
    "Lièvre", "Canari", "Note", "GPX", "CMJ", "VMA",
    "RDL", "D-", "PAR",
}

CANONICAL_TYPES = {
    "ai", "bilan", "capacite", "concept", "decision", "exercice", "gabarit",
    "index", "profil", "seance", "seance-type", "épisode",
}
OPERATIONAL_SURFACES = ("ai/hot-cache.md", "coaching/current-plan.md")
OLD_PAIN_CEILING = re.compile(
    r"(?:douleur[^\n]{0,100}(?:≤|<=)\s*2\s*-\s*3\s*/\s*10|"
    r"(?:≤|<=)\s*2\s*-\s*3\s*/\s*10[^\n]{0,100}douleur)", re.IGNORECASE)
NUMERIC_WAKE = re.compile(
    r"\b(?:réveil|reveil)[^.!?;\n]{0,40}\b\d(?:[,.]\d)?\s*/\s*10", re.IGNORECASE)

WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
FENCED_CODE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")
# A copied external fact carries its date + shelf life: "2026-07-08 · TTL 90 j" (R13).
TTL = re.compile(r"(\d{4}-\d{2}-\d{2})\s*·\s*TTL\s*(\d+)\s*j")
ALIASES = re.compile(r"^aliases:\s*\[(.*?)\]", re.MULTILINE)
FRONTMATTER = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
MAJ = re.compile(r"^maj:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)
# Le motif appartient au registre typé : le `19\d{9}` codé ici s'éteindra tout seul le
# jour où Strava passera aux ids en 20…, et ce contrôle rapportera « aucun doublon »
# parce qu'il n'aura plus rien trouvé du tout (2026-07-29).
STRAVA_ID = re.compile(rf"\b{ledger.STRAVA_ID_PATTERN}\b")
DECISION_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})", re.MULTILINE)
# Documentation placeholders, not real links. Matched EXACTLY: matching a PREFIX
# like `ep-` would silently excuse every broken link to the 78 notes of episodes/.
PLACEHOLDERS = {"Seuil", "Lactate", "Travail au seuil", "concepts", "Concept", "note"}


def _is_placeholder(name: str) -> bool:
    return name in PLACEHOLDERS or "…" in name or "<" in name or name == "..."


def _without_code(text: str) -> str:
    """The note with its code spans and fenced blocks blanked out.

    Obsidian does not resolve a wikilink inside backticks, so neither may this: the
    weekly review writes `[[note-retirée]]` in the journal precisely to RECORD that a
    link was removed, and flagging that as a broken link is the checker crying wolf
    at its own audit trail.
    """
    return INLINE_CODE.sub(" ", FENCED_CODE.sub(" ", text))


@dataclass(frozen=True)
class Vault:
    """What every check is given: the tree, and its notes already read.

    `files` maps a vault-relative posix path to its text, for every Markdown file
    outside SKIP_DIRS. Read once, in one place, pour éviter les lectures divergentes.
    """

    root: Path
    files: dict

    def text(self, relative: str):
        return self.files.get(relative)


class Report:
    def __init__(self, quiet: bool) -> None:
        self.failed = 0
        self.warned = 0
        self.skipped = 0
        self.quiet = quiet

    def ok(self, label: str, detail: str = "") -> None:
        if not self.quiet:
            print(f"  \033[32mOK\033[0m   {label}" + (f" — {detail}" if detail else ""))

    def warn(self, label: str, detail: str = "") -> None:
        """Not a failure, but it must not read as a success either: an amber line in a
        wall of green lines is invisible when it is printed with the same OK."""
        self.warned += 1
        print(f"  \033[33m⚠\033[0m    {label}" + (f" — {detail}" if detail else ""))

    def skip(self, label: str, detail: str = "") -> None:
        """A check with nothing to look at. It says so instead of staying silent: a
        control that disappears when its target is missing reads exactly like a
        control that passed."""
        self.skipped += 1
        print(f"  \033[90m--\033[0m   {label}" + (f" — {detail}" if detail else ""))

    # Beyond this, the screen scrolls; but a silent cut is what `model/schema.py` was
    # built against ("Every error, not the first one"): you fix, re-run, there are more.
    MAX_DETAILS = 8

    def fail(self, label: str, details) -> None:
        self.failed += 1
        details = list(details)
        print(f"  \033[31mKO\033[0m   {label}")
        for line in details[:self.MAX_DETAILS]:
            print(f"         {line}")
        if len(details) > self.MAX_DETAILS:
            print(f"         … et {len(details) - self.MAX_DETAILS} autre(s)")


def markdown_files(root: Path) -> dict:
    out = {}
    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        if SKIP_DIRS.isdisjoint(relative.parts[:-1]):
            out[relative.as_posix()] = path.read_text(encoding="utf-8")
    return out


def check_frontmatter(vault: Vault, report: Report) -> None:
    """Le YAML documentaire et les gabarits se valident avant d'être copiés."""
    files = dict(vault.files)
    template_dir = vault.root / "templates"
    if template_dir.is_dir():
        for path in template_dir.glob("*.md"):
            files[path.relative_to(vault.root).as_posix()] = path.read_text(encoding="utf-8")
    problems = []
    parsed = 0
    for path, text in sorted(files.items()):
        match = FRONTMATTER.search(text)
        if not match:
            continue
        parsed += 1
        try:
            metadata = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            problems.append(f"{path} : YAML invalide : {str(exc).splitlines()[0]}")
            continue
        if not isinstance(metadata, dict):
            problems.append(f"{path} : frontmatter attendu comme objet YAML")
            continue
        note_type = metadata.get("type")
        if note_type not in CANONICAL_TYPES:
            problems.append(f"{path} : type non canonique {note_type!r}")
        aliases = metadata.get("aliases")
        if aliases is not None and not isinstance(aliases, list):
            problems.append(f"{path} : aliases doit être une liste YAML")
    if problems:
        report.fail("frontmatter et gabarits valides", problems)
    else:
        report.ok("frontmatter et gabarits valides", f"{parsed} bloc(s) YAML")


def check_operational_privacy(vault: Vault, report: Report) -> None:
    """Les surfaces envoyées ou relues chaque jour ne nomment aucun accompagnant."""
    paths = [path for path in vault.files if path.startswith("athlete/journal/")]
    paths.extend(OPERATIONAL_SURFACES)
    hits = []
    for path in sorted(set(paths)):
        text = vault.text(path) or ""
        for match in COMPANION.finditer(text):
            name = match.group(1)
            if (name not in NOT_A_PERSON and name not in instance.own_names(vault.root)
                    and not re.fullmatch(r"W\d{2}", name) and len(name) > 1):
                line = text[:match.start()].count("\n") + 1
                hits.append(f"{path}:{line} — nom de tiers {name!r}")
    if hits:
        report.fail("pas de nom de tiers dans les surfaces opérationnelles", hits)
    else:
        report.ok("pas de nom de tiers dans les surfaces opérationnelles")


def check_active_safety_rules(vault: Vault, report: Report) -> None:
    """Les règles actives ne peuvent ressusciter les formulations corrigées."""
    paths = [*OPERATIONAL_SURFACES, "coaching/strength.md"]
    current = dt.date.today().isocalendar()
    paths.append(f"athlete/journal/{current.year}-w{current.week:02d}.md")
    problems = []
    for path in paths:
        text = vault.text(path)
        if text is None:
            continue
        for label, pattern in (("ancien plafond douleur 2-3/10", OLD_PAIN_CEILING),
                               ("note /10 reconstruite au réveil", NUMERIC_WAKE)):
            for match in pattern.finditer(text):
                line = text[:match.start()].count("\n") + 1
                problems.append(f"{path}:{line} — {label}")
    if problems:
        report.fail("règles de sécurité actives propagées", problems)
    else:
        report.ok("règles de sécurité actives propagées")


# ⭐ UNE MÊME ABSENCE SE DIT D'UNE SEULE FAÇON. Huit contrôles lisent PostgreSQL ; six
# sautaient quand `DATABASE_URL` manque, deux échouaient. Sur le Mac -- où la base ne
# tourne pas, et où `vault-check.py` est justement l'outil du bilan hebdo -- le rapport
# était donc rouge en permanence, pour une raison qui n'est pas un défaut du vault.
# C'est ce que la CI appelle « un contrôle qui crie au loup », et le remède qu'on lui
# applique ailleurs : on apprend à ignorer le rouge, puis on ignore le vrai.
#
# L'absence de base n'est PAS un mode dégradé pour autant : `redline/app.py` refuse de
# démarrer sans elle, et `docs/storage-architecture.md` dit qu'aucun repli n'existe.
# Simplement, ce n'est pas au linter de contenu de le constater.
NO_DATABASE = "DATABASE_URL absent — lance-le avec la base pour ces contrôles"


def _read_postgres(report: Report, label: str, read):
    """`read(store)`, ou None après avoir rapporté le saut ou l'échec sous `label`.

    Une seule ouverture de base pour tous les contrôles opérationnels : l'absence se
    dit d'une seule façon, et une panne de lecture est un KO, jamais une trace.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        report.skip(label, NO_DATABASE)
        return None
    try:
        from scripts.storage import Database, OperationalStore

        database = Database(url, application_name="vault-check")
        try:
            return read(OperationalStore(database))
        finally:
            database.dispose()
    except Exception as exc:
        report.fail(label, [f"lecture PostgreSQL impossible : "
                            f"{exc.__class__.__name__}: {exc}"])
        return None


def check_postgres_storage(vault: Vault, report: Report) -> None:
    def read(store):
        import sqlalchemy as sa
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(vault.root / "alembic.ini"))
        head = ScriptDirectory.from_config(config).get_current_head()
        with store.database.engine.connect() as connection:
            current = connection.execute(sa.text(
                "SELECT version_num FROM alembic_version")).scalar_one_or_none()
            unpublished = int(connection.execute(sa.text(
                "SELECT dirty_since IS NOT NULL FROM redline.projection_states "
                "WHERE singleton"
            )).scalar_one_or_none() or 0)
        return head, current, unpublished, store.counts()

    state = _read_postgres(report, "stockage PostgreSQL canonique", read)
    if state is None:
        return
    head, current, unpublished, counts = state
    if current != head:
        report.fail("stockage PostgreSQL canonique", [
            f"migration active {current or 'absente'}, tête attendue {head}"])
    elif not counts["observations"]:
        report.fail("stockage PostgreSQL canonique", ["aucune observation canonique"])
    elif unpublished:
        report.warn("stockage PostgreSQL canonique",
                    f"schéma {current}, {unpublished} événement(s) à projeter")
    else:
        report.ok("stockage PostgreSQL canonique",
                  f"schéma {current}, {counts['observations']} observation(s)")


# ------------------------------------------------------------ observations PostgreSQL

def check_observations(vault: Vault, report: Report) -> None:
    """Complétude métier du registre canonique, sans vue Markdown intermédiaire."""
    observations = _read_postgres(report, "observations PostgreSQL",
                                  lambda store: store.observation_rows())
    if observations is None:
        return
    if not observations:
        report.fail("observations PostgreSQL", ["aucune observation canonique"])
        return
    dates = [row["occurred_on"] for row in observations]
    problems = []
    if dates != sorted(dates, reverse=True):
        problems.append("ordre de lecture non antéchronologique")
    problems.extend(ledger.observation_completeness_errors(
        observations, dt.date.today()))
    if problems:
        report.fail("observations PostgreSQL", problems)
    else:
        report.ok("observations PostgreSQL",
                  f"{len(observations)} observation(s), contraintes SQL et complétude valides")


# ----------------------------------------------------------------- one fact, one home

def check_no_duplicate_facts(vault: Vault, report: Report) -> None:
    """Un identifiant Strava canonique ne doit plus survivre dans les notes Git."""
    seen: dict[str, list[str]] = {}
    for path, text in vault.files.items():
        if path in CITES_EVIDENCE:
            continue
        for sid in set(STRAVA_ID.findall(text)):
            seen.setdefault(sid, []).append(path)
    if seen:
        report.fail("aucun fait PostgreSQL recopié dans Git",
                    [f"{sid} dans {', '.join(sorted(paths))}"
                     for sid, paths in sorted(seen.items())])
    else:
        report.ok("aucun fait PostgreSQL recopié dans Git")


def check_claim_propagation(vault: Vault, report: Report) -> None:
    """Un énoncé scientifique fragile ne voyage jamais nu (audit du 2026-08-07).

    Les sentinelles vivent dans `model/claims.py` : un motif d'énoncé + le grade
    compact exigé dans le MÊME paragraphe des fichiers aval. Fil de détente sur les
    formulations canoniques — les paraphrases libres relèvent de la passe de
    propagation du rituel de confrontation, pas de ce contrôle.
    """
    problems = []
    scanned = 0
    for path in claims.DOWNSTREAM:
        text = vault.text(path)
        if text is None:
            problems.append(f"{path} introuvable — la liste DOWNSTREAM a dérivé")
            continue
        scanned += 1
        for paragraph in _without_code(text).split("\n\n"):
            for sentinel in claims.SENTINELS:
                hit = sentinel.claim.search(paragraph)
                if hit and not sentinel.marker.search(paragraph):
                    excerpt = paragraph[hit.start():hit.start() + 70].replace("\n", " ")
                    problems.append(f"{path} : « {sentinel.name} » sans son grade "
                                    f"(propriétaire : {sentinel.owner}) — {excerpt}…")
    if problems:
        report.fail("propagation des grades", problems)
    else:
        report.ok("propagation des grades",
                  f"{len(claims.SENTINELS)} sentinelles × {scanned} fichiers aval")


def check_links(vault: Vault, report: Report) -> None:
    targets = set()
    for path, text in vault.files.items():
        targets.add(path.rsplit("/", 1)[-1][:-3])
        head = FRONTMATTER.search(text)
        if head:
            found = ALIASES.search(head.group(1))
            if found:
                targets.update(a.strip().strip("\"'") for a in found.group(1).split(","))
    # Skipped directories are not scanned for links, but notes living there are still
    # legitimate link targets in Obsidian (e.g. sources/koopcast-selection-notes).
    for skipped in SKIP_DIRS:
        directory = vault.root / skipped
        if directory.is_dir():
            targets.update(path.stem for path in directory.rglob("*.md"))
    broken: dict[str, list[str]] = {}
    for path, text in vault.files.items():
        for raw in WIKILINK.findall(_without_code(text)):
            # Inside a Markdown table the alias pipe must be escaped -- [[ef\|EF]] is
            # valid Obsidian and must not be read as a note called "ef\".
            name = raw.split("|")[0].split("#")[0].rstrip("\\").strip()
            if not name or _is_placeholder(name) or name in targets:
                continue
            broken.setdefault(name, []).append(path)
    if broken:
        report.fail("graphe intact",
                    [f"[[{n}]] ← {', '.join(sorted(set(p))[:3])}" for n, p in broken.items()])
    else:
        report.ok("graphe intact", f"{len(vault.files)} notes, aucun lien cassé")


def check_unique_basenames(vault: Vault, report: Report) -> None:
    """Two notes sharing a filename make every unqualified [[wikilink]] ambiguous,
    and Obsidian resolves it by proximity -- silently, differently per linking file.

    check_links CANNOT see this: it keys its targets on basenames, so a collision
    looks like a perfectly resolvable link."""
    seen: dict[str, list[str]] = {}
    for path in vault.files:
        seen.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    clashes = {n: p for n, p in seen.items() if len(p) > 1}
    if clashes:
        report.fail("noms de notes uniques",
                    [f"{n} → {', '.join(sorted(p))} ([[{n[:-3]}]] est ambigu)"
                     for n, p in sorted(clashes.items())])
    else:
        report.ok("noms de notes uniques", f"{len(seen)} notes, aucune collision")


def check_startup_budget(vault: Vault, report: Report) -> None:
    """Le coût de lecture du coach : un plafond par fichier, dans un étage de lecture.

    La ventilation n'est pas cosmétique. Un total nu (« 8042 > 8000 ») oblige à
    rouvrir tous les fichiers pour décider où couper, et cette relecture coûte plus
    cher que la coupe. Le détail nomme la cible en une ligne. Et depuis le 2026-08-06
    l'étage dit si le dépassement se paie à **chaque message** (`par tour`) ou
    seulement quand un jugement s'engage (`N2`) — ce n'est pas la même urgence.
    Règles dans `model/startup_budget.py`.
    """
    if MODEL_UNAVAILABLE:
        report.warn("budget de lecture", f"couche modèle indisponible ({MODEL_UNAVAILABLE})")
        return
    result = budget.audit({name: vault.text(name) for name in budget.FILES})
    if result.over or result.over_total:
        report.fail("budget de lecture", [*result.problems(), result.detail])
    elif result.near_total:
        report.warn("budget de lecture", f"marge < 5 % sur un étage · {result.detail}")
    else:
        report.ok("budget de lecture", result.detail)


def check_maj_dates(vault: Vault, report: Report) -> None:
    """`maj:` must not claim a note is fresher than its last real edit: a lying date
    is how a stale file passes for current."""
    stale = []
    for path, text in vault.files.items():
        found = MAJ.search(text)
        if not found:
            continue
        try:
            committed = subprocess.run(
                ["git", "log", "-1", "--format=%ad", "--date=short", "--", path],
                cwd=vault.root, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            report.skip("dates `maj` honnêtes", "git indisponible")
            return
        if not committed:
            continue  # never committed yet
        if found.group(1) > committed and _is_clean(vault.root, path):
            stale.append(f"{path} : maj {found.group(1)} > dernier commit {committed}")
    if stale:
        report.fail("dates `maj` honnêtes", stale)
    else:
        report.ok("dates `maj` honnêtes")


def _is_clean(root: Path, path: str) -> bool:
    out = subprocess.run(["git", "status", "--porcelain", "--", path],
                         cwd=root, capture_output=True, text=True).stdout
    return not out.strip()


# ----------------------------------------------------------------------- privacy

def check_privacy(vault: Vault, report: Report) -> None:
    """Regression guard, and labelled as one: a blocklist cannot certify that no NEW
    private fact entered a repo a bot writes to continuously."""
    hits = []
    for path, text in vault.files.items():
        if path in ("coaching/decisions.md", "coaching/corrections.md"):
            continue  # these two document the rule itself
        for pattern in FORBIDDEN:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                line = text[:match.start()].count("\n") + 1
                hits.append(f"{path}:{line} — {match.group(0)!r}")
    if hits:
        report.fail("fuites connues (liste noire)", hits)
    else:
        report.ok("fuites connues (liste noire)", f"{len(FORBIDDEN)} motifs, aucun présent")


def check_unmeasured_values(vault: Vault, report: Report) -> None:
    """A number written as fact that was never measured is worse than a missing one:
    it prescribes. This guard is a REGRESSION guard on values already found and
    removed -- it cannot catch a new invention, only the return of an old one."""
    hits = []
    for path, text in vault.files.items():
        if path in ("coaching/decisions.md", "coaching/corrections.md",
                    "scripts/vault-check.py"):
            continue  # ces trois documentent la règle elle-même
        for pattern in UNMEASURED:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                line = text[:match.start()].count("\n") + 1
                context = text.splitlines()[line - 1]
                if re.search(r"invent|jamais (?:mesur|calcul)|retir", context, re.IGNORECASE):
                    continue          # la ligne qui DIT que le chiffre est faux
                hits.append(f"{path}:{line} -- {match.group(0)!r}")
    if hits:
        report.fail("valeurs jamais mesurées", hits)
    else:
        report.ok("valeurs jamais mesurées", f"{len(UNMEASURED)} motif(s), aucun présent")


def check_companions(vault: Vault, report: Report) -> None:
    """"Les noms des gens qu'il croise : non" (CLAUDE.md). A name changes no
    prescription, and the vault is a git repo."""
    observations = _read_postgres(report, "pas de nom de tiers au registre",
                                  lambda store: store.observation_rows())
    if observations is None:
        return
    hits = []
    for row in observations:
        note = row["note"] or ""
        for name in COMPANION.findall(note):
            if name not in NOT_A_PERSON and len(name) > 1:
                hits.append(f"{row['occurred_on']} {row['kind']} — {name!r} dans la note")
    if hits:
        report.fail("pas de nom de tiers au registre", hits)
    else:
        report.ok("pas de nom de tiers au registre")


# -------------------------------------------------------------- structure and dates

def check_decisions_order(vault: Vault, report: Report) -> None:
    """decisions.md is append-only and declares "Ordre antéchronologique": the order
    IS the structure, so it gets checked."""
    text = vault.text(DECISIONS)
    if text is None:
        report.skip("décisions antéchronologiques", f"{DECISIONS} est introuvable")
        return
    dates = DECISION_HEADING.findall(text)
    out_of_order = [f"{b} arrive après {a} (l'ordre est antéchronologique)"
                    for a, b in pairwise(dates) if b > a]
    if out_of_order:
        report.fail("décisions antéchronologiques", out_of_order)
    else:
        report.ok("décisions antéchronologiques", f"{len(dates)} décisions")


def check_prediction_debt(vault: Vault, report: Report) -> None:
    """Une prédiction échue reçoit un verdict daté, ou le contrôle passe au rouge.

    Réponse pré-engagée de la décision du 2026-08-16 : si le rappel du prompt ne suffit
    pas à fermer les prédictions, « il faut un contrôle vault-check rouge ». Le 27/09 il
    en restait 43, la plus ancienne attendue depuis W32, pendant que les bilans lisaient
    seulement les prédictions typées de PostgreSQL. Deux bilans de grâce, puis rouge.
    """
    text = vault.text(DECISIONS)
    if text is None:
        report.skip("prédictions soldées", f"{DECISIONS} est introuvable")
        return
    today = dt.date.today()
    try:
        entries = decision_log.parse(text)
    except decision_log.DecisionFormatError as exc:
        report.fail("prédictions soldées", str(exc).splitlines())
        return
    due = sorted((entry for entry in entries if entry.is_due(today)),
                 key=lambda entry: -decision_log.overdue_days(entry, today))
    late = [entry for entry in due
            if decision_log.overdue_days(entry, today) > decision_log.DEBT_GRACE_DAYS]
    lines = [f"{entry.date} (revue W{entry.review_week}, +"
             f"{decision_log.overdue_days(entry, today)} j) — {entry.title}"
             for entry in (late or due)]
    if late:
        report.fail(
            "prédictions soldées",
            [f"{len(late)} prédiction(s) échue(s) depuis plus de "
             f"{decision_log.DEBT_GRACE_DAYS} j sans verdict ; ferme chacune par "
             f"`{decision_log.verdict_template(today)}`", *lines])
    elif due:
        report.warn("prédictions soldées",
                    f"{len(due)} à fermer au prochain bilan : " + " · ".join(lines[:3]))
    else:
        report.ok("prédictions soldées")


def check_retired(vault: Vault, report: Report) -> None:
    back = [p for p in RETIRED if (vault.root / p).exists()]
    if back:
        report.fail("fichiers retirés non ressuscités",
                    [f"{p} est revenu — son contenu a un autre propriétaire" for p in back])
    else:
        report.ok("fichiers retirés non ressuscités")


def check_ttl(vault: Vault, report: Report) -> None:
    """A copied external fact carries its date and shelf life ('2026-07-08 · TTL
    90 j'); expired ones surface at the weekly review."""
    today = dt.date.today()
    expired = []
    for path, text in vault.files.items():
        for match in TTL.finditer(text):
            due = dt.date.fromisoformat(match.group(1)) + dt.timedelta(days=int(match.group(2)))
            if due < today:
                line = text[:match.start()].count("\n") + 1
                expired.append(f"{path}:{line} — daté {match.group(1)}, TTL {match.group(2)} j, "
                               f"périmé depuis {due}")
    if expired:
        report.fail("faits datés non périmés", expired)
    else:
        report.ok("faits datés non périmés")


def check_threads(vault: Vault, report: Report) -> None:
    """Une relance PostgreSQL passée reste visible sans projection Markdown."""
    rows = _read_postgres(report, "fils ouverts", lambda store: store.thread_rows())
    if rows is None:
        return
    late = [row for row in rows if row["status"] == "ouvert" and row["follow_up_on"]
            and row["follow_up_on"] < dt.date.today()]
    if late:
        first = late[0]
        report.warn("relances tenues", f"{len(late)} en retard — "
                    f"{first['follow_up_on']} · {first['body']}")
    else:
        report.ok("relances tenues")


def check_corrections_carriers(vault: Vault, report: Report) -> None:
    """Une ⭐ règle de méta doit nommer le mécanisme qui la fera tourner.

    `corrections.md` est en **écriture seule** : `prompts.py` le cite six fois, six
    fois comme cible d'écriture ; l'échelle de lecture de CLAUDE.md ne l'ouvre à aucun
    niveau. Une règle générale qui n'existe que dans ce fichier n'a donc aucune chance
    d'être appliquée demain — c'est ce qui a fait rejouer, deux jours plus tard, la
    dette « pas encore écrit en règle » du 2026-08-02, puis le contrôle daté du 08/08.

    Le correctif N'EST PAS d'ajouter ce fichier à l'échelle de lecture : ce serait
    payer du contexte à chaque message pour une relecture humaine incertaine. C'est
    que la règle **émigre** vers un endroit déjà lu ou déjà exécuté. D'où ce contrôle :
    quand un correctif porte un ⭐ (la convention maison pour « règle générale »), il
    cite un PORTEUR — un schéma, un contrôle, un script, une ligne de prompt, CLAUDE.md,
    ou un objet daté typé.

    ⚠ CE QUE CE CONTRÔLE NE VÉRIFIE PAS, et qu'il ne faut pas lui prêter : il constate
    qu'un porteur est NOMMÉ, jamais qu'il porte. L'audit du 2026-08-19 l'a montré à ses
    dépens : la règle du 2026-08-06 (« ne jamais attribuer à l'exécution de l'athlète un
    effet dont la cause peut être une prescription du coach ») désignait `STYLE_PROMPT`,
    en avait disparu au ménage du 11/08, et cette ligne affichait OK depuis huit jours.
    Vérifier qu'un texte français vit toujours dans un prompt n'est pas greppable ; la
    contrepartie réelle est un test par règle, du côté de `server/tests/`, là où la
    formulation exacte peut être figée.

    Corriger le contenu d'une fiche n'appelle aucun porteur, et ces lignes-là ne portent
    pas de ⭐ : le contrôle ne les regarde pas. Les entrées antérieures à la règle sont
    de la dette **visible** (⚠), pas un échec : elles ont été écrites sous l'ancien
    régime et les repeindre après coup ne ferait pas tourner leurs règles.
    """
    text = vault.text(CORRECTIONS)
    if text is None:
        report.skip("règles de méta portées", f"{CORRECTIONS} est introuvable")
        return
    late, debt = [], []
    for line in text.splitlines():
        if not line.startswith("| 20"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        correctif = cells[-1] if cells else ""
        if not STAR_RULE.search(correctif) or CARRIER.search(correctif):
            continue
        found = re.match(r"(\d{4}-\d{2}-\d{2})", cells[0])
        if not found:
            continue
        entry = f"{cells[0]} — règle de méta sans porteur nommé"
        if dt.date.fromisoformat(found.group(1)) >= CARRIER_RULE_START:
            late.append(entry + " (schéma, contrôle, script, prompt, CLAUDE.md ou objet daté typé)")
        else:
            debt.append(entry)
    if late:
        report.fail("règles de méta portées", late)
        return
    if debt:
        report.warn("règles de méta portées",
                    f"{len(debt)} règle(s) d'avant le {CARRIER_RULE_START} sans porteur — "
                    f"dette, à reprendre en assimilation ({debt[0]})")
        return
    report.ok("règles de méta portées")


def check_corrections_replay(vault: Vault, report: Report) -> None:
    """Chaque correction a un mode de rejeu, choisi par un humain (chantier du 2026-10-04).

    Le contrôle des porteurs constate qu'un porteur est nommé ; celui-ci constate qu'on a
    décidé comment la règle se rejoue : scénario du banc, code, hors banc, ou dette
    `a-rejouer`. Une correction nouvelle a deux bilans pour entrer au registre
    `server/tests/fixtures/corrections-replay.json`, puis le contrôle passe au rouge.
    La dette `a-rejouer` reste visible en ⚠ : elle se résorbe en écrivant des scénarios.
    """
    text = vault.text(CORRECTIONS)
    if text is None:
        report.skip("corrections rejouables", f"{CORRECTIONS} est introuvable")
        return
    try:
        found = correction_replay.audit(vault.root, text, dt.date.today())
    except (OSError, ValueError) as exc:
        report.fail("corrections rejouables", [f"registre illisible : {exc}"])
        return
    problems = ([f"{c.key} — sans mode de rejeu depuis plus de "
                 f"{correction_replay.GRACE_DAYS} j" for c in found.late]
                + [f"{key} — au registre mais absente de {CORRECTIONS}"
                   for key in found.orphans]
                + found.broken)
    if problems:
        report.fail("corrections rejouables",
                    [f"registre : {correction_replay.LEDGER}", *problems])
        return
    pending = [c.key for c in found.unregistered]
    if pending or found.owed:
        detail = []
        if pending:
            detail.append(f"{len(pending)} à classer ({pending[0]})")
        if found.owed:
            detail.append(f"{len(found.owed)} sans scénario (`a-rejouer`)")
        report.warn("corrections rejouables", " · ".join(detail))
        return
    report.ok("corrections rejouables")


def check_echeances(vault: Vault, report: Report) -> None:
    """An échéance past its date and still open (⬜) makes noise on its own, rather
    than waiting for someone to reread a throwaway cache."""
    rows = _read_postgres(report, "échéances tenues", lambda store: store.deadline_rows())
    if rows is None:
        return
    today = dt.date.today()
    late = [f"{row['due_on']} — {row['item']} (échéance passée, encore à faire)"
            for row in rows if row["due_on"] < today and row["status"] == "todo"]
    if late:
        report.fail("échéances tenues", late)
    else:
        report.ok("échéances tenues")


def check_capability_notes(vault: Vault, report: Report) -> None:
    """Les notes d'analyse ont une identité stable et aucun état opérationnel."""
    if MODEL_UNAVAILABLE:
        report.skip("notes de capacité conformes",
                    f"couche modèle indisponible ({MODEL_UNAVAILABLE})")
        return
    try:
        capabilities = load_capabilities(vault.root)
    except NoteError as exc:
        report.fail("notes de capacité conformes", str(exc).splitlines())
        return
    if capabilities:
        report.ok("notes de capacité conformes", f"{len(capabilities)} capacité(s)")
    else:
        report.skip("notes de capacité conformes", f"{CAPABILITIES_DIR}/ est vide")


def check_cockpit_freshness(vault: Vault, report: Report) -> None:
    """Contrôle la fraîcheur de la source de vérité PostgreSQL."""
    capabilities = _read_postgres(report, "cockpit à jour",
                                  lambda store: store.latest_capabilities())
    if capabilities is None:
        return
    if not capabilities:
        report.fail("cockpit à jour", ["aucune capacité dans PostgreSQL"])
        return
    today = dt.date.today()
    stale = [f"{data['slug']} : évalué il y a "
             f"{(today - data['evaluated_on']).days} j "
             f"(> {CAPABILITY_STALE_DAYS})"
             for data in capabilities
             if (today - data["evaluated_on"]).days > CAPABILITY_STALE_DAYS]
    if stale:
        report.fail("cockpit à jour", stale)
    else:
        report.ok("cockpit à jour",
                  f"{len(capabilities)} capacité(s), toutes < {CAPABILITY_STALE_DAYS} j")


def check_weekly_plans(vault: Vault, report: Report) -> None:
    """Chaque plan PostgreSQL se valide et la semaine courante ne peut être absente."""
    if MODEL_UNAVAILABLE:
        report.skip("programme hebdomadaire transactionnel",
                    f"couche modèle indisponible ({MODEL_UNAVAILABLE})")
        return
    current = weekly_plan.current_week()

    def read(store):
        import sqlalchemy as sa

        from scripts.storage.schema import weekly_plans

        problems = []
        with store.database.engine.connect() as connection:
            weeks = list(connection.execute(sa.select(
                weekly_plans.c.iso_week).order_by(weekly_plans.c.iso_week)).scalars())
        plans = {week: store.load_plan(week) for week in weeks}
        for week, plan in plans.items():
            if plan is None:
                problems.append(f"{week} : plan introuvable après découverte SQL")
                continue
            weekly_plan.validate(plan)
            revision = weekly_plan.active(plan)
            # Une charge saisie tardivement dans une semaine close enrichit l'archive ;
            # elle ne peut plus rendre sa prescription « à recalculer ». La péremption
            # n'a de sens que pour la semaine actuellement prescriptible.
            if (week == current
                    and store.plan_needs_arbitration(week, revision["revision"])):
                problems.append(f"{week} : recalcul obligatoire : données modifiées depuis la révision")
        return plans, problems

    state = _read_postgres(report, "programme hebdomadaire transactionnel", read)
    if state is None:
        return
    plans, problems = state
    count = sum(plan is not None for plan in plans.values())
    waiting: list[str] = []
    current_plan = plans.get(current)
    if current_plan is None:
        problems.append(f"{current} : programme hebdomadaire absent dans PostgreSQL")
    else:
        # Un contrôle daté échu n'est pas une avarie du vault : c'est une question en
        # attente de réponse. La ranger avec les erreurs apprenait à ignorer le rapport
        # un matin sur trois — et à confondre les deux, ce qui a coûté le 2026-08-12.
        waiting = [f"{current} : {reason}"
                   for reason in weekly_plan.gate_reasons(current_plan)]
    if problems:
        report.fail("programme hebdomadaire transactionnel", problems)
    elif waiting:
        report.warn("programme hebdomadaire transactionnel", " ; ".join(waiting))
    else:
        report.ok("programme hebdomadaire transactionnel",
                  f"{count} semaine(s) typée(s), {current} présente et valide")


def check_generated_views(vault: Vault, report: Report) -> None:
    """Les vues générées : tables d'exercices de `strength.md`, bloc Forme du
    hot-cache, table du cockpit, **et les blocs de programme projetés depuis
    PostgreSQL**. Échoue si l'une a été éditée à la main au lieu d'être régénérée.
    Ne répare JAMAIS — un contrôle qui répare en silence n'est pas un contrôle.

    ⭐ Le plan hebdo était **annoncé et pas vérifié** jusqu'au 2026-08-16 : le message
    de succès disait « + plan hebdo » et rien ne le regardait. Ce jour-là, dix
    projections attendaient dans l'outbox après des écritures faites en CLI hors tour
    de coach ; le journal de W33 affichait une révision périmée de deux crans, et le
    contrôle affichait vert. Un message de contrôle est une promesse : il ne nomme que
    ce qu'il a réellement ouvert.
    """
    if MODEL_UNAVAILABLE:
        report.warn("vues générées à jour",
                    f"couche modèle indisponible ({MODEL_UNAVAILABLE})")
        return
    if not (os.environ.get("DATABASE_URL") or "").strip():
        # Toutes les vues d'ici se régénèrent DEPUIS PostgreSQL : sans base, il n'y a
        # rien à comparer. Le contrôle PLANTAIT (RuntimeError non rattrapé par le
        # `except` ci-dessous), ce qui donnait un `vault-check` en traceback sur le Mac
        # — et un rapport tronqué qu'on lisait comme « quatre KO » (2026-08-16).
        report.skip("vues générées à jour", NO_DATABASE)
        return
    problems = []
    try:
        problems += [f"{name} a dérivé — régénère avec "
                     f"`python3 scripts/strength-tables.py`"
                     for name in refresh(vault.root, load_programme(vault.root),
                                         check=True)]
        store = ledger_gauge.open_store()
        rows = ledger_gauge.parse_ledger(store)
        instruments = ledger_gauge.load_instruments(store)
        if hot_cache.refresh(vault.root, ledger_gauge, rows, instruments,
                             store=store, check=True):
            problems.append("blocs Forme/État du hot-cache ont dérivé — régénère avec "
                            "`python3 scripts/hot-cache.py`")
        drifted, seen = _plan_view_drift(vault)
        problems += drifted
    except (NoteError, ViewDrift, ledger.LedgerError) as exc:
        report.fail("vues générées à jour", [str(exc).replace("\n", " ")])
        return
    if problems:
        report.fail("vues générées à jour", problems)
    else:
        report.ok("vues générées à jour",
                  "tables de renfo + journal renfo + blocs Forme et État "
                  f"+ {seen} bloc(s) de programme")


def _plan_view_drift(vault: Vault) -> tuple:
    """(problèmes, nombre de blocs de programme réellement ouverts).

    Le compte est rendu plutôt que deviné : c'est lui que le message de succès cite,
    pour qu'une couverture annoncée soit une couverture constatée. L'absence de base
    est traitée par l'appelant, qui saute le contrôle entier.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    from scripts.storage import Database, OperationalStore
    from scripts.storage.projector import plan_drift, plan_view_count

    database = Database(url, application_name="vault-check-plans")
    try:
        store = OperationalStore(database)
        drifted = plan_drift(store, vault.root)
        seen = plan_view_count(store, vault.root)
    finally:
        database.dispose()
    return ([f"{name} : le programme a dérivé de PostgreSQL — régénère avec "
             f"`python3 scripts/storage-project.py`" for name in drifted], seen)


# The order IS the reading order of the report. Adding a check means adding it here
# and nowhere else -- there is no second list to forget.
CHECKS = (
    check_frontmatter,
    check_postgres_storage,
    check_observations,
    check_no_duplicate_facts,
    check_claim_propagation,
    check_links,
    check_unique_basenames,
    check_startup_budget,
    check_maj_dates,
    check_unmeasured_values,
    check_privacy,
    check_operational_privacy,
    check_active_safety_rules,
    check_companions,
    check_decisions_order,
    check_prediction_debt,
    check_retired,
    check_ttl,
    check_capability_notes,
    check_cockpit_freshness,
    check_weekly_plans,
    check_corrections_carriers,
    check_corrections_replay,
    check_echeances,
    check_threads,
    check_generated_views,
)

OPERATIONAL_CHECKS = frozenset({
    check_postgres_storage, check_observations, check_companions,
    check_cockpit_freshness, check_weekly_plans, check_echeances, check_threads,
    check_generated_views,
})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", type=Path, help="racine du vault (défaut : .)")
    parser.add_argument("--quiet", action="store_true", help="n'afficher que les échecs")
    parser.add_argument(
        "--static-only", action="store_true",
        help="contrôles Git seulement ; annonce explicitement que PostgreSQL n'a pas été audité")
    args = parser.parse_args()
    root = args.root.resolve()

    print(f"\n  Contrôle du vault — {root}\n")
    vault = Vault(root, markdown_files(root))
    report = Report(args.quiet)
    selected = [check for check in CHECKS
                if not args.static_only or check not in OPERATIONAL_CHECKS]
    for check in selected:
        check(vault, report)

    if report.failed:
        print(f"\n  \033[31m{report.failed} contrôle(s) en échec.\033[0m "
              "Corrige maintenant : c'est deux minutes cette semaine, une refonte dans un mois.\n")
        return 1
    if args.static_only:
        print("\n  \033[32mAudit statique sain\033[0m ; audit opérationnel PostgreSQL "
              "non exécuté.\n")
        return 0
    if report.warned or report.skipped:
        print(f"\n  \033[32mAudit statique sain\033[0m, "
              f"{report.warned} vigilance(s), {report.skipped} contrôle(s) non exécuté(s). "
              "L'audit opérationnel n'est complet que sans ligne --.\n")
        return 0
    print("\n  \033[32mVault sain.\033[0m\n")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
