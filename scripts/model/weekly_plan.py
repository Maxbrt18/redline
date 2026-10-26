"""Validation et rendu des programmes hebdomadaires canoniques dans PostgreSQL.

Les journaux Obsidian n'en sont que des projections en lecture seule.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from itertools import pairwise

from . import instance as instance_model
from . import paths, rulebook, transitions, typography
from . import programme as strength_programme
from .programme import GATE_SCHEMA as STRENGTH_GATE_SCHEMA
from .schema import Schema, SchemaError, field

WEEK_RE = r"20\d{2}-W(?:0[1-9]|[1-4]\d|5[0-3])"
DATE_RE = r"20\d{2}-\d{2}-\d{2}"
TIME_RE = r"(?:[01]\d|2[0-3]):[0-5]\d"
STAMP_RE = r"20\d{2}-\d{2}-\d{2}T.+"
BLOCK = "WEEK_PLAN"

KINDS = ("run", "strength", "test", "rest", "bike", "hike")
WORKOUTS = ("EF", "SL", "seuil", "cotes", "lignes", "cadence", "course",
            "renfo-a", "renfo-b", "test-mollet", "repos", "velo", "rando")
INTENT_STATUSES = ("planned", "cancelled")
# ``done`` existe dans le read model rendu depuis ``session_cycles`` et dans les
# révisions historiques. Une nouvelle révision ne peut plus l'écrire.
STATUSES = (*INTENT_STATUSES, "done")
# Les états qui SOLDENT une séance : elle a eu lieu, ou elle n'aura pas lieu. Dans les
# deux cas elle appartient au récit de la semaine et plus jamais à la prescription.
# Écrit en négatif (« pas encore soldée ») plutôt qu'en positif (« planned ») pour
# qu'un statut ajouté plus tard reste prescriptible par défaut : oublier une séance à
# faire est pire que d'en afficher une de trop.
SETTLED_STATUSES = frozenset({"done", "cancelled"})
HARD_WORKOUTS = {"SL", "seuil", "cotes", "course"}

# Une note de séance planifiée décrit ce qui devra être fait. Les résultats vivent
# dans l'observation et le cycle, puis le read model ajoute seulement le statut
# ``done``. Ces marqueurs sont volontairement étroits : ils attrapent les comptes
# rendus qui ont déjà essaimé dans les révisions sans interdire une consigne comme
# « se consigne au registre après coup ».
REALIZED_TEXT = re.compile(
    r"\b(?:réalis(?:é|ée|és|ées)|effectu(?:é|ée|és|ées)|fait le|faite le|"
    r"consign(?:é|ée|és|ées)|FC moy|WBGT|difficulté par exercice|"
    r"RPE global|titre Strava)\b",
    re.IGNORECASE,
)
WALK_QUESTION = re.compile(r"\bmarch(?:e|er|ée)\b", re.IGNORECASE)
THIRD_PARTY_TEXT = re.compile(
    r"\b(?:avec|chez|pour|et)\s+"
    r"(?P<name>[A-ZÉÈÀÂÎÔÛ][\wÉÈÊÀÂÎÔÛéèêàâîôûç-]{2,})\b"
)
#: Les mots capitalisés qui ne désignent jamais un tiers. Le prénom et le lieu de
#: l'instance s'y ajoutent à la lecture (`instance.own_names`).
PLAN_NAME_ALLOWLIST = frozenset({"Strava", "RDL", "EF", "FC", "SL"})

STRENGTH_SPEC_SCHEMA = Schema("modulation de renforcement", [
    field("loads", list, required=False, maximum=20,
          item=field("charge", dict, schema=strength_programme.PRESCRIBED_LOAD_SCHEMA)),
    field("variant", str, required=False,
          note="une variante déclarée par la fiche de séance"),
    field("half_dose", bool, required=False),
    field("drop", list, required=False, item=field("slug", str)),
    field("note", str, required=False, maximum=300),
    field("duration_min", int, required=False, minimum=5, maximum=120),
    field("gate", dict, required=False, schema=STRENGTH_GATE_SCHEMA),
])

SESSION_SCHEMA = Schema("séance planifiée", [
    field("id", str, pattern=r"[a-z0-9][a-z0-9-]+"),
    # Human-facing text is part of the persisted transaction.  Renderers must
    # never maintain a second, incomplete taxonomy of workout labels.
    field("title", str, minimum=1),
    field("date", str, pattern=DATE_RE, note="AAAA-MM-JJ"),
    field("start_time", str, required=False, pattern=TIME_RE,
          note="HH:MM, heure locale Europe/Paris ; absent si le créneau n'est pas fixé"),
    field("kind", str, choices=KINDS),
    field("workout", str, choices=WORKOUTS),
    field("status", str, choices=STATUSES),
    field("distance_target_km", float, required=False, minimum=0),
    field("distance_cap_km", float, required=False, minimum=0),
    field("duration_cap_min", int, required=False, minimum=1),
    field("rpe_min", int, required=False, minimum=0, maximum=10),
    field("rpe_max", int, required=False, minimum=0, maximum=10),
    field("hr_zone", str, required=False),
    field("hr_cap_bpm", int, required=False, minimum=80, maximum=220,
          note="plafond de FC MOYENNE de la séance, en bpm : le chiffre que hr_zone "
               "annonce en texte ; obligatoire à l'écriture avec hr_zone"),
    field("cadence_target", int, required=False, minimum=120, maximum=220),
    field("demanding", bool),
    field("transition", str, required=False, choices=transitions.KINDS,
          note="nature qui sort des 30 derniers jours (garde-fou n°8) : une par "
               "semaine, sans fenêtre ; absent pour une séance déjà connue"),
    field("location_label", str, required=False),
    field("carbs_target_g_h", int, required=False, minimum=0, maximum=150,
          note="cible digestive expérimentale en grammes de glucides par heure"),
    field("strength", dict, required=False, schema=STRENGTH_SPEC_SCHEMA,
          note="modulations typées ; la séance et les exercices viennent du workout"),
    field("note", str, required=False),
    field("sources", list, minimum=1, item=field("source", str)),
])

GATE_WHEN = ("avant", "apres")


# A dated condition is typed so it cannot be prescribed around once due.
#
# ⭐ `when` EST LA QUESTION CENTRALE, et elle vaut d'être posée à l'écriture (2026-08-18).
# Un contrôle daté sait deux choses très différentes :
#
#   avant  -- une PRÉ-CONDITION. Sa réponse existe avant les séances qu'il retient, et
#             tant qu'elle manque, ces séances ne se prescrivent pas.
#   apres  -- une OBSERVATION. Sa réponse n'existe qu'une fois la séance faite. Elle
#             informe la suite ; elle ne retient rien, parce qu'il n'y a rien qu'elle
#             PUISSE retenir sans se mordre la queue.
#
# Le 2026-08-18 le programme portait « après le bloc de pliométrie de la séance A, la
# cheville a-t-elle parlé ? », échéance le jour de cette séance A. Une observation, donc,
# écrite dans le seul moule qui existait : celui d'une pré-condition. Le service a fait ce
# qu'on lui demandait, il a retenu la prescription -- en attendant la réponse à une
# question qui exigeait de faire la séance qu'il retenait. L'athlète a reçu un message qui
# nommait son renfo, posait la question, et ne livrait pas la carte.
#
# Un contrôle doit trancher, et « avant » se paie d'un `holds` qui NOMME les séances
# retenues. Les anciennes lignes ont été normalisées une fois en base : le modèle
# courant n'a donc aucun dialecte historique à interpréter.
GATE_SCHEMA = Schema("contrôle daté", [
    field("id", str, pattern=r"[a-z0-9][a-z0-9-]+"),
    field("due", str, pattern=DATE_RE, note="AAAA-MM-JJ"),
    field("when", str, choices=GATE_WHEN,
          note="avant = pré-condition, retient les séances de `holds` ; "
               "apres = observation, ne retient rien"),
    field("holds", list, required=False, item=field("séance", str),
          note="les identifiants de séance que ce contrôle retient — "
               "obligatoire si when=avant, interdit si when=apres"),
    field("question", str, minimum=10, note="ce qu'il faut demander à l'athlète ce jour-là"),
    field("on_fail", str, minimum=3, note="ce que devient le programme si la réponse est mauvaise"),
])

CHANGE_FIELDS = (
    "added", "removed", "title", "date", "start_time", "workout", "status",
    "distance_target_km", "distance_cap_km", "duration_cap_min", "rpe_min",
    "rpe_max", "hr_zone", "cadence_target", "demanding", "transition",
    # `calibration` n'existe plus depuis le 2026-10-09 ; le nom reste lisible dans les
    # `changes` des révisions déjà écrites, qui sont relues et validées en entier.
    "calibration",
    "carbs_target_g_h", "location_label", "strength", "note",
)

#: ⭐ UNE RESTRICTION NOMME SA PRÉMISSE EN TYPE (audit du 2026-10-09). L'erreur du coach
#: qui touche le plus souvent une prescription est la restriction sans prémisse : 19
#: incidents contre 9 erreurs permissives, à raison de deux par semaine malgré des
#: rappels écrits partout en prose. Sept passaient par une révision (renfo A retiré le
#: 11/08, EF retirée sur « intermittent » le 27/08, RDL et fréquence le 14/09…). Une
#: `rule` en texte libre ne se vérifie pas ; un identifiant du règlement et un fait de
#: `evidence`, si. `demande` couvre ce que l'athlète a lui-même demandé.
MOTIF_RULES = (*(rule.id for rule in rulebook.RULES), "demande")
#: Les préfixes d'un FAIT : ce que le registre, Strava, un avis ou un contrôle ont dit.
#: Une règle du règlement ne s'applique qu'à un fait ; une demande vient d'un message.
_FACT_TRIGGER = re.compile(r"^(?:observation|exposure|medical|gate|incident|strength|strava):")
_REQUEST_TRIGGER = re.compile(r"^(?:messenger|request|availability):")

MOTIF_SCHEMA = Schema("motif d'une restriction", [
    field("regle", str, choices=MOTIF_RULES,
          note="l'identifiant du règlement qui impose la restriction, ou demande"),
    field("fait", str, minimum=3,
          note="une entrée exacte de evidence : le fait qui déclenche la règle"),
])

CHANGE_SCHEMA = Schema("preuve d'un changement de séance", [
    field("session_id", str, pattern=r"[a-z0-9][a-z0-9-]+"),
    field("fields", list, minimum=1,
          item=field("champ", str, choices=CHANGE_FIELDS)),
    field("trigger", str, minimum=3,
          note="une entrée exacte de evidence qui déclenche ce changement"),
    field("context", str, minimum=3,
          note="le contexte ou l'intention confirmée qui donne son sens à la preuve"),
    field("rule", str, minimum=3,
          note="la règle de coaching appliquée à cette preuve"),
    # Optionnel à la lecture : les révisions d'avant le 2026-10-10 n'en portent pas.
    # Exigé à l'écriture sur toute restriction (`change_evidence_errors`).
    field("motif", dict, required=False, schema=MOTIF_SCHEMA,
          note="obligatoire si la séance est retirée, annulée ou réduite"),
])

REVISION_SCHEMA = Schema("révision hebdomadaire", [
    field("revision", int, minimum=1),
    field("computed_at", str, pattern=STAMP_RE, note="date-heure ISO 8601"),
    field("reason", str, minimum=3),
    field("evidence", list, minimum=1, item=field("preuve", str)),
    field("constraints", list, item=field("contrainte", str)),
    # Optionnel pour relire l'historique ; obligatoire à la frontière `revise` dès
    # qu'une prescription future change (voir `change_evidence_errors`).
    field("changes", list, required=False,
          item=field("changement", dict, schema=CHANGE_SCHEMA)),
    # Optional: the revisions written before 2026-08-08 carry none, and a week
    # that conditions nothing on a date is the normal case.
    field("gates", list, required=False,
          item=field("contrôle", dict, schema=GATE_SCHEMA)),
    # Optionnel à la lecture : les révisions d'avant le 2026-10-07 n'en portent pas.
    # Exigé à l'écriture dès que la semaine court (`typed_target_errors`).
    field("volume_target_km", float, required=False, minimum=1, maximum=200,
          note="volume couru visé sur la semaine entière, en km"),
    field("sessions", list, minimum=1,
          item=field("session", dict, schema=SESSION_SCHEMA)),
])

WEEKLY_PLAN_SCHEMA = Schema("plan hebdomadaire", [
    field("week", str, pattern=WEEK_RE, note="AAAA-WNN"),
    field("revisions", list, minimum=1,
          item=field("revision", dict, schema=REVISION_SCHEMA)),
])


def now_stamp() -> str:
    """L'instant réel de l'écriture, en heure locale et avec son décalage.

    ⭐ Extrait ici (2026-08-25) pour que ce soit UNE fonction, patchable en test et
    citée par un seul appelant : `week-plan.py revise`. Le champ `computed_at` était
    rempli par le modèle, qui n'a pas d'horloge -- voir le commentaire de `revise`.
    """
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def current_week(today: dt.date | None = None) -> str:
    iso = (today or dt.date.today()).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def next_week(today: dt.date | None = None) -> str:
    day = (today or dt.date.today()) + dt.timedelta(days=7)
    return current_week(day)


def _week_dates(week: str) -> set[str]:
    year, number = week.split("-W")
    monday = dt.date.fromisocalendar(int(year), int(number), 1)
    return {(monday + dt.timedelta(days=i)).isoformat() for i in range(7)}


# `title` et `note` sont les DEUX seuls champs de la révision qui partent tels quels
# chez l'athlète -- texto du matin et carte du programme. `reason`, `evidence` et
# `constraints` sont l'archive, personne ne les lui envoie. Le 2026-08-11 la frontière
# n'existait pas : un replan lui a expédié « Renfo A reporté : ... (trigger replan de
# rev.1) ... (gate w33-renfoa-report) » et la moitié de la semaine désaccentuée. Un
# schéma qui accepte n'importe quelle chaîne dans un champ qu'il PUBLIE ne protège rien.
#
# Append-only : les révisions déjà écrites ne peuvent plus être réparées. La règle ne
# juge donc que ce qui s'écrit à partir de cet instant -- sans quoi W33 r2, écrite le
# matin même du correctif, deviendrait invalide et bloquerait toute prescription
# jusqu'à W34. Comparaison de chaînes ISO, comme le reste du module (`gate["due"] <
# written_on`) : tous les `computed_at` du vault portent le même décalage local.
_INTERNAL_RE = re.compile(r"\b(?:rev|r[ée]vision)\.?\s*\d", re.IGNORECASE)


def _text_errors(text: str, where: str, label: str, gate_ids: list[str]) -> list[str]:
    """`title`/`note` : ce que l'athlète lit, jugé comme tel."""
    problems = []
    for gate_id in gate_ids:
        if gate_id in text:
            problems.append(
                f"{where} : {label} cite le contrôle daté « {gate_id} » — ce texte part "
                "sur le téléphone de l'athlète et sur la carte, la machinerie interne "
                "reste dans reason/evidence")
    match = _INTERNAL_RE.search(text)
    if match:
        problems.append(
            f"{where} : {label} cite une révision (« {match.group()} ») — dis ce que la "
            "séance devient, pas d'où la consigne vient")
    return problems


