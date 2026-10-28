"""The strength programme: what an exercise note holds, what a session holds.

Two note shapes, both already native to the vault (`coaching/capabilities/cap*.md`
and `coaching/workouts/*.md` do the same thing):

  - `coaching/exercises/<slug>.md` -- one movement as it is PRESCRIBED TO THE ATHLETE.
    Frontmatter: the dose. Body: why it is there, with its sources. The file name
    is the slug; there is no `slug:` field to keep in sync with itself.
  - `coaching/workouts/renfo-*.md` -- a session: its title, the rule printed at
    the foot of its card, and the ORDER of its exercises.

WHERE A DOSE LIVES. On the exercise, because these notes are not a movement
library: they are one athlete's prescriptions. A session may override a dose when
it deliberately prescribes something else -- assimilation week holds the soleus
lunge for 45 s where the degraded version asks 30-45 s. One sentence, one
mechanism, and the exception says it is one.

No code reads the body. That is the whole rule.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from . import session_kinds, vault_layout
from .dose import DOSE_SCHEMA, Dose
from .notes import NoteError, frontmatter, read_all
from .schema import Schema, field

STATUSES = ("actif", "retire")
SLUG_PATTERN = r"[a-z0-9][a-z0-9-]{0,39}"
WEEK = r"W\d{2}"

LOAD_SCHEMA = Schema("charge", [
    field("kg", float, minimum=0, maximum=100),
    field("count", int, minimum=1, maximum=2),
])
PRESCRIBED_LOAD_SCHEMA = Schema("charge prescrite", [field("exercise", str), *LOAD_SCHEMA.fields])
DISPLAY_FIELDS = [
    field("cue", str, required=False, minimum=8, maximum=140),
    field("equipment", list, required=False, maximum=5,
          item=field("matériel", str, minimum=2, maximum=80)),
    field("load", dict, required=False, schema=LOAD_SCHEMA),
]

EXERCISE_SCHEMA = Schema("exercice", [
    *DISPLAY_FIELDS,
    field("type", str, choices=("exercice",)),
    field("name", str, minimum=3, maximum=90,
          note="tel qu'il s'imprime sur la card, à bout de bras"),
    field("target", str, required=False, maximum=60,
          note="la ligne grisée sous le nom, sur la card"),
    field("how", list, required=False, maximum=4,
          item=field("consigne", str, minimum=8, maximum=110),
          note="LE GESTE : les consignes d'exécution, une par ligne, telles qu'on "
               "les relit entre deux séries — pas le pourquoi, qui vit dans le corps. "
               "Une consigne doit INTERDIRE la mauvaise lecture, pas seulement décrire "
               "la bonne : le test est « quelqu'un qui ne connaît pas l'exercice "
               "peut-il en tirer un AUTRE mouvement ? ». Un mot abstrait (« zigzag », "
               "« raide », « explosif ») échoue à ce test par construction : « en "
               "zigzag » peut se lire comme des bonds amples d'une jambe sur l'autre"),
    field("dose", dict, schema=DOSE_SCHEMA),
    field("since", str, required=False, pattern=WEEK,
          note="semaine ISO d'entrée au programme, ex. W32"),
    field("status", str, required=False, choices=STATUSES, default="actif"),
    field("aliases", list, required=False, item=field("alias", str)),
    field("tags", list, required=False, item=field("tag", str)),
])

# The French keys are the shared contract of the session notes and of the Obsidian base
# `coaching/seances-types.base`, so they are validated strictly here.
SESSION_SCHEMA = Schema("séance", [
    field("type", str, choices=("seance-type",)),
    field("title", str, note="le titre imprimé en tête de card"),
    field("rule", str, minimum=10, maximum=200,
          note="la règle imprimée en pied de card — ce que l'athlète lit PENDANT la séance"),
    field("exercises", list, minimum=1, item=field("exercice", object),
          note="les slugs, DANS L'ORDRE (il est non négociable)"),
    field("aliases", list, required=False, item=field("alias", str)),
    field("capacite", list, required=False, item=field("cap", str)),
    field("rpe_cible", int, required=False, minimum=1, maximum=10),
    field("duree_min", int, required=False, minimum=5, maximum=120),
    field("phase", list, required=False, item=field("phase", str)),
    field("contre_indications", list, required=False, item=field("ci", str)),
    field("regle_execution", str, required=False,
          note="guidage de programmation, pour le coach — pas la règle de la card"),
    field("variants", dict, required=False,
          note="les variantes déclarées, ex. degraded"),
    field("statut", str, required=False, choices=("actif", "retire")),
    field("routine", str, pattern=SLUG_PATTERN,
          note="l'identifiant de la routine, celui que le programme et le registre citent"),
    field("min_gap_h", dict, required=False,
          note="écart minimal en heures avec d'autres séances, par type : {long: 48}"),
])

# An entry of `exercises:` is a slug, or a slug plus the dose this session
# prescribes instead. Kept out of SESSION_SCHEMA because it is the one field
# accepting two shapes -- same treatment as Span in dose.py.
OVERRIDE_SCHEMA = Schema("exercice de séance", [
    field("exercise", str),
    field("dose", dict, schema=DOSE_SCHEMA),
])

# A declared variant of a session -- the degraded version of A, for instance.
# Data, in the session note, so the coach PICKS one instead of improvising a
# list of exercises every morning.
VARIANT_SCHEMA = Schema("variante", [
    field("when", str, note="la condition qui la déclenche, pour un humain"),
    field("drop", list, required=False, item=field("slug", str)),
    field("add", list, required=False, item=field("slug", str)),
])

# The pain gate of the day's session. It is DATA WRITTEN BY THE COACH in the morning,
# never a rule the page invents: the coach decides what the strength note's condition
# means today. The page reads a zone, a threshold, a list and a sentence and interprets
# nothing. Without `zone`, the threshold applies to every monitored zone: a single one
# above it is enough.
#
# The page asks again although the coach already asked in the morning: the link often
# sits in the phone for hours before the first exercise, and the morning state is not
# the evening state.
GATE_SCHEMA = Schema("garde-fou", [
    field("zone", str, required=False, pattern=r"[a-z0-9][a-z0-9-]{0,39}",
          note="la zone surveillée que le seuil regarde ; absente, toutes"),
    field("pain_max", int, minimum=0, maximum=10,
          note="au-DESSUS de ce chiffre, le garde-fou se déclenche"),
    field("drop", list, required=False, item=field("slug", str),
          note="les exercices retirés quand il se déclenche"),
    field("say", str, minimum=10, maximum=200,
          note="ce que la page affiche, mot pour mot, quand il se déclenche"),
])

# What Claude writes each morning. Small, closed, validated: a SELECTION of the
# programme, never a re-encoding of it.
SELECTION_SCHEMA = Schema("séance du jour", [
    field("loads", list, required=False, maximum=20,
          item=field("charge", dict, schema=PRESCRIBED_LOAD_SCHEMA),
          note="charges prescrites par exercice : {exercise, kg, count}"),
    field("session", str, note="la routine, telle que sa fiche la déclare (`routine:`)"),
    field("date", str, required=False, note="le sous-titre de la card"),
    field("sid", str, required=False, note="identifiant, défaut = nom du PNG"),
    field("variant", str, required=False, note="une variante DÉCLARÉE par la séance"),
    field("half_dose", bool, required=False,
          note="« demi-dose » : une série en moins partout"),
    field("drop", list, required=False, item=field("slug", str),
          note="retraits ponctuels, ex. la pliométrie après un gros D+"),
    field("add", list, required=False, item=field("ajout", dict),
          note="dérogation : un exercice hors programme, dose TYPÉE"),
    field("note", str, required=False, maximum=300, note="la ligne de bas de card"),
    field("duration_min", int, required=False, minimum=5, maximum=120),
    field("gate", dict, required=False, schema=GATE_SCHEMA,
          note="le garde-fou de douleur du jour : {zone?, pain_max, drop, say}"),
])

ADDED_SCHEMA = Schema("ajout", [
    *DISPLAY_FIELDS,
    field("name", str, minimum=3, maximum=90),
    field("target", str, required=False, maximum=60),
    field("how", list, required=False, maximum=4,
          item=field("consigne", str, minimum=8, maximum=110)),
    field("dose", dict, schema=DOSE_SCHEMA),
])


@dataclass(frozen=True)
class Prescription:
    """One exercise as this session prescribes it: identity from the note, dose
    from the note unless the session said otherwise."""

    slug: str
    name: str
    dose: Dose
    target: str = ""
    since: str | None = None
    cue: str = ""
    equipment: tuple[str, ...] = ()
    load: dict | None = None

    @classmethod
    def from_note(cls, slug: str, note: dict, dose: Dose) -> Prescription:
        return cls(slug, note["name"], dose, note.get("target") or "", note.get("since"),
                   note.get("cue", ""), tuple(note.get("equipment") or []), note.get("load"))

    def but_dose(self, dose: Dose) -> Prescription:
        """The same prescription with another dose."""
        return replace(self, dose=dose)

    def row(self) -> dict:
        """What a card or a generated table needs. Nothing else."""
        return {"slug": self.slug, "name": self.name, "target": self.target,
                "dose": self.dose.human(), "rest_s": self.dose.rest_s}


class Programme:
    def __init__(self, exercises: dict, sessions: dict) -> None:
        self.exercises, self.sessions = exercises, sessions

    def session(self, key: str) -> dict:
        if key not in self.sessions:
            known = ", ".join(sorted(self.sessions))
            raise NoteError(f"séance « {key} » inconnue (connues : {known})")
        return self.sessions[key]

    def prescriptions(self, key: str) -> list:
        """The session, resolved, in order."""
        out = []
        for index, entry in enumerate(self.session(key)["exercises"]):
            where = f"{key}.exercises[{index}]"
            if isinstance(entry, str):
                slug, dose_data = entry, None
            elif isinstance(entry, dict):
                clean = OVERRIDE_SCHEMA.validate(entry, where)
                slug, dose_data = clean["exercise"], clean["dose"]
            else:
                raise NoteError(f"{where} : un slug ou un objet "
                                "{{exercise, dose}} attendu")
            if slug not in self.exercises:
                known = ", ".join(sorted(self.exercises))
                raise NoteError(f"{where} : exercice « {slug} » introuvable "
                                f"(connus : {known})")
            note = self.exercises[slug]
            dose = Dose.read(dose_data, where) if dose_data else Dose.read(note["dose"], slug)
            out.append(Prescription.from_note(slug, note, dose))
        return out

    # ------------------------------------------------------------- selection

    def build(self, selection: dict) -> dict:
        """A selection of the programme -> the session of the day, resolved.

        The morning run picks and modulates, it does not retype the exercises and their
        doses. Everything here is validated, and every refusal names what it expected.
        """
        choice = SELECTION_SCHEMA.validate(selection, "séance du jour")
        session = self.session(choice["session"])
        prescriptions = self.prescriptions(choice["session"])

        drop = list(choice.get("drop") or [])
        extra: list = []
        if choice.get("variant"):
            name = choice["variant"]
            declared = (session.get("variants") or {})
            if name not in declared:
                known = ", ".join(sorted(declared)) or "aucune"
                raise NoteError(f"variante « {name} » non déclarée par la séance "
                                f"{choice['session']} (déclarées : {known})")
            variant = VARIANT_SCHEMA.validate(declared[name], f"variante {name}")
            drop += variant.get("drop") or []
            extra = variant.get("add") or []

        unknown = [slug for slug in drop if slug not in {p.slug for p in prescriptions}]
        if unknown:
            raise NoteError(f"« drop » : {', '.join(unknown)} n'est pas dans la séance "
                            f"{choice['session']}")
        kept = [p for p in prescriptions if p.slug not in drop]

        for slug in extra:
            if slug not in self.exercises:
                raise NoteError(f"variante : exercice « {slug} » introuvable")
            note = self.exercises[slug]
            kept.append(Prescription.from_note(slug, note, Dose.read(note["dose"], slug)))

        for index, added in enumerate(choice.get("add") or []):
            clean = ADDED_SCHEMA.validate(added, f"add[{index}]")
            kept.append(Prescription.from_note(
                "", clean, Dose.read(clean["dose"], f"add[{index}]")))

        if choice.get("half_dose"):
            kept = [p.but_dose(p.dose.fewer_sets()) for p in kept]

        seen_loads = set()
        for entry in choice.get("loads") or []:
            clean = PRESCRIBED_LOAD_SCHEMA.validate(entry, "loads")
            slug = clean["exercise"]
            if slug in seen_loads or slug not in {p.slug for p in kept}:
                raise NoteError(f"loads : exercice absent ou dupliqué : {slug}")
            seen_loads.add(slug)
            kept = [replace(p, load={"kg": clean["kg"], "count": clean["count"]})
                    if p.slug == slug else p for p in kept]

        gate = choice.get("gate")
        if gate:
            # A gate naming an exercise absent from the session would never fire and
            # nobody would notice: same refusal as for "drop", for the same reason.
            unknown = [slug for slug in (gate.get("drop") or [])
                       if slug not in {p.slug for p in kept}]
            if unknown:
                raise NoteError(f"« gate.drop » : {', '.join(unknown)} n'est pas dans "
                                f"la séance {choice['session']} telle qu'elle est servie")

        return {
            "sid": choice.get("sid") or "",
            "session": choice["session"],
            "title": session["title"],
            "rule": session["rule"],
            "date": choice.get("date", ""),
            "duration_min": choice.get("duration_min") or session.get("duree_min") or 30,
            "note": choice.get("note", ""),
            "gate": gate,
            "exercises": kept,
        }


# ------------------------------------------------------ the RPE target of a kind

#: The keys a workout note uses to say what it describes. A strength routine declares
#: `routine:` (its slug); any other note declares the session `kinds:` it covers.
NOTE_KINDS_SCHEMA = Schema("fiche de séance", [
    field("kinds", list, required=False, item=field("type", str, choices=session_kinds.RECORDED)),
    field("routine", str, required=False, pattern=SLUG_PATTERN),
], extra=True)


def _workout_notes(vault: Path) -> list[tuple[Path, dict]]:
    """Every workout note with what it declares; raises NoteError on a bad declaration."""
    notes, problems = [], []
    for path in sorted((vault / vault_layout.WORKOUTS).glob("*.md")):
        data = frontmatter(path)
        try:
            NOTE_KINDS_SCHEMA.validate(data, path.name)
        except Exception as exc:   # SchemaError names the field
            problems.append(f"{path.name} : {exc}")
            continue
        if "routine" in data and "kinds" in data:
            problems.append(f"{path.name} : une routine est un renfo, elle ne déclare pas kinds")
            continue
        notes.append((path, data))
    if problems:
        raise NoteError("\n".join(problems))
    return notes


def rpe_targets(vault: Path) -> dict[tuple[str, str | None], float]:
    """The RPE target of each (kind, routine), read from the workout notes.

    A kind without a declared `rpe_cible` is absent: the caller compares nothing
    rather than inventing a target.
    """
    targets: dict[tuple[str, str | None], float] = {}
    for _, data in _workout_notes(vault):
        target = data.get("rpe_cible")
        if target is None:
            continue
        if "routine" in data:
            targets[(session_kinds.STRENGTH, data["routine"])] = float(target)
        for kind in data.get("kinds") or ():
            targets[(kind, None)] = float(target)
    return targets


def target_for(targets: dict[tuple[str, str | None], float], kind: str,
               routine: str | None = None) -> float | None:
    return targets.get((kind, routine if kind == session_kinds.STRENGTH else None))


def load(vault: Path) -> Programme:
    """Every exercise and routine note, validated. Raises NoteError listing every
    broken note -- never a partial programme."""
    exercises = read_all(vault / vault_layout.EXERCISES, EXERCISE_SCHEMA)
    sessions = {}
    problems = []
    for path, data in _workout_notes(vault):
        if "routine" not in data:
            continue
        routine = data["routine"]
        if routine in sessions:
            problems.append(f"{path.name} : routine « {routine} » déjà déclarée")
            continue
        gaps = data.get("min_gap_h") or {}
        bad = [f"{kind}: {hours}" for kind, hours in gaps.items()
               if kind not in session_kinds.PLANNED or type(hours) is not int or hours < 1]
        if bad:
            problems.append(f"{path.name} : min_gap_h attend {{type planifié: heures}} "
                            f"({', '.join(bad)})")
            continue
        try:
            sessions[routine] = SESSION_SCHEMA.validate(data, path.name)
        except NoteError as exc:
            problems.append(str(exc))
        except Exception as exc:   # SchemaError carries every field problem
            problems.append(f"{path.name} : {exc}")
    if problems:
        raise NoteError("\n".join(problems))
    return Programme(exercises, sessions)
