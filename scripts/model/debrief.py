"""Les écarts calculés d'une sortie : ce que la montre ne dit pas à l'athlète.

Audit du 2026-10-11. Les moments où Redline lui a appris quelque chose sont des
comparaisons calculées : FC montée à allure comparable avant le rhume, renfo de 73 min
pour 30 prévues, SL « roulante » à 164 bpm pour un plafond de 157. Sur 26 débriefs, 4
seulement portaient une comparaison ; 13 ouvraient sur trois chiffres relus sur Strava.
Le service calcule donc ces écarts avant le tour et les joint à l'instantané : le coach
n'a plus à les reconstruire, ni la tentation de réciter la montre à leur place.

Fonction pure : la ligne du registre, la séance prévue et la dérive de FC entrent, des
phrases courtes et chiffrées sortent. Rien n'est écrit.
"""

from __future__ import annotations

from typing import Any


def _fr(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _signed(value: float) -> str:
    return ("+" if value > 0 else "") + _fr(value)


def planned_vs_done(row: Any, session: dict | None) -> list[str]:
    """Chaque cible typée de la séance mise en regard de ce qui a été fait."""
    if not session:
        return []
    out = []
    if row.km is not None:
        for key, label in (("distance_target_km", "cible"), ("distance_cap_km", "plafond")):
            if session.get(key) is not None:
                out.append(f"distance {_fr(row.km)} km pour un {label} de "
                           f"{_fr(session[key])} ({_signed(row.km - session[key])})")
                break
    # Une durée plafond ne se compare que si c'est elle que l'athlète a lue : le rendu
    # (`weekly_plan.distance_of`) la tait dès qu'une distance engage. Le 15/10, « 57 min
    # pour 55 » lui a été reproché sur un plafond qu'aucun message ne lui avait donné.
    shown_by_distance = (session.get("distance_cap_km") is not None
                         or session.get("distance_target_km") is not None)
    if (row.hours is not None and session.get("duration_cap_min") is not None
            and not shown_by_distance):
        minutes = row.hours * 60
        out.append(f"durée {minutes:.0f} min pour un plafond de {session['duration_cap_min']} "
                   f"({_signed(round(minutes - session['duration_cap_min']))})")
    # Une FC courue sans brassard ne se compare à aucun plafond (2026-10-20).
    hr = row.hr_avg if getattr(row, "hr_doubt", None) != "sans_brassard" else None
    if hr is not None and session.get("hr_cap_bpm") is not None:
        out.append(f"FC moyenne {hr} pour un plafond de {session['hr_cap_bpm']} "
                   f"({_signed(hr - session['hr_cap_bpm'])})")
    if row.rpe is not None and session.get("rpe_max") is not None:
        low = session.get("rpe_min")
        span = f"{_fr(low)}-{_fr(session['rpe_max'])}" if low is not None else \
            f"au plus {_fr(session['rpe_max'])}"
        out.append(f"RPE {_fr(row.rpe)} pour une cible {span}")
    return out


def gaps(row: Any, session: dict | None, drift: Any | None) -> list[str]:
    """Les écarts d'une sortie, dans l'ordre où ils servent : prévu/fait, puis la FC."""
    out = planned_vs_done(row, session)
    if drift is not None:
        out.append("FC à allure comparable : " + str(drift))
    return out


# ------------------------------------------------------------------ répétitions

#: Les séances dont le débrief porte la carte des répétitions. Une SL ou une course
#: sont exigeantes sans être fractionnées : leurs tours sont des kilomètres, pas des reps.
QUALITY_WORKOUTS = frozenset({"seuil", "cotes", "lignes"})
#: Une séance typée « 1re qualité » en est une, quel que soit son workout.
QUALITY_TRANSITION = "qualite"
#: Écart minimal d'allure entre le plus lent des tours rapides et le plus rapide des
#: tours lents. En dessous, les tours sont des tours automatiques d'une allure
#: continue, pas une alternance effort/récup.
MIN_SPLIT_RATIO = 1.12
#: Un tour plus court est un double appui sur le bouton, pas une phase de la séance.
MIN_LAP_S = 10


def is_quality(session: dict | None) -> bool:
    if not session:
        return False
    return (session.get("workout") in QUALITY_WORKOUTS
            or session.get("transition") == QUALITY_TRANSITION)


def _seconds(text: Any) -> int | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        parts = [int(part) for part in text.split(":")]
    except ValueError:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _phases(laps: list) -> list[dict]:
    """Les tours lisibles, dans l'ordre : durée, distance, FC, allure (s/km)."""
    out = []
    for index, lap in enumerate(laps):
        if not isinstance(lap, dict):
            continue
        time_s = _seconds(lap.get("temps"))
        if not time_s or time_s < MIN_LAP_S:
            continue
        dist_m = float(lap.get("distance_km") or 0) * 1000
        out.append({"time_s": time_s, "dist_m": dist_m, "hr": lap.get("fc_moy"),
                    "pace": time_s / (dist_m / 1000) if dist_m > 0 else float("inf"),
                    "index": index})
    return out


def _merge(phases: list[dict]) -> dict:
    time_s = sum(phase["time_s"] for phase in phases)
    merged = {"time_s": float(time_s),
              "dist_m": float(round(sum(phase["dist_m"] for phase in phases)))}
    if all(phase["hr"] for phase in phases):
        merged["hr"] = float(round(sum(phase["hr"] * phase["time_s"] for phase in phases)
                                   / time_s))
    return merged


def lap_blocks(laps: list) -> list[tuple[bool, list[dict]]] | None:
    """Les tours lisibles groupés en blocs d'effort (`True`) et de récupération.

    Sans modèle : les tours se rangent par allure et se coupent au plus grand saut ;
    le côté rapide est l'effort. Des tours rapides contigus forment UN bloc (tours
    automatiques pendant une répétition). Chaque tour garde son rang (`index`) dans la
    liste reçue. `None` quand les tours ne montrent pas une alternance nette d'au moins
    deux efforts.
    """
    phases = _phases(laps)
    if len(phases) < 3:
        return None
    # Une récup arrêtée (distance nulle) est lente par nature : elle ne place pas la coupe.
    paces = sorted(phase["pace"] for phase in phases if phase["pace"] != float("inf"))
    ratio, cut = max(((paces[i + 1] / paces[i], i) for i in range(len(paces) - 1)
                      if paces[i] > 0), default=(0.0, -1))
    if ratio < MIN_SPLIT_RATIO:
        return None
    threshold = paces[cut]
    blocks: list[tuple[bool, list[dict]]] = []
    for phase in phases:
        fast = phase["pace"] <= threshold
        if blocks and blocks[-1][0] == fast:
            blocks[-1][1].append(phase)
        else:
            blocks.append((fast, [phase]))
    if sum(1 for fast, _ in blocks if fast) < 2:
        return None
    return blocks


def workout_reps(laps: list) -> list[dict] | None:
    """Les répétitions lues sur les tours Strava, chacune avec la récup qui la suit.

    Les tours lents entre deux répétitions forment sa récup ; échauffement et retour au
    calme restent hors carte (`lap_blocks` dit comment les blocs se lisent).
    """
    blocks = lap_blocks(laps)
    if blocks is None:
        return None
    efforts = [index for index, (fast, _) in enumerate(blocks) if fast]
    reps = []
    for number, index in enumerate(efforts):
        rep = _merge(blocks[index][1])
        if rep["dist_m"] < 1:
            return None
        rep["label"] = f"R{number + 1}"
        if number + 1 < len(efforts):
            recovery = _merge(blocks[index + 1][1])
            rep["recovery"] = {"time_s": recovery["time_s"], "dist_m": recovery["dist_m"]}
        reps.append(rep)
    return reps


def quality_card(laps: list, session: dict | None, date: str = "") -> dict | None:
    """La charge utile de `chart-workout.py` pour une séance de qualité, sinon None."""
    if session is None or not is_quality(session):
        return None
    reps = workout_reps(laps)
    if reps is None:
        return None
    card: dict[str, Any] = {"title": session.get("title") or "Séance", "reps": reps}
    if date:
        card["date"] = date
    return card