def written_text_errors(revision: dict, where: str = "revision") -> list[str]:
    """Les signes interdits dans le texte D'UNE révision qu'on s'apprête à écrire.

    ⭐ Appelée par `week-plan.py revise` sur la révision NEUVE, jamais par `validate()`.
    L'historique est append-only et `revise` valide le plan entier : la même règle posée
    dans `semantic_errors` refuserait la révision du jour à cause du texte de la veille,
    donc bloquerait le coach sur une faute devenue incorrigible. Ici, seul ce qui entre
    est jugé, et le programme se nettoie au rythme où il se révise.

    Tous les champs de prose y passent, pas seulement `title`/`note` : `as_json()` rend
    la révision ACTIVE ENTIÈRE, donc `reason`, `evidence`, `constraints` et le texte des
    contrôles datés sont réinjectés au coach à chaque tour prescriptif au même titre.
    """
    problems = []
    for label in ("reason",):
        if revision.get(label):
            problems += typography.errors(revision[label], where, label)
    for label in ("evidence", "constraints"):
        for index, item in enumerate(revision.get(label) or []):
            problems += typography.errors(item, where, f"{label}[{index}]")
    for gate in revision.get("gates") or []:
        for label in ("question", "on_fail"):
            if gate.get(label):
                problems += typography.errors(
                    gate[label], f"{where}.gate[{gate.get('id', '?')}]", label)
        # Décision de l'athlète du 2026-10-11 : la marche ne se demande jamais, il signale
        # lui-même une gêne. 29 questions en trois semaines, aucune réponse « modifiée ».
        # Décision du 2026-10-11 : une pré-condition ne retient plus de séance. 19
        # contrôles bloquants en deux mois, aucun échec sur son critère ; 23 changements
        # de séance sur 25 venaient d'un message de l'athlète. La condition s'écrit dans la
        # séance, et l'alerte « nette » du carnet la déclenche.
        if gate.get("when") == "avant":
            problems.append(
                f"{where}.gate[{gate.get('id', '?')}] : une pré-condition ne retient plus de "
                "séance. Écris la condition dans la note de la séance (si la trace est nette "
                "ce matin : repos) ; le carnet alerte sur une trace nette. Un contrôle sert "
                "seulement à une observation (when=apres).")
        if WALK_QUESTION.search(gate.get("question") or ""):
            problems.append(
                f"{where}.gate[{gate.get('id', '?')}] : question sur la marche. Elle ne se "
                "demande jamais ; demande seulement la trace au réveil.")
    for session in revision.get("sessions") or []:
        for label in ("title", "note"):
            if session.get(label):
                problems += typography.errors(
                    session[label], f"{where}.{session.get('id', '?')}", label)
    return problems


