"""Les décisions de coaching et leurs prédictions narratives, lues depuis decisions.md.

Extrait de `scripts/decisions-index.py` le 2026-09-27 pour que `scripts/vault-check.py`
puisse contrôler la DETTE de prédictions sans dupliquer le parseur. Statut : identique à
l'index, ce code ne dérive aucun fait opérationnel de la prose ; il indexe un journal
documentaire et vérifie qu'une prédiction échue a reçu son verdict daté.

Deux familles de prédictions coexistent, chacune avec UN propriétaire :
- les prédictions **narratives** d'une décision (ici, fermées par `**Verdict** (date)`) ;
- les prédictions **typées** (`fact-write.py prediction`, PostgreSQL, score de Brier).
Le bilan du dimanche solde les deux ; en oublier une a laissé 43 prédictions narratives
sans verdict pendant que le bilan écrivait « Prédictions échues : aucune » (audit du
2026-09-27).
"""

from __future__ import annotations

import datetime as dt
import re

# A prediction "revue W36" comes due once that week has ended. The week carries no
# year: it is the W36 of the decision's ISO year, unless that one ended more than half
# a year before the decision -- a "W03" written in December means next January. No
# plan window and no race day here: the race date belongs to its typed deadline in
# PostgreSQL, and this parser reads prose only.
WRAP_AFTER = dt.timedelta(weeks=26)


def week_end(week: int, decided: dt.date) -> dt.date | None:
    """Sunday of review week `week` for a decision taken on `decided`, or None if that
    week does not exist (a W53 in a 52-week year)."""
    year = decided.isocalendar().year
    try:
        sunday = dt.date.fromisocalendar(year, week, 7)
        if decided - sunday > WRAP_AFTER:
            sunday = dt.date.fromisocalendar(year + 1, week, 7)
    except ValueError:
        return None
    return sunday

HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})([^\n—-]*)—\s*(.+)$", re.MULTILINE)
# Une rubrique de prédiction est une frontière contrôlée : les trois libellés ont
# coexisté dans decisions.md, et n'en reconnaître qu'un a rendu onze décisions
# récentes invisibles. `PREDICTION_START` est volontairement plus large que le
# parseur. Si une future variante commence par le mot attendu mais ne respecte pas
# la forme en gras fermée, `parse` refuse au lieu de la transformer en « — ».
PREDICTION_START = re.compile(r"^\*\*Prédictions?\b", re.MULTILINE | re.IGNORECASE)
PREDICTION = re.compile(
    r"^\*\*(?P<label>Prédictions?(?:\s+falsifiables?)?[^*\n]*)\*\*"
    r"\s*:?\s*(?P<text>.+?)(?=\n\s*\n|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
WEEK = re.compile(r"\bW(\d{2})\b")
# Une prédiction se FERME. Sans marqueur de clôture, `--due` réaffichait éternellement
# ce qui avait déjà été jugé : au bilan du 2026-08-16 il en listait sept, dont trois
# échues depuis W30 et W32 — et le seul moyen de les faire taire aurait été de
# repousser leur semaine de revue, c'est-à-dire de réécrire l'histoire. Le verdict est
# daté parce qu'un verdict sans date ne dit pas s'il a été rendu à l'heure.
VERDICT = re.compile(r"^\*\*Verdict\*\*\s*\((\d{4}-\d{2}-\d{2})\)", re.MULTILINE)
# L'issue d'un verdict est un vocabulaire fermé depuis le 2026-10-05 ([[decisions]] du
# 2026-10-04) : 22 des 47 verdicts classables étaient « non testable », sans cause
# notée, et aucune réfutée ne disait dans quel sens le coach s'était trompé. Le
# qualificatif nomme ce qui rend le score actionnable. Les verdicts antérieurs restent
# lus tels quels : un historique figé ne se réécrit pas pour satisfaire un parseur.
OUTCOMES = {
    "tenue": (),
    # optimiste : la décision a fait moins bien que prédit ; prudente : mieux.
    "réfutée": ("optimiste", "prudente"),
    # instrument : la mesure n'existait pas · exposition : la situation n'a pas eu
    # lieu · portée : le critère exigeait un inventaire exhaustif · remplacée : une
    # décision ultérieure l'a retirée avant sa revue.
    "non testable": ("instrument", "exposition", "portée", "remplacée"),
}
STRICT_VERDICTS_FROM = dt.date(2026, 10, 5)
VERDICT_LINE = re.compile(r"^\*\*Verdict\*\*\s*\((\d{4}-\d{2}-\d{2})\)\s*:?\s*([^\n]*)",
                          re.MULTILINE)
OUTCOME = re.compile(r"^(tenue|réfutée|non testable)(?:\s*\(([^)]*)\))?\s*—", re.IGNORECASE)
LEGACY_OUTCOME = re.compile(r"^(tenue|réfutée|non testable)\b", re.IGNORECASE)
# Un verdict historique ne se réécrit pas ; il se RECTIFIE, par une ligne datée qui
# s'ajoute sous lui et fait foi pour l'issue (audit du 2026-10-09 : quatorze réfutées
# sans sens, et deux verdicts qui disaient le contraire de leur prédiction). La date du
# verdict d'origine reste celle qui solde la dette : la rectification ne la repousse pas.
RECTIFIED_LINE = re.compile(
    r"^\*\*Verdict rectifié\*\*\s*\((\d{4}-\d{2}-\d{2})\)\s*:?\s*([^\n]*)", re.MULTILINE)
# La semaine de revue que la prédiction ÉCRIT fait foi sur les autres semaines qu'elle
# cite : « Revue : bilan W36 » d'un palier de W42 était indexé W42, six semaines trop
# tard (audit du 2026-10-09).
STATED_REVIEW = re.compile(r"\bRevue\b[^.\n]{0,40}?\bW(\d{2})\b", re.IGNORECASE)
# Une prédiction revue « après la troisième SL » n'a pas de semaine : elle ne devenait
# jamais due. Sa revue se fixe par une ligne datée ajoutée sous elle, sans réécrire le
# texte d'origine.
FIXED_REVIEW = re.compile(r"^\*\*Revue fixée\*\*\s*\(\d{4}-\d{2}-\d{2}\)\s*:?\s*W(\d{2})\b",
                          re.MULTILINE)


class DecisionFormatError(ValueError):
    """A prediction-looking rubric would be silently absent from the index."""


class Decision:
    def __init__(self, date: str, suffix: str, title: str, body: str) -> None:
        self.date = date
        self.title = (suffix.strip() + " " + title.strip()).strip()
        self.body = body
        self.predictions = list(PREDICTION.finditer(body))
        # Le pointeur PostgreSQL reste indexé ; seul son propriétaire typé le solde.
        self.narrative_predictions = [
            match for match in self.predictions
            if match.group('label').casefold().strip() != 'prédiction postgresql'
        ]
        self.has_prediction = bool(self.predictions)
        indexed = {match.start() for match in self.predictions}
        self.unindexed_predictions = [
            match for match in PREDICTION_START.finditer(body)
            if match.start() not in indexed
        ]
        self.review_week = self._review_week()
        found = VERDICT.search(body)
        self.verdict_on = found.group(1) if found else None
        self.outcome: str | None = None
        self.qualifier: str | None = None
        self.verdict_problem: str | None = None
        line = VERDICT_LINE.search(body)
        if line:
            self._read_outcome(dt.date.fromisoformat(line.group(1)), line.group(2))
        rectified = list(RECTIFIED_LINE.finditer(body))
        if rectified:
            if not self.verdict_on:
                self.verdict_problem = "verdict rectifié sans verdict d'origine"
            else:
                last = rectified[-1]
                self.outcome = self.qualifier = None
                self._read_outcome(dt.date.fromisoformat(last.group(1)), last.group(2),
                                   strict=True)

    def _read_outcome(self, on: dt.date, rest: str, *, strict: bool = False) -> None:
        rest = rest.strip()
        if on < STRICT_VERDICTS_FROM and not strict:
            # Historique : seule l'issue en tête est lue, jamais un qualificatif deviné.
            legacy = LEGACY_OUTCOME.match(rest)
            self.outcome = legacy.group(1).casefold() if legacy else None
            return
        found = OUTCOME.match(rest)
        outcome = found.group(1).casefold() if found else ""
        qualifier = (found.group(2) or "").strip().casefold() if found else ""
        allowed = OUTCOMES.get(outcome)
        if allowed is None or (qualifier not in allowed if allowed else qualifier != ""):
            self.verdict_problem = (
                f"verdict du {on.isoformat()} hors vocabulaire ({rest[:60]}) ; "
                f"attendu {OUTCOME_FORM}")
            return
        self.outcome, self.qualifier = outcome, qualifier or None

    def _review_week(self) -> int | None:
        fixed = [int(week) for week in FIXED_REVIEW.findall(self.body)]
        if fixed:
            return fixed[-1]
        stated = [
            int(week)
            for found in self.narrative_predictions or self.predictions
            for week in STATED_REVIEW.findall(f"{found.group('label')} {found.group('text')}")
        ]
        if stated:
            return max(stated)
        weeks = [
            int(week)
            for found in self.narrative_predictions or self.predictions
            for week in WEEK.findall(f"{found.group('label')} {found.group('text')}")
        ]
        return max(weeks) if weeks else None

    @property
    def review_date(self) -> dt.date | None:
        if not self.review_week:
            return None
        return week_end(self.review_week, dt.date.fromisoformat(self.date))

    def is_due(self, today: dt.date) -> bool:
        if self.verdict_on or not self.narrative_predictions:
            return False
        return self.review_date is not None and self.review_date <= today


def parse(text: str) -> list[Decision]:
    out, problems = [], []
    matches = list(HEADING.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        decision = Decision(m.group(1), m.group(2), m.group(3), text[m.end():end])
        out.append(decision)
        if decision.verdict_problem:
            problems.append(f"décision {decision.date} « {decision.title} » : "
                            f"{decision.verdict_problem}")
        if (decision.narrative_predictions and not decision.verdict_on
                and decision.review_week is None):
            # Sans semaine, `is_due` ne la rend jamais due et la dette ne la voit pas :
            # onze prédictions dormaient ainsi au 2026-10-09, dont une réfutée.
            problems.append(
                f"décision {decision.date} « {decision.title} » : prédiction ouverte sans "
                "semaine de revue (écrire « Revue : Wnn », ou ajouter "
                "« **Revue fixée** (AAAA-MM-JJ) : Wnn » sous une prédiction historique)")
        elif (decision.narrative_predictions and not decision.verdict_on
                and decision.review_date is None):
            problems.append(
                f"décision {decision.date} « {decision.title} » : la semaine de revue "
                f"W{decision.review_week:02d} n'existe pas cette année-là")
        for rubric in decision.unindexed_predictions:
            line = text[:m.end() + rubric.start()].count("\n") + 1
            excerpt = decision.body[rubric.start():].splitlines()[0]
            problems.append(
                f"ligne {line}, décision {decision.date} « {decision.title} » : "
                f"rubrique non indexée ({excerpt})"
            )
    if problems:
        raise DecisionFormatError("\n".join(problems))
    return out



#: Au-delà, une prédiction échue sans verdict fait échouer `vault-check.py` : la
#: réponse pré-engagée par la décision du 2026-08-16 (« un contrôle vault-check rouge »)
#: quand le rappel dans le prompt ne suffit pas. Deux bilans de grâce.
DEBT_GRACE_DAYS = 14


def overdue_days(decision: Decision, today: dt.date) -> int:
    """Jours écoulés depuis la fin de la semaine de revue, 0 si rien n'est dû."""
    if not decision.is_due(today) or decision.review_date is None:
        return 0
    return (today - decision.review_date).days


OUTCOME_FORM = " | ".join(
    f"{outcome} ({' | '.join(qualifiers)})" if qualifiers else outcome
    for outcome, qualifiers in OUTCOMES.items())


def verdict_template(today: dt.date) -> str:
    return (f"**Verdict** ({today.isoformat()}) : {OUTCOME_FORM} — une ligne "
            "adossée au registre ou au fait qui tranche")


def calibration(decisions: list[Decision], since: dt.date | None = None) -> dict:
    """Décompte des verdicts narratifs ; `since` filtre sur la date du verdict.

    Pas d'état dérivé stocké : le score se recalcule depuis le journal à chaque lecture.
    """
    counts: dict = {"verdicts": 0, "unclassified": 0, "outcomes": {}}
    for decision in decisions:
        if not decision.verdict_on:
            continue
        if since and dt.date.fromisoformat(decision.verdict_on) < since:
            continue
        counts["verdicts"] += 1
        if decision.outcome is None:
            counts["unclassified"] += 1
            continue
        detail = counts["outcomes"].setdefault(decision.outcome, {})
        key = decision.qualifier or "sans qualificatif"
        detail[key] = detail.get(key, 0) + 1
    return counts
