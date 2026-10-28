#!/usr/bin/env python3
"""Les signaux faibles, calculés — la moitié du § D de `coaching/protocols.md` qui
peut se lire depuis le registre.

`protocols.md` liste depuis le 2026-07-09 cinq contrôles « qui ne peuvent pas mentir ».
Aucun n'était **calculé** : ils reposaient sur le fait que le coach y pense, en
relisant, un dimanche. C'est le dispositif censé attraper la blessure
avant l'aveu — et l'aveu du 09/07 est arrivé avec deux mois de retard.

**Ce que ce script NE fait pas, et pourquoi.** Quatre des contrôles du § D vivent chez
Strava, pas au registre : allure en descente sur les segments de référence, parcours
écourtés, pauses inhabituelles en fin de sortie, longueur des notes. Ils restent à la
charge du coach au bilan hebdo, via `scripts/strava.py` (le MCP a été retiré le
2026-08-14) : ce script ne lit que le registre.

**Les seuils sont grossiers, et c'est assumé.** Ils n'ont pas besoin d'être justes : ils
doivent forcer le regard. Un signal qui sort ici n'est jamais un verdict, c'est une ligne
à aller vérifier — la même doctrine que la jauge, « signal d'alerte, jamais couperet ».

    python3 scripts/signals.py            # les 4 dernières semaines
    python3 scripts/signals.py --semaines 6
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import ledger as gauge  # noqa: E402
from scripts.model import (  # noqa: E402
    entrypoint,
    paths,
)

# Une cadence à allure égale qui MONTE mérite un regard. Elle ne porte pas sa cause :
# travail volontaire, terrain, allure, bruit de mesure et protection sont des hypothèses.
# En pas/min deux pieds : 4 est au-dessus du bruit d'une moyenne de séance.
CADENCE_JUMP = 4.0
# Les sorties entre lesquelles la cadence est comparable. Ailleurs (côtes, trail, D+,
# qualité) elle vit sa vie et l'y mesurer reviendrait à lire le terrain, pas le injury.
EASY_TYPES = {"EF", "SL"}
# La plus longue sortie se compte sur tout ce qui est COURU : la plafonner aux seules
# EF/SL ferait passer une sortie de groupe ou une course pour une semaine calme, et
# c'est exactement la sortie qui a fait exploser la charge le 29/07.
RUN_TYPES = EASY_TYPES | {"seuil", "côtes", "lignes", "cadence", "course"}
# Le plaisir n'est plus un signal (2026-10-09, [[decisions]]) : « il chute une à deux
# sorties avant que la douleur ne soit avouée » avait été écrit le 09/07 sans source, ne
# s'est jamais vérifié dans le registre, et a produit six messages et une correction.
# La plus longue sortie n'a PLUS de signal de saut : aucun pourcentage fixe ne juge sa
# progression depuis le 2026-09-27 (décision de l'athlète, `scripts/model/rulebook.py`).
# Elle reste affichée, et sa STAGNATION reste signalée (scénario 9).

# ⭐ ALARME N°4, CALCULÉE (2026-09-27). « FC anormalement haute en footing » est le
# marqueur qui a précédé l'effondrement de mars 2026 d'une à deux semaines
# (current-plan.md, garde-fou n°4) ; il n'était calculé nulle part. On compare une EF
# plate à ses voisines d'ALLURE, jamais à une moyenne globale : sinon on mesure l'allure.
HR_DRIFT_BPM = 6            # au-dessus du bruit d'une FC moyenne de séance
HR_PACE_BAND_S = 20         # « même allure » : ±20 s/km
HR_BASELINE_DAYS = 42       # six semaines de référence, antérieures à la sortie
HR_BASELINE_MIN = 3         # en dessous, pas de référence, donc pas de verdict
HR_FLAT_M_PER_KM = 15       # au-delà, le relief pilote la FC
HR_MIN_KM = 4.0
# La météo normalise la FC, elle ne l'excuse pas (CLAUDE.md) : une journée chaude
# n'entre ni dans la référence ni dans la comparaison. WBGT inconnu : gardé.
HR_HOT_WBGT_C = 22.0


@dataclass
class Signal:
    label: str
    detail: str

    def __str__(self) -> str:
        return f"  ⚑ {self.label} — {self.detail}"


def _weeks(rows: list, count: int) -> list:
    """Les `count` dernières étiquettes de semaine présentes au registre, dans l'ordre."""
    return sorted({row.week for row in rows})[-count:]