def intention_errors(revision: dict, where: str = "revision") -> list[str]:
    """Refuse de recopier l'exécution dans une révision neuve.

    Le schéma de lecture garde ``done`` pour les plans historiques et les vues
    enrichies depuis les cycles. La frontière d'écriture est volontairement plus
    étroite : une révision dit ce qui est prévu ou annulé, jamais ce qui a eu lieu.
    """
    allowed = PLAN_NAME_ALLOWLIST | instance_model.own_names(paths.vault_root())
    problems = [
        f"{where}.{session.get('id', '?')} : status=done appartient au cycle de "
        "séance. Garde status=planned ; le rendu ajoute 'fait' depuis PostgreSQL."
        for session in revision.get("sessions") or []
        if session.get("status") not in INTENT_STATUSES
    ]
    for session in revision.get("sessions") or []:
        for label in ("title", "note"):
            text = session.get(label) or ""
            if REALIZED_TEXT.search(text):
                problems.append(
                    f"{where}.{session.get('id', '?')}.{label} : résultat recopié dans "
                    "le plan. Garde seulement l'intention ; l'observation et le cycle "
                    "portent ce qui a eu lieu.")
            for match in THIRD_PARTY_TEXT.finditer(text):
                if (match.group("name") not in allowed
                        and not re.fullmatch(r"W\d{2}", match.group("name"))):
                    problems.append(
                        f"{where}.{session.get('id', '?')}.{label} : nom de tiers "
                        f"{match.group('name')!r} dans le plan. Garde la contrainte utile "
                        "sans identité personnelle.")
        strength_note = (session.get("strength") or {}).get("note") or ""
        for match in THIRD_PARTY_TEXT.finditer(strength_note):
            if (match.group("name") not in allowed
                    and not re.fullmatch(r"W\d{2}", match.group("name"))):
                problems.append(
                    f"{where}.{session.get('id', '?')}.strength.note : nom de tiers "
                    f"{match.group('name')!r} dans le plan.")
    for index, constraint in enumerate(revision.get("constraints") or []):
        if REALIZED_TEXT.search(constraint):
            problems.append(
                f"{where}.constraints[{index}] : résultat recopié comme contrainte. "
                "Référence l'observation dans evidence et garde ici l'effet sur la suite.")
    for label, values in (("reason", [revision.get("reason") or ""]),
                          ("evidence", revision.get("evidence") or []),
                          ("constraints", revision.get("constraints") or [])):
        for index, text in enumerate(values):
            for match in THIRD_PARTY_TEXT.finditer(text):
                if (match.group("name") not in allowed
                        and not re.fullmatch(r"W\d{2}", match.group("name"))):
                    problems.append(
                        f"{where}.{label}[{index}] : nom de tiers "
                        f"{match.group('name')!r} dans le plan.")
    return problems


def typed_target_errors(revision: dict, where: str = "revision") -> list[str]:
    """Le volume visé et le plafond de FC se typent, comme une date à venir (2026-10-07).

    « Environ 41 km sur cinq sorties » vivait dans `constraints`, donc nulle part pour
    le code : le carnet ne pouvait pas afficher « 14,1 / ~41 km » sans lire de la
    prose. Une révision neuve qui prescrit de la course le donne en chiffre ; le
    registre reste seul à dire ce qui a été couru.
    """
    runs = [session for session in revision.get("sessions") or []
            if session.get("kind") == "run" and session.get("status") != "cancelled"]
    problems = []
    if runs and revision.get("volume_target_km") is None:
        problems.append(f"{where}.volume_target_km : manquant — le volume couru visé de "
                        "la semaine entière, en km (séances déjà faites comprises), celui "
                        "que les contraintes annoncent en prose")
    # Même raison pour la FC : « Z1-Z2, 130-157 bpm » se lit, il ne se compare pas.
    # Le carnet met le fait (FC moyenne Strava) en regard de ce plafond. Il reste hors
    # de CHANGE_FIELDS : il double `hr_zone`, dont tout changement exige déjà sa preuve,
    # et le premier ajout sur une séance existante n'est pas une nouvelle prescription.
    problems.extend(
        f"{where}.{session.get('id', '?')}.hr_cap_bpm : manquant — le plafond de FC "
        f"MOYENNE en bpm que hr_zone « {session['hr_zone']} » annonce en texte"
        for session in revision.get("sessions") or []
        if session.get("status") != "cancelled" and session.get("hr_zone")
        and session.get("hr_cap_bpm") is None)
    return problems


def _session_errors(session: dict, where: str) -> list[str]:
    problems = []
    target, cap = session.get("distance_target_km"), session.get("distance_cap_km")
    if target is not None and cap is not None and target > cap:
        problems.append(f"{where} : cible {target:g} km au-dessus du plafond {cap:g} km")
    lo, hi = session.get("rpe_min"), session.get("rpe_max")
    if lo is not None and hi is not None and lo > hi:
        problems.append(f"{where} : RPE minimum {lo} au-dessus du maximum {hi}")
    workout = session.get("workout")
    duration = session.get("duration_cap_min", 0)
    expected_hard = workout in HARD_WORKOUTS or (workout == "EF" and duration > 75)
    if session.get("demanding") != expected_hard:
        problems.append(
            f"{where} : demanding doit valoir {str(expected_hard).lower()} pour {workout}")
    if session.get("kind") == "run" and target is None and cap is None and duration == 0:
        problems.append(f"{where} : une course exige une cible, un plafond ou une durée")
    if session.get("transition") and session.get("kind") == "rest":
        problems.append(f"{where} : un repos n'est pas une transition")
    if session.get("carbs_target_g_h") is not None and workout not in {"SL", "course"}:
        problems.append(f"{where} : une cible digestive ne se teste que sur SL ou course")
    strength = session.get("strength")
    is_strength = workout in {"renfo-a", "renfo-b"}
    if strength is not None and not is_strength:
        problems.append(f"{where} : strength ne vaut que pour renfo-a ou renfo-b")
    if is_strength and strength is None:
        problems.append(
            f"{where} : strength manque. Même sans modulation, écris strength={{}} "
            "pour figer la prescription de carte")
    return problems


def _strength_programme_errors(session: dict, where: str) -> list[str]:
    """Résout une prescription de renfo contre son vrai programme.

    Le schéma prouve la forme de ``strength`` ; seul ``Programme.build`` peut prouver
    que sa variante et ses exercices existent. On ne valide que la révision active :
    l'historique append-only peut contenir une ancienne erreur devenue irréparable,
    mais une nouvelle révision ne doit jamais l'activer.
    """
    workout = session.get("workout")
    if workout not in {"renfo-a", "renfo-b"}:
        return []
    selection = {
        "session": {"renfo-a": "a", "renfo-b": "b"}[workout],
        "date": session["date"],
        "sid": session["id"],
        **(session.get("strength") or {}),
    }
    try:
        strength_programme.load(paths.vault_root()).build(selection)
    except Exception as exc:
        return [f"{where}.strength : prescription impossible — {exc}"]
    return []


def _gate_typing_errors(gate: dict, where: str, sessions: list[dict]) -> list[str]:
    """`when` et `holds` : ce qu'un contrôle daté a le droit de retenir.

    `when` est obligatoire. « Avant » se paie d'un `holds` qui nomme les séances
    retenues -- écrire « je retiens la séance A » juste à côté d'une question qui
    demande comment la séance A s'est passée est une contradiction visible.
    """
    problems = []
    when = gate["when"]
    holds = gate.get("holds")

    if when == "apres" and holds:
        problems.append(
            f"{where} : when=apres avec `holds` — une observation ne retient rien. Sa "
            "réponse arrive APRÈS les séances, elle ne peut pas être leur condition.")
    if when == "avant" and not holds:
        problems.append(
            f"{where} : when=avant sans `holds` — nomme les séances que ce contrôle "
            "retient. Un contrôle qui retient « tout » retient aussi celle dont il "
            "attend la réponse.")

    known = {session["id"]: session["date"] for session in sessions}
    for session_id in holds or []:
        if session_id not in known:
            problems.append(
                f"{where} : `holds` cite « {session_id} », qui n'est pas une séance de "
                "cette révision")
            continue
        # Retenir une séance déjà passée au moment où la question se pose n'a aucun
        # effet : elle a eu lieu. C'est le signe qu'on voulait une observation.
        if known[session_id] < gate["due"]:
            problems.append(
                f"{where} : retient « {session_id} » du {known[session_id]}, antérieure "
                f"à l'échéance {gate['due']} — cette séance aura déjà eu lieu. Une "
                "condition qui arrive après sa séance est une observation (when=apres).")
    return problems


def semantic_errors(plan: dict, where: str = "weekly_plan",
                    today: dt.date | None = None) -> list[str]:
    """Cross-revision and coaching invariants not expressible field by field."""
    problems = WEEKLY_PLAN_SCHEMA.errors(plan, where)
    if problems:
        return problems
    week = plan["week"]
    allowed = _week_dates(week)
    # Le renfo se résout contre le programme du jour : c'est une garde de prescription.
    # Une semaine close n'a plus rien à prescrire, et le programme a pu changer depuis
    # (fusion A/B du 2026-10-04) -- la juger ainsi rendrait son archive illisible.
    prescribable = dt.date.fromisocalendar(int(week[:4]), int(week[-2:]), 7) \
        >= (today or dt.date.today())
    revisions = plan["revisions"]
    numbers = [revision["revision"] for revision in revisions]
    if numbers != list(range(1, len(numbers) + 1)):
        problems.append(f"{where} : révisions non contiguës ({numbers})")
    for revision in revisions:
        prefix = f"{where}.revision[{revision['revision']}]"
        gates = revision.get("gates") or []
        gate_ids = [gate["id"] for gate in gates]
        written_on = revision["computed_at"][:10]
        sessions = revision["sessions"]
        ids = [session["id"] for session in sessions]
        if len(ids) != len(set(ids)):
            problems.append(f"{prefix} : identifiants de séance dupliqués")
        problems.extend(transitions.per_week_errors(sessions, prefix))
        for session in sessions:
            if session["date"] not in allowed:
                problems.append(
                    f"{prefix}.{session['id']} : date {session['date']} hors de {week}")
            problems.extend(_session_errors(session, f"{prefix}.{session['id']}"))
            if revision is revisions[-1] and prescribable:
                problems.extend(_strength_programme_errors(
                    session, f"{prefix}.{session['id']}"))
            for label in ("title", "note"):
                if session.get(label):
                    problems.extend(_text_errors(
                        session[label], f"{prefix}.{session['id']}", label, gate_ids))

        if len(gate_ids) != len(set(gate_ids)):
            problems.append(f"{prefix} : identifiants de contrôle daté dupliqués")
        for gate in gates:
            where_gate = f"{prefix}.gate[{gate['id']}]"
            # A gate belongs to the week that carries it.  A condition landing
            # past Sunday has a different owner -- `redline.deadlines`, whose overdue
            # check already exists -- otherwise it would sit in a plan
            # nothing looks at once the week is over.
            if gate["due"] not in allowed:
                problems.append(
                    f"{where_gate} : échéance {gate['due']} hors de {week} — "
                    "une condition au-delà du dimanche va dans PostgreSQL via "
                    "scripts/fact-write.py deadline")
            if gate["due"] < written_on:
                problems.append(
                    f"{where_gate} : échéance {gate['due']} antérieure au calcul "
                    f"de la révision ({written_on})")
            problems.extend(_gate_typing_errors(gate, where_gate, sessions))

        hard_days = sorted({dt.date.fromisoformat(s["date"]) for s in sessions
                            if s["status"] != "cancelled" and s["demanding"]})
        for first, second in pairwise(hard_days):
            if (second - first).days == 1:
                problems.append(
                    f"{prefix} : jours exigeants consécutifs {first} et {second}")

        active = [s for s in sessions if s["status"] != "cancelled"]
        strength_a = [dt.date.fromisoformat(s["date"]) for s in active
                      if s["workout"] == "renfo-a"]
        strength_b = [dt.date.fromisoformat(s["date"]) for s in active
                      if s["workout"] == "renfo-b"]
        for a in strength_a:
            for b in strength_b:
                if abs((a - b).days) < 2:
                    problems.append(f"{prefix} : renfo A et B espacés de moins de 48 h")
        long_runs = [dt.date.fromisoformat(s["date"]) for s in active
                     if s["workout"] == "SL"]
        for b in strength_b:
            for long_run in long_runs:
                if abs((b - long_run).days) < 2:
                    problems.append(f"{prefix} : renfo B à moins de 48 h d'une SL")
    return problems