def intentional_cadence_keys(store: gauge.LedgerStore) -> frozenset[str]:
    """Les sorties dont la cadence a été travaillée exprès, prescrite ou volontaire.

    Une hausse de cadence voulue n'est pas un signal. Chaque lecteur des signaux passe
    par ici : le dossier du bilan l'avait oublié et signalait le travail demandé.
    """
    exposures = store.exposure_rows(confirmed_only=True)
    return frozenset(
        row["observation_key"] for row in exposures
        if row["exposure_type"] == "cadence"
        and row["origin"] in {"prescribed", "voluntary"})


def current(store: gauge.LedgerStore, count: int = 4) -> list:
    """Les signaux des `count` dernières semaines du registre, tels que tout lecteur les voit."""
    rows = gauge.parse_ledger(store)
    weeks = _weeks(rows, count)
    return signals(rows, weeks, intentional_cadence_keys(store)) if weeks else []


def _mean(values: list) -> float | None:
    return sum(values) / len(values) if values else None


def cadence_at_easy_pace(rows: list, weeks: list, intentional_keys: frozenset[str]) -> list:
    """Cadence moyenne sur les sorties faciles, semaine par semaine, à allure comparable.

    On ne compare que des sorties faciles entre elles, sinon on mesure surtout le
    terrain. Même comparable, une hausse reste un constat et jamais son explication.
    """
    out = []
    for label in weeks:
        cadences = [row.cadence for row in rows
                    if row.week == label and row.type in EASY_TYPES and row.cadence
                    and row.idempotency_key not in intentional_keys]
        out.append((label, _mean([float(c) for c in cadences]), len(cadences)))
    return out


def longest_run(rows: list, weeks: list) -> list:
    out = []
    for label in weeks:
        distances = [row.km for row in rows
                     if row.week == label and row.type in RUN_TYPES and row.km]
        out.append((label, max(distances) if distances else None))
    return out


@dataclass
class HrDrift:
    date: dt.date
    pace_s: float
    hr: float
    reference: float
    n: int

    @property
    def delta(self) -> float:
        return self.hr - self.reference

    def __str__(self) -> str:
        minutes, seconds = divmod(round(self.pace_s), 60)
        return (f"{self.date:%d/%m} à {minutes}:{seconds:02d}/km : {self.hr:.0f} bpm "
                f"contre {self.reference:.0f} (médiane de {self.n} EF voisines, "
                f"{self.delta:+.0f})")


def _flat_easy_runs(rows: list) -> list:
    """Les EF comparables : plates, assez longues, avec chrono et FC, hors chaleur.

    Une FC déclarée douteuse n'entre ni dans la référence ni dans la comparaison, quel
    que soit le motif : sans brassard elle est fausse, en contexte (repas, maladie) elle
    mesure autre chose que la fatigue. La porte W40-W42 comptait une sortie du 07/10
    courue sans brassard (audit d'effet du 2026-10-19).
    """
    runs = [row for row in rows
            if row.type == "EF" and row.km and row.km >= HR_MIN_KM and row.hours
            and row.hr_avg and not getattr(row, "hr_doubt", None)
            and row.dplus / row.km <= HR_FLAT_M_PER_KM
            and (row.wbgt is None or row.wbgt < HR_HOT_WBGT_C)]
    return sorted(runs, key=lambda row: row.date)