def validate(plan: dict, where: str = "weekly_plan",
             today: dt.date | None = None) -> dict:
    problems = semantic_errors(plan, where, today)
    if problems:
        raise SchemaError(problems)
    return plan


def active(plan: dict) -> dict:
    validate(plan)
    return plan["revisions"][-1]


def scheduled(sessions: list[dict]) -> list[dict]:
    """Ce qui figure au programme : tout sauf ce qui a été annulé.

    Vue DESCRIPTIVE — elle raconte la semaine, séances faites comprises. C'est celle
    du journal, de la carte et du hot-cache.
    """
    return [session for session in sessions if session["status"] != "cancelled"]


def remaining(sessions: list[dict], *, since: str = "") -> list[dict]:
    """Ce qu'il RESTE à faire, à partir de `since` : la vue PRESCRIPTIVE.

    ⭐ Une séance faite ou annulée n'engage plus rien, et cette phrase n'a le droit
    d'exister qu'une fois. Le 2026-08-16, l'athlète finit sa SL vallonnée à 9 h, le coach
    la débriefe correctement, et le bloc canonique posté juste après la lui redonne à
    faire : « la suite de la semaine — dim 16/08 : SL vallonnée, cible 10 km ». La
    donnée était juste (r11 l'avait passée à `done` à l'arrivée du fichier Strava), et
    les deux autres rendus du même objet lisaient bien `status` — la carte affichait
    « · fait », le journal « (fait) ». Seul le rendu Messenger, le seul que l'athlète lise
    vraiment et le seul à affirmer « la suite », filtrait sur la DATE et sur elle seule.

    Un objet, trois rendus, trois filtres réécrits à la main : c'est le rendu le plus
    affirmatif qui en savait le moins. D'où cette fonction, et la règle qui va avec :
    un rendu ne redéfinit jamais ce qu'est une séance à faire, il le demande ici.

    `since` est une borne INCLUSIVE : la journée en cours reste prescriptible tant que
    sa séance n'est pas faite.
    """
    return [session for session in sessions
            if session["status"] not in SETTLED_STATUSES
            and session.get("date", "") >= since]


def due_gates(plan: dict, today: dt.date | None = None) -> list[dict]:
    """Les contrôles datés dont le jour est venu, des DEUX sortes.

    Ce sont les questions à poser aujourd'hui, et rien d'autre : depuis le 2026-08-18,
    c'est `blocking_gates` qui dit lesquelles retiennent une prescription. Confondre
    les deux revient à fermer une porte chaque fois qu'on ouvre la bouche.

    Une réponse ne s'obtient qu'en POSANT la question : un contrôle échu est donc
    l'état normal du matin où il tombe, jamais une avarie.

    La confondre avec un programme cassé a tué le mot du matin du 2026-08-12 : le
    contrôle injury du jour rendait `check` non nul, la persistance refusait le tour,
    et le message de coach qui posait précisément la question du contrôle était jeté
    au profit d'un « le programme n'a pas passé ses contrôles ». Un mécanisme fait
    pour produire une question produisait une panne.
    """
    return due_gates_of(active(plan), today)


def due_gates_of(revision: dict, today: dt.date | None = None) -> list[dict]:
    """Les contrôles échus d'UNE révision, quand on l'a déjà sous la main.

    Le service tient la révision active sous forme de JSON -- `weekly_plan_context` a
    payé le `show` -- et n'a donc pas de plan à donner à `due_gates`. Il portait pour
    ça sa propre comparaison de dates, et sa propre partition par `when` : deux copies
    de ce que ce module déclare, exactement de part et d'autre de la frontière où le
    désaccord du 2026-08-18 s'est produit.
    """
    as_of = today or dt.date.today()
    return [gate for gate in (revision.get("gates") or [])
            if isinstance(gate, dict)
            and dt.date.fromisoformat(gate["due"]) <= as_of]


def split_by_when(gates: list[dict]) -> tuple[list[dict], list[dict]]:
    """(pré-conditions, observations) — deux consignes, jamais un seul paquet.

    Les fondre est ce qui a produit le message du 2026-08-18 : une observation portait
    l'interdiction de prescrire d'une pré-condition, le coach a posé la question ET
    retenu la carte de la séance sur laquelle il la posait.
    """
    return ([gate for gate in gates if gate["when"] == "avant"],
            [gate for gate in gates if gate["when"] == "apres"])


def blocking_gates(plan: dict, today: dt.date | None = None) -> list[dict]:
    """Les contrôles échus qui RETIENNENT réellement une prescription.

    ⭐ 2026-08-18 : `due_gates` rendait les deux sortes, et tout le service traitait le
    résultat comme un blocage. Une observation -- « comment la cheville a réagi pendant
    la séance » -- gelait donc la semaine en attendant une réponse qui exigeait de faire
    la séance gelée. C'est cette fonction, et non `due_gates`, que consultent la
    validation et la persistance : une question qu'on pose n'est pas une porte qu'on
    ferme.
    """
    return split_by_when(due_gates(plan, today))[0]


def gate_reasons(plan: dict, today: dt.date | None = None) -> list[str]:
    """Le texte des contrôles échus, tel qu'il est servi au coach."""
    return [f"contrôle daté du {gate['due']} non levé — DEMANDE À SAM AVANT "
            f"TOUTE PRESCRIPTION : {gate['question']} ; à défaut : {gate['on_fail']}. "
            "Le contrôle ne se lève qu'en enregistrant une révision dont le motif "
            "porte SA réponse, jamais en le retirant du programme."
            for gate in blocking_gates(plan, today)]


def as_json(plan: dict) -> str:
    return json.dumps(active(plan), ensure_ascii=False, indent=2)


WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def distance_of(session: dict) -> str:
    """Ce qui ENGAGE en distance ou en durée, dans l'ordre où ça se lit.

    ⭐ SOURCE UNIQUE (2026-08-18). Trois rendus du même objet en portaient chacun leur
    version : le texto Messenger (`redline/vault.py`), le journal et le hot-cache (ici),
    la carte PNG (`chart-plan.py`). Trois arbres, trois copies, et rien qui les tienne
    ensemble -- la même famille que le filtre « ce qui reste à faire », que `remaining()`
    a dû unifier après que le seul rendu affirmant « la suite de la semaine » se soit
    trouvé être le moins bien informé des trois.

    Un champ ajouté à `SESSION_SCHEMA` demandait trois éditions dans trois arbres. Il
    en demande une, ici, et les trois rendus la reçoivent.
    """
    target, cap = session.get("distance_target_km"), session.get("distance_cap_km")
    if target is not None and cap is not None:
        return f"cible {target:g} km, plafond {cap:g} km"
    if cap is not None:
        return f"plafond {cap:g} km"
    if target is not None:
        return f"cible {target:g} km"
    if session.get("duration_cap_min"):
        return f"plafond {session['duration_cap_min']} min"
    return ""


def details_of(session: dict, *, cadence_unit: str = "") -> list[str]:
    """Tout ce qui engage sur une séance, dans l'ordre, sans son titre ni son statut.

    L'ordre fait partie du contrat : distance, RPE, zone FC, cadence, note. Chaque rendu
    ne décide plus que de son séparateur et de son balisage -- et `cadence_unit` est la
    seule différence réelle entre les trois, le journal nommant l'unité que le texto et
    la carte laissent implicite.
    """
    details = [distance_of(session)]
    if session.get("rpe_min") is not None:
        high = session.get("rpe_max", session["rpe_min"])
        details.append(f"RPE {session['rpe_min']}-{high}")
    if session.get("hr_zone"):
        details.append(session["hr_zone"])
    if session.get("cadence_target"):
        details.append(f"cadence ~{session['cadence_target']}{cadence_unit}")
    if session.get("carbs_target_g_h") is not None:
        details.append(f"glucides {session['carbs_target_g_h']} g/h")
    if session.get("note"):
        details.append(session["note"])
    return [part for part in details if part]


def _session_line(session: dict) -> str:
    day = dt.date.fromisoformat(session["date"])
    label = WEEKDAYS[day.weekday()].capitalize()
    suffix = " · ".join(details_of(session, cadence_unit=" pas/min"))
    status = "fait" if session["status"] == "done" else session["status"]
    if session.get("transition"):
        status += ", transition " + transitions.LABELS[session["transition"]]
    return f"- **{label} {day:%d/%m}** : {session['title']} ({status})" + (f" · {suffix}" if suffix else "")


def render_journal(plan: dict) -> str:
    revision = active(plan)
    lines = [f"## Programme actif — révision {revision['revision']}", "",
             f"Calculé le `{revision['computed_at']}` · Motif : {revision['reason']}", ""]
    if revision.get("volume_target_km") is not None:
        lines += [f"Volume couru visé : ~{revision['volume_target_km']:g} km", ""]
    lines.extend(_session_line(session) for session in revision["sessions"])
    gates = revision.get("gates") or []
    # ⭐ Le journal disait de TOUS les contrôles qu'ils refusent le programme. Depuis le
    # 2026-08-18 c'est faux de la moitié d'entre eux : une observation (`apres`) se pose
    # et ne retient rien. La projection que l'athlète lit affirmait donc le contraire du
    # code qui décide -- et c'est précisément la confusion qui a coûté le mot du matin
    # du 18/08. Une vue qui ment sur la règle est pire qu'une vue absente.
    for kind, titre in (
        ("avant", "**Pré-conditions** (aucune séance retenue ne se prescrit tant "
                  "qu'une révision ne porte pas la réponse) :"),
        ("apres", "**Observations** (à poser le jour dit ; elles ne retiennent "
                  "aucune séance) :"),
    ):
        due = [gate for gate in gates if gate["when"] == kind]
        if not due:
            continue
        lines += ["", titre, ""]
        lines.extend(f"- **{gate['due'][8:]}/{gate['due'][5:7]}** : {gate['question']} "
                     f"→ à défaut, {gate['on_fail']}" for gate in due)
    attention = revision.get("constraints") or []
    if attention:
        lines += ["", "**Points d'attention** : " + " · ".join(attention)]
    lines += ["", "Ce bloc est généré depuis le programme typé canonique. Ne pas l'éditer à la main."]
    return "\n".join(lines)


MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre")


def journal_skeleton(week: str, today: dt.date) -> str:
    """Le journal d'une semaine qui a un programme mais pas encore de fichier.

    Né le 2026-09-27 : W36 et W39 n'avaient pas de journal. Sa création était une étape
    confiée au modèle dans le bilan (« crée le journal de la semaine suivante »), sautée
    deux fois sur quatre, et le projecteur n'écrit que dans un fichier qui existe : le
    programme était canonique en base et invisible dans Obsidian, pendant que le
    hot-cache renvoyait à un chemin absent. La structure suit les journaux existants ;
    le bloc de programme est rempli juste après par la projection.
    """
    year, number = week.split("-W")
    monday = dt.date.fromisocalendar(int(year), int(number), 1)
    sunday = monday + dt.timedelta(days=6)
    span = (f"{monday.day}-{sunday.day} {MONTHS[sunday.month - 1]}"
            if monday.month == sunday.month else
            f"{monday.day} {MONTHS[monday.month - 1]} - {sunday.day} {MONTHS[sunday.month - 1]}")
    return "\n".join([
        "---", "type: bilan", f"semaine: {week}", f"maj: {today.isoformat()}", "---", "",
        f"# Bilan {week} ({span})", "",
        "> Les séances restent dans le registre. Le programme opérationnel est la révision "
        "PostgreSQL",
        f"> `{week}` ; ce journal recevra la lecture de fin de semaine.", "",
        f"<!-- {BLOCK}_START -->", f"<!-- {BLOCK}_END -->", "",
        "## Intention d'ouverture", "",
        "## Ressenti de l'athlète", "",
        "## Bilan de clôture", "",
        "## Lecture du coach", ""])


def render_hot_cache(plan: dict) -> str:
    revision = active(plan)
    sessions = scheduled(revision["sessions"])
    compact = "; ".join(
        f"{WEEKDAYS[dt.date.fromisoformat(s['date']).weekday()][:3]} {s['title']}"
        + (f" ≤{s['distance_cap_km']:g} km" if s.get("distance_cap_km") is not None else "")
        for s in sessions)
    return (f"- **Semaine en cours — {plan['week']} · plan typé r{revision['revision']}** : "
            f"{compact}. PostgreSQL → `athlete/journal/{plan['week'].lower()}.md`. "
            "Macro-plan jamais prescriptible.")


#: ⭐ UNE RÉVISION SE RELIT AVANT DE SE RACONTER (2026-08-25).
#:
#: Le coach compose le tableau des séances de tête, l'envoie sur stdin, et recevait pour
#: toute réponse « révision N enregistrée et validée ». Il n'a jamais vu ce qu'il venait
#: d'écrire : il enchaînait sur un message à l'athlète décrivant ce qu'il CROYAIT avoir
#: écrit. Le 25/08, r3 annonçait dans son propre motif un jogging le jeudi, un renfo B
#: décalé et un repos le mardi ; l'objet ne portait qu'une annulation d'EF le mercredi.
#: Les deux autres faits du message de l'athlète n'existaient nulle part, et r4 a reconduit
#: l'oubli du jogging.
#:
#: Le contrat rend donc la semaine telle qu'elle EST, jour par jour, jours vides et
#: séances annulées compris -- c'est le seul rendu du vault qui les montre au coach
#: avant qu'il parle. Le diff dit ce qui a bougé, la semaine dit ce qui en résulte, et
#: aucun des deux ne se déduit de l'autre : un oubli ne se voit que dans le second.
def _revision_index(revision: dict | None) -> dict[str, dict]:
    return {s["id"]: s for s in (revision or {}).get("sessions", [])}