def hr_at_pace(rows: list) -> list[HrDrift]:
    """Chaque EF comparée à la médiane des EF de même allure des six semaines d'avant."""
    runs = _flat_easy_runs(rows)
    out = []
    for run in runs:
        pace = run.hours * 3600 / run.km
        reference = [other.hr_avg for other in runs
                     if other is not run
                     and 0 < (run.date - other.date).days <= HR_BASELINE_DAYS
                     and abs(other.hours * 3600 / other.km - pace) <= HR_PACE_BAND_S]
        if len(reference) >= HR_BASELINE_MIN:
            out.append(HrDrift(run.date, pace, float(run.hr_avg),
                               float(statistics.median(reference)), len(reference)))
    return out


def signals(rows: list, weeks: list, intentional_keys: frozenset[str]) -> list:
    """Ce qui mérite un regard cette semaine. Vide = rien à signaler, et c'est normal."""
    found = []

    cadences = [(label, value) for label, value, n in cadence_at_easy_pace(
        rows, weeks, intentional_keys)
                if value is not None]
    if len(cadences) >= 2:
        (_, before), (label, now) = cadences[-2], cadences[-1]
        if now - before >= CADENCE_JUMP:
            found.append(Signal(
                "cadence en hausse à allure facile",
                f"{before:.0f} → {now:.0f} pas/min ({label}). Cause inconnue : vérifier "
                "d'abord si la cadence était prescrite ou volontaire, puis le terrain, "
                "l'allure, la facilité du geste et la douleur"))

    drifts = hr_at_pace(rows)
    recent = [drift for drift in drifts if drift_week(drift) in weeks]
    if len(recent) >= 2 and all(drift.delta >= HR_DRIFT_BPM for drift in recent[-2:]):
        found.append(Signal(
            "FC haute à allure comparable, deux EF de suite",
            " ; ".join(str(drift) for drift in recent[-2:]) + ". Alarme n°4 du plan "
            "(elle a précédé mars 2026). Vérifier d'abord sommeil, maladie, chaleur "
            "non mesurée et terrain : un signal n'est pas une cause"))

    return found


def drift_week(drift: HrDrift) -> str:
    year, week, _ = drift.date.isocalendar()
    return f"{year}-W{week:02d}"


def render(rows: list, weeks: list, intentional_keys: frozenset[str],
           today: dt.date | None = None) -> str:
    lines = ["", f"  Signaux faibles — {weeks[0]} → {weeks[-1]}", ""]
    lines.append("  Cadence (EF/SL, pas/min)   " + " · ".join(
        f"{label[-3:]} {value:.0f}" if value else f"{label[-3:]} —"
        for label, value, _ in cadence_at_easy_pace(rows, weeks, intentional_keys)))
    lines.append("  Plus longue (km)           " + " · ".join(
        f"{label[-3:]} {value:.1f}" if value else f"{label[-3:]} —"
        for label, value in longest_run(rows, weeks)))
    # La pente : le scénario 9 se lit sur des semaines qui ne progressent plus.
    stall = gauge.long_run_stall(rows, today or dt.date.today())
    if stall:
        lines.append(f"  {stall}")
    drifts = [drift for drift in hr_at_pace(rows) if drift_week(drift) in weeks]
    lines.append("  FC à allure (EF plates)    " + (" · ".join(
        f"{drift.date:%d/%m} {drift.delta:+.0f}" for drift in drifts)
        or "aucune EF comparable"))
    found = signals(rows, weeks, intentional_keys)
    lines += ["", *[str(signal) for signal in found]] if found else [
        "", "  Rien à signaler sur ces semaines."]
    lines += ["", "  Les 4 contrôles Strava du § D (allure en descente, parcours écourtés,",
              "  pauses en fin de sortie, longueur des notes) restent à lire chez Strava.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=paths.vault_root(), type=Path)
    parser.add_argument("--semaines", dest="weeks", type=int, default=4)
    args = parser.parse_args()

    store = gauge.open_store()
    rows = gauge.parse_ledger(store)
    intentional = intentional_cadence_keys(store)
    weeks = _weeks(rows, args.weeks)
    if not weeks:
        print("Registre vide : rien à lire.")
        return 1
    print(render(rows, weeks, intentional))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