def _day_label(stamp: str) -> str:
    day = dt.date.fromisoformat(stamp)
    return f"{WEEKDAYS[day.weekday()][:3]} {day:%d/%m}"


def _comparable(session: dict) -> tuple:
    """Ce dont un changement mérite d'être signalé : ce qui ENGAGE, pas la prose."""
    return (session.get("title"), session.get("workout"), session.get("status"),
            session.get("distance_target_km"), session.get("distance_cap_km"),
            session.get("rpe_min"), session.get("rpe_max"), session.get("demanding"),
            session.get("transition"))


def _changed_session_fields(previous: dict | None, current: dict,
                            as_of: str) -> dict[str, set[str]]:
    before, after = _revision_index(previous), _revision_index(current)
    changed: dict[str, set[str]] = {}
    for session_id in before.keys() | after.keys():
        old, new = before.get(session_id), after.get(session_id)
        relevant_dates = [session["date"] for session in (old, new) if session]
        if not relevant_dates or max(relevant_dates) < as_of:
            continue
        if old is None:
            changed[session_id] = {"added"}
            continue
        if new is None:
            changed[session_id] = {"removed"}
            continue
        fields = {name for name in CHANGE_FIELDS if name not in {"added", "removed"}
                  and old.get(name) != new.get(name)}
        if fields:
            changed[session_id] = fields
    return changed


def restrictions(old: dict, new: dict | None) -> list[str]:
    """Ce qui, d'une révision à l'autre, RETIRE à une séance future.

    Ajouter, déplacer ou allonger n'en fait pas partie : seule la restriction a fauté
    en série. Une séance déjà annulée ne se restreint plus.
    """
    if old.get("status") == "cancelled":
        return []
    if new is None:
        return ["retirée"]
    if new.get("status") == "cancelled":
        return ["annulée"]
    found = []
    old_cap, new_cap = old.get("distance_cap_km"), new.get("distance_cap_km")
    if new_cap is not None and (
            (old_cap is not None and new_cap < old_cap)
            or (old_cap is None and old.get("distance_target_km") is not None
                and new_cap < old["distance_target_km"])):
        found.append("plafond de distance abaissé")
    old_target, new_target = old.get("distance_target_km"), new.get("distance_target_km")
    if old_target is not None and new_target is not None and new_target < old_target:
        found.append("cible de distance abaissée")
    old_minutes, new_minutes = old.get("duration_cap_min"), new.get("duration_cap_min")
    if old_minutes is not None and new_minutes is not None and new_minutes < old_minutes:
        found.append("plafond de durée abaissé")
    if old.get("workout") in HARD_WORKOUTS and new.get("workout") not in HARD_WORKOUTS:
        found.append(f"{old['workout']} remplacée par {new.get('workout')}")
    old_strength, new_strength = old.get("strength") or {}, new.get("strength") or {}
    if set(new_strength.get("drop") or []) - set(old_strength.get("drop") or []):
        found.append("exercice retiré du renfo")
    if new_strength.get("half_dose") and not old_strength.get("half_dose"):
        found.append("renfo en demi-dose")
    return found


def _motif_errors(session_id: str, entry: dict, cuts: list[str],
                  evidence: list[str]) -> list[str]:
    motif = entry.get("motif")
    if not motif:
        return [
            f"changes.{session_id}.motif : restriction ({', '.join(cuts)}) sans motif "
            "typé. Nomme la règle du règlement qui l'impose (`regle` : "
            f"{', '.join(MOTIF_RULES)}) et le fait qui la déclenche (`fait` : une entrée "
            "exacte de evidence). Sans prémisse, la séance reste."]
    fait = motif.get("fait") or ""
    if fait not in evidence:
        return [f"changes.{session_id}.motif.fait doit recopier exactement une entrée "
                "de evidence."]
    if motif.get("regle") == "demande":
        if not _REQUEST_TRIGGER.match(fait):
            return [f"changes.{session_id}.motif : regle=demande exige un message de "
                    "l'athlète (messenger:, request:, availability:)."]
    elif not _FACT_TRIGGER.match(fait):
        return [f"changes.{session_id}.motif : regle={motif.get('regle')} s'applique à "
                "un fait observé (observation:, strava:, medical:, gate:, exposure:, "
                "incident:, strength:), pas à une intention."]
    return []


_AUTHORITATIVE_TRIGGER = re.compile(
    r"^(?:observation|exposure|messenger|medical|availability|gate|request|incident|strength|strava):"
)


def change_evidence_errors(previous: dict | None, current: dict) -> list[str]:
    """Chaque changement futur nomme sa preuve et la règle qui le transforme en décision ;
    une restriction y ajoute son `motif` typé (2026-10-10)."""
    if previous is None:
        return []
    as_of = str(current.get("computed_at") or now_stamp())[:10]
    expected = _changed_session_fields(previous, current, as_of)
    entries = current.get("changes")
    if not expected:
        return ["changes : aucune prescription future ne change ; retire ces justifications."] \
            if entries else []
    if not isinstance(entries, list) or not entries:
        return [
            "changes obligatoire : chaque séance future modifiée doit nommer les champs "
            "touchés, une preuve exacte de evidence (`trigger`) et la règle appliquée."]
    problems: list[str] = []
    before, after = _revision_index(previous), _revision_index(current)
    by_session: dict[str, dict] = {}
    for entry in entries:
        session_id = entry.get("session_id") if isinstance(entry, dict) else None
        if not session_id or session_id in by_session:
            problems.append(f"changes : session_id absent ou dupliqué ({session_id!r}).")
            continue
        by_session[session_id] = entry
    for session_id, fields in expected.items():
        entry = by_session.get(session_id)
        if entry is None:
            problems.append(f"changes : justification absente pour {session_id}.")
            continue
        declared = set(entry.get("fields") or [])
        if declared != fields:
            problems.append(
                f"changes.{session_id}.fields : attendu {sorted(fields)}, reçu {sorted(declared)}.")
        trigger = entry.get("trigger") or ""
        if trigger not in (current.get("evidence") or []):
            problems.append(
                f"changes.{session_id}.trigger doit recopier exactement une entrée de evidence.")
        elif not _AUTHORITATIVE_TRIGGER.match(trigger):
            problems.append(
                f"changes.{session_id}.trigger n'est pas une preuve autoritative typée.")
        if not str(entry.get("rule") or "").strip():
            problems.append(f"changes.{session_id}.rule est vide.")
        if not str(entry.get("context") or "").strip():
            problems.append(f"changes.{session_id}.context est vide.")
        cuts = restrictions(before[session_id], after.get(session_id)) \
            if session_id in before else []
        if cuts:
            problems += _motif_errors(session_id, entry, cuts,
                                      current.get("evidence") or [])
    extras = sorted(set(by_session) - set(expected))
    if extras:
        problems.append("changes : aucune modification future pour " + ", ".join(extras) + ".")
    return problems


def revision_diff(previous: dict | None, current: dict) -> list[str]:
    """Ce qui a bougé entre deux révisions, une ligne par séance touchée."""
    avant, apres = _revision_index(previous), _revision_index(current)
    lignes = []
    for session_id, session in apres.items():
        old = avant.get(session_id)
        if old is None:
            lignes.append(f"{session_id} : AJOUTÉE le {_day_label(session['date'])} "
                          f"({session['title']})")
        elif old["date"] != session["date"]:
            lignes.append(f"{session_id} : DÉPLACÉE du {_day_label(old['date'])} "
                          f"au {_day_label(session['date'])}")
        elif session["status"] == "cancelled" and old["status"] != "cancelled":
            lignes.append(f"{session_id} : ANNULÉE le {_day_label(session['date'])} "
                          f"({session['title']})")
        elif _comparable(old) != _comparable(session):
            lignes.append(f"{session_id} : MODIFIÉE le {_day_label(session['date'])} "
                          f"({session['title']})")
    for session_id, session in avant.items():
        if session_id not in apres:
            lignes.append(f"{session_id} : RETIRÉE de la révision "
                          f"(elle était le {_day_label(session['date'])})")
    return lignes


def revision_week_lines(week: str, revision: dict) -> list[str]:
    """La semaine résultante, un jour par ligne, vides et annulées comprises."""
    monday = dt.date.fromisocalendar(int(week[:4]), int(week[-2:]), 1)
    par_jour: dict[str, list[dict]] = {}
    for session in revision.get("sessions", []):
        par_jour.setdefault(session["date"], []).append(session)
    lignes = []
    for offset in range(7):
        day = monday + dt.timedelta(days=offset)
        same = par_jour.get(day.isoformat(), [])
        if not same:
            lignes.append(f"{_day_label(day.isoformat())} : AUCUNE SÉANCE")
            continue
        lignes.append(f"{_day_label(day.isoformat())} : " + " + ".join(
            s["title"] + ("" if s["status"] == "planned" else f" ({s['status']})")
            for s in same))
    return lignes


def revision_readback(week: str, previous: dict | None, current: dict) -> str:
    """Ce que la CLI rend au coach : le diff, puis la semaine telle qu'elle est."""
    depuis = f"r{previous['revision']}" if previous else "rien (première révision)"
    diff = revision_diff(previous, current) or [
        "AUCUNE séance ajoutée, annulée, déplacée ni modifiée. Si le message de l'athlète "
        "demandait un changement, cette révision ne le porte pas."]
    bloc = [
        f"{week} révision {current['revision']} enregistrée et validée.",
        "",
        f"Ce qui change depuis {depuis} :",
        *(f"- {ligne}" for ligne in diff),
        "",
        "RELIS LA SEMAINE AVANT DE RÉPONDRE. Elle est maintenant :",
        *(f"- {ligne}" for ligne in revision_week_lines(week, current)),
    ]
    if current.get("volume_target_km") is not None:
        bloc.append(f"- Volume couru visé : ~{current['volume_target_km']:g} km")
    orphelins = sessions_orphelines(previous, current)
    if orphelins:
        bloc += ["", "ATTENTION :", *(f"- {ligne}" for ligne in orphelins)]
    bloc += ["", "Chaque fait du message de l'athlète doit se retrouver ci-dessus. Un jour "
                 "marqué AUCUNE SÉANCE est un oubli tant que tu n'as pas vérifié qu'il "
                 "est voulu."]
    return "\n".join(bloc)


#: ⭐ LE MOTIF ET L'OBJET SE JUGENT ENSEMBLE (2026-08-25). `semantic_errors` vérifiait
#: des invariants physiologiques -- 48 h entre renfo A et B, jours exigeants
#: consécutifs -- et jamais si la révision FAIT ce qu'elle DIT.
#: La r3 du 25/08 annonçait dans son motif une sortie le jeudi et un renfo B décalé ;
#: l'objet ne touchait que le mercredi. Elle est passée validée, et le coach a raconté
#: son motif à l'athlète.
#:
#: Le contrôle porte sur les jours parce que c'est la seule affirmation d'un motif qu'un
#: programme puisse contredire mécaniquement. Il ouvre les deux chemins corrects : soit
#: la révision oublie ce jour, soit le motif le nomme sans le changer -- et une
#: circonstance qui ne change rien vit dans `constraints`, pas dans le motif.
_JOURS_NOMMES = {nom: index for index, nom in enumerate(WEEKDAYS)}
_DATE_COURTE = re.compile(r"\b(\d{2})/(\d{2})\b")
_DATE_ISO = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def _dates_nommees(text: str, week: str) -> set[str]:
    monday = dt.date.fromisocalendar(int(week[:4]), int(week[-2:]), 1)
    semaine = {(monday + dt.timedelta(days=index)).isoformat() for index in range(7)}
    trouvees = set()
    lowered = text.lower()
    for nom, index in _JOURS_NOMMES.items():
        if re.search(rf"\b{nom}\b", lowered):
            trouvees.add((monday + dt.timedelta(days=index)).isoformat())
    for jour, mois in _DATE_COURTE.findall(text):
        candidate = f"{monday.year}-{mois}-{jour}"
        if candidate in semaine:
            trouvees.add(candidate)
    trouvees.update(stamp for stamp in _DATE_ISO.findall(text) if stamp in semaine)
    return trouvees & semaine


def _dates_touchees(previous: dict | None, current: dict) -> set[str]:
    avant, apres = _revision_index(previous), _revision_index(current)
    touchees = set()
    for session_id, session in apres.items():
        old = avant.get(session_id)
        if old is None or _comparable(old) != _comparable(session):
            touchees.add(session["date"])
        if old is not None and old["date"] != session["date"]:
            touchees.update({old["date"], session["date"]})
    touchees.update(session["date"] for session_id, session in avant.items()
                    if session_id not in apres)
    return touchees


def coherence_errors(week: str, previous: dict | None, current: dict) -> list[str]:
    """Le motif nomme un jour que l'objet ne touche pas : l'un des deux ment."""
    if previous is None:      # une première révision touche toute la semaine
        return []
    nommees = _dates_nommees(current.get("reason") or "", week)
    manquantes = sorted(nommees - _dates_touchees(previous, current))
    return [
        f"revision {current['revision']} : le motif nomme le {_day_label(stamp)} mais "
        "aucune séance de ce jour n'est ajoutée, annulée, déplacée ni modifiée. Soit la "
        "révision oublie ce jour et il faut l'écrire, soit le motif le nomme sans le "
        "changer : retire-le du motif, ou porte la circonstance dans `constraints`."
        for stamp in manquantes]


def sessions_orphelines(previous: dict | None, current: dict) -> list[str]:
    """Les jours que CETTE révision laisse sans rien à faire.

    Pas une faute : renoncer à une séance est une décision de coaching ordinaire. Mais
    c'est la forme exacte qu'a prise l'oubli du 25/08, et elle ne se voit dans aucun
    rendu -- l'annulé sort de `scheduled()`, donc du texto, de la carte et du hot-cache.
    Le coach la reçoit donc nommée, avec la question qui va avec.
    """
    avant = _revision_index(previous)
    par_jour: dict[str, list[dict]] = {}
    for session in current.get("sessions", []):
        par_jour.setdefault(session["date"], []).append(session)
    orphelins = []
    for stamp, same in sorted(par_jour.items()):
        if any(s["status"] != "cancelled" for s in same):
            continue
        neuves = [s for s in same
                  if avant.get(s["id"], {}).get("status") != "cancelled"]
        if neuves:
            orphelins.append(
                f"{_day_label(stamp)} ne porte plus que des annulations "
                f"({', '.join(s['title'] for s in neuves)}) et aucune séance à la "
                "place. Voulu, ou remplaçante oubliée ?")
    return orphelins
