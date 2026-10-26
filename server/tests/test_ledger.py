"""Unit tests for scripts/ledger.py -- the load gauge and the trends.

The calibration week (16-22 Feb 2026 = 100 by definition, coaching/protocols.md) is the
non-regression test of the whole gauge: if a change breaks it, the gauge stops meaning
100 = the week that broke Sam, and every reading downstream lies.
"""

import datetime as dt

import pytest
from conftest import RPE_TARGETS, VAULT_ROOT

from scripts import ledger
from scripts.ledger import Instruments, Row
from scripts.model import ledger as table
from scripts.model import programme


def _etalon_rows():
    """A reference week: 47.5 km / ~4.40 h of running over four sessions incl. two
    intensity sessions, ~150 m D+, on three consecutive demanding days, poor sleep."""
    return [
        # D− = D+ : c'étaient des boucles urbaines, donc elles
        # rendaient ce qu'elles montaient. Hypothèse explicite depuis que le terme de
        # dénivelé lit le D− (confrontation n°2, volet dénivelé) : elle laisse l'étalon
        # de février à 100, ce que ce test vérifie.
        Row(dt.date(2026, 2, 17), "seuil", km=11.5, hours=1.10, dplus=50, dminus=50),
        Row(dt.date(2026, 2, 18), "SL", km=15.6, hours=1.40, dplus=50, dminus=50),
        Row(dt.date(2026, 2, 19), "seuil", km=15.9, hours=1.50, dplus=50, dminus=50),
        Row(dt.date(2026, 2, 21), "course", km=4.6, hours=0.40),
    ]


def test_calibration_week_is_100():
    h_eq, level, _ = ledger.equivalent_hours(
        _etalon_rows(), Instruments(sleep="2_3_mauvaises_nuits"), RPE_TARGETS)
    assert abs(h_eq - 7.15) < 0.1, h_eq
    assert abs(level - 100) <= 2, level


def test_gauge_prints_its_inputs():
    _, _, detail = ledger.equivalent_hours(_etalon_rows(),
                                           Instruments(sleep="2_3_mauvaises_nuits"),
                                           RPE_TARGETS)
    assert any("×1.15" in d for d in detail)     # three consecutive demanding days
    assert any("×1.10" in d for d in detail)     # poor sleep
    assert any("h-éq" in d for d in detail)


def test_rated_session_adds_half_an_hour():
    """Garde-fou n°6: a séance run above its RPE target costs +0.5 h-éq."""
    on_target = [Row(dt.date(2026, 3, 2), "seuil", km=8, hours=0.75, rpe=6.0)]
    over = [Row(dt.date(2026, 3, 2), "seuil", km=8, hours=0.75, rpe=8.0)]
    assert ledger.equivalent_hours(over, Instruments(), RPE_TARGETS)[0] == \
        ledger.equivalent_hours(on_target, Instruments(), RPE_TARGETS)[0] + 0.5


# ------------------------------ le terme de dénivelé, et à qui il appartient

def test_a_bike_carries_no_elevation_term():
    """⭐ `protocols.md` § Formule v1 donne le terme de dénivelé à la course (plein) et à
    la rando (moitié). « Vélo Z2 : ×0,5 » n'en porte aucun — le code écrivait `else 1.0`
    et donnait donc au vélo le poids PLEIN d'une course. Sur la sortie du 2026-08-13
    (647 m), ça valait 1,08 h-éq : W33 affichait 87/100 au lieu de 72.

    La raison de fond, tranchée par la confrontation n°2 : ce que le terme approche est la
    charge MÉCANIQUE d'une descente — impact, freinage, excentrique
    ([[src-vernillo-course-en-pente]]). À vélo il n'y a ni l'un ni l'autre. Le coût
    ÉNERGÉTIQUE du dénivelé, lui, est bien comparable entre les deux — mais ce n'est pas
    ce que la formule facture ([[src-minetti-cout-pente]]).
    """
    velo = Row(dt.date(2026, 8, 13), "vélo", hours=1.716, dplus=647.5, dminus=648)
    assert ledger.row_weight(velo, RPE_TARGETS)[0] == pytest.approx(0.858, abs=0.01)


def test_a_hilly_run_keeps_its_full_elevation_term():
    """Le contrôle négatif : retirer le terme au vélo ne le retire à personne d'autre.
    Sur une boucle (D− = D+), le changement de lecture ne bouge rien — c'est le point."""
    course = Row(dt.date(2026, 8, 16), "SL", km=10.3, hours=1.152, dplus=600, dminus=600)
    rando = Row(dt.date(2026, 7, 21), "rando", hours=4.0, dplus=600, dminus=600)
    assert ledger.row_weight(course, RPE_TARGETS)[0] == pytest.approx(2.152, abs=0.01)
    assert ledger.row_weight(rando, RPE_TARGETS)[0] == pytest.approx(2.5, abs=0.01)


def test_every_known_session_type_declares_its_elevation_weight():
    """Un type ajouté demain CHOISIT son coefficient : il ne l'hérite pas d'un `else`.
    C'est exactement le défaut qui a donné au vélo le poids d'une course."""
    assert set(table.SESSION_TYPES) <= set(ledger.DESCENT_WEIGHT)


# ------------------------------------------------- la cible de RPE, source unique

def test_the_targets_come_from_the_workout_notes():
    """`scripts/ledger.py` portait une COPIE en dur des `rpe_cible` (2026-08-08) : six
    de ses huit entrées étaient mortes et quatre fiches n'y figuraient pas, donc un
    renfo au-dessus de sa cible ne déclenchait rien. Les nombres n'existent plus qu'une
    fois. Ce test est le seul de la suite qui lise vraiment les fiches : c'est lui qui
    dit que la table littérale de conftest n'a pas pris du retard."""
    assert programme.rpe_targets(VAULT_ROOT) == RPE_TARGETS


def test_every_effort_type_resolves_a_workout_or_says_it_has_none():
    """Un type d'effort qui ne résout aucune fiche ne se compare à rien, en silence.
    L'ajouter doit forcer le choix ici, comme pour les familles de douleur."""
    unresolved = (set(table.EFFORT_TYPES)
                  - set(programme.WORKOUT_OF_LEDGER_TYPE)
                  - set(programme.TYPES_WITHOUT_NOTE))
    assert not unresolved, f"types sans fiche ni dispense : {sorted(unresolved)}"


def test_a_session_above_its_target_is_flagged_whatever_its_type():
    """Le cas qui a tout déclenché : renfo B du 06/08 à RPE 7 pour une cible de 6."""
    rows = [Row(dt.date(2026, 8, 6), "renfoB", hours=0.73, rpe=7.0),
            Row(dt.date(2026, 7, 31), "renfoB", hours=0.42, rpe=3.0)]
    flags = ledger.over_target(rows, RPE_TARGETS)
    assert [f.date for f in flags] == [dt.date(2026, 8, 6)]
    assert "RPE 7 déclaré pour une cible de 6" in str(flags[0])


def test_the_gauge_penalty_stays_restricted_to_quality_sessions():
    """Signaler n'est pas pénaliser. Le +0,5 « séance ratée » a été calibré sur les
    seules qualités ; l'étendre aux renfos re-échelonnerait la jauge, ce qui est une
    décision de coaching et pas un effet de bord de ce correctif."""
    over = [Row(dt.date(2026, 8, 6), "renfoB", hours=1.0, rpe=9.0)]
    on_target = [Row(dt.date(2026, 8, 6), "renfoB", hours=1.0, rpe=6.0)]
    assert ledger.equivalent_hours(over, Instruments(), RPE_TARGETS)[0] == \
        ledger.equivalent_hours(on_target, Instruments(), RPE_TARGETS)[0]
    assert ledger.over_target(over, RPE_TARGETS)      # mais le signal, lui, part


def test_rando_is_half_weighted():
    rando = [Row(dt.date(2026, 3, 2), "rando", km=20, hours=4.0, dplus=600, dminus=600)]
    h_eq, _, _ = ledger.equivalent_hours(rando, Instruments(), RPE_TARGETS)
    assert h_eq == 4.0 * 0.5 + (600 / 600) * 0.5     # 2.0 + 0.5 = 2.5


def test_a_typed_strength_report_enters_the_gauge_without_a_second_observation():
    class Store:
        def observation_rows(self):
            return []

        def strength_history(self):
            return [{
                "performed_on": dt.date(2026, 8, 26), "workout": "a",
                "duration_minutes": 60, "shin_after": 1,
                "note": "Planche unipodale trop dure.",
            }]

    rows = ledger.parse_ledger(Store())

    assert rows == [Row(
        dt.date(2026, 8, 26), "renfoA", hours=1.0, pain=1.0,
        note="Planche unipodale trop dure.")]
    assert ledger.row_weight(rows[0], RPE_TARGETS)[0] == 0.5


def test_an_old_strength_observation_wins_instead_of_being_counted_twice():
    class Store:
        def observation_rows(self):
            return [{
                "occurred_on": dt.date(2026, 8, 18), "kind": "renfoA",
                "measurements": {
                    "duration_s": {"state": "known", "value": 3540},
                    "rpe": {"state": "known", "value": 7},
                    "pain": {"state": "known", "value": 1},
                },
                "note": "Observation historique enrichie.",
            }]

        def strength_history(self):
            return [{
                "performed_on": dt.date(2026, 8, 18), "workout": "a",
                "duration_minutes": 59, "shin_after": 1, "note": "Rapport page.",
            }]

    rows = ledger.parse_ledger(Store())

    assert len(rows) == 1
    assert rows[0].rpe == 7
    assert rows[0].note == "Observation historique enrichie."


def test_consecutive_hard_days_detected():
    rows = [Row(dt.date(2026, 2, 20), "SL", hours=6.0),
            Row(dt.date(2026, 2, 21), "rando", hours=2.5)]
    assert ledger._two_hard_days_in_a_row(rows)
    spaced = [Row(dt.date(2026, 2, 20), "SL", hours=6.0),
              Row(dt.date(2026, 2, 23), "rando", hours=2.5)]
    assert not ledger._two_hard_days_in_a_row(spaced)


def test_ratio_over_four_previous_weeks():
    per_week = {"2026-W10": 2.0, "2026-W11": 2.0, "2026-W12": 2.0,
                "2026-W13": 2.0, "2026-W14": 4.0}
    assert ledger.acute_chronic_ratio(per_week, "2026-W14") == 2.0


def test_ratio_counts_an_empty_week_as_zero():
    """A week with no session is a real 0 in the chronic load, not a week to skip.
    Skipping it inflates the denominator and hides the alarm -- and that alarm is the
    March 2026 relapse: a jump right after an almost empty week."""
    with_gap = {"2026-W10": 2.0, "2026-W11": 2.0, "2026-W12": 2.0,
                "2026-W14": 4.0}                      # W13 = total rest
    # chronic = (2 + 2 + 0 + 2) / 4 = 1.5  ->  4 / 1.5 = 2.67, not 2.0
    assert ledger.acute_chronic_ratio(with_gap, "2026-W14") == 2.67


def test_ratio_ignores_weeks_before_the_ledger_starts():
    """No data is not a zero: the first weeks must not be padded with rest."""
    assert ledger.acute_chronic_ratio({"2026-W10": 2.0, "2026-W11": 4.0},
                                       "2026-W11") == 2.0


# --------------------------------------------------------------------------- pain

def test_pain_trend_reports_the_weekly_peak():
    """A median over a week of zero-pain hikes is 0 by construction: it reported W30 at
    0.0 while the two most recent readings were 1.5 and 1-1.5."""
    rows = [Row(dt.date(2026, 7, 20), "rando", pain=0.0),
            Row(dt.date(2026, 7, 21), "rando", pain=0.0),
            Row(dt.date(2026, 7, 22), "lecture", pain=1.5),
            Row(dt.date(2026, 7, 23), "EF", pain=1.5)]
    week = ledger.pain_trend(rows, 1)[0]
    assert week.effort.peak == 1.5
    assert week.effort.median == 0.0        # trois séances : 0, 0, 1.5
    assert week.effort.n == 3


def test_pain_during_effort_and_at_rest_are_two_series():
    """Pooled, then reduced to a weekly max, the two answer neither question: on
    2026-07-29 a 1.5 felt in the climbs and a 0.5 read cold that evening came out as a
    single « pic 1.5 » -- the same number an untouched shin at rest would have given."""
    rows = [Row(dt.date(2026, 7, 29), "côtes", pain=1.5),
            Row(dt.date(2026, 7, 29), "lecture", pain=0.5),
            Row(dt.date(2026, 7, 30), "reveil", pain=0.5)]
    week = ledger.pain_trend(rows, 1)[0]
    assert week.effort.peak == 1.5 and week.effort.n == 1
    assert week.rest.peak == 0.5 and week.rest.n == 2      # lecture + reveil
    assert week.wake.peak == 0.5 and week.wake.n == 1      # le réveil seul


def test_a_peak_is_printed_with_its_count():
    """A max over three readings and a max over seven are not comparable: without n,
    the more diligently Sam declares, the worse his trend looks."""
    week = ledger.pain_trend([Row(dt.date(2026, 7, 29), "EF", pain=1.0)], 1)[0]
    assert "n=1" in str(week)


def test_a_rest_reading_never_enters_the_gauge():
    for kind in ("lecture", "reveil"):
        row = Row(dt.date(2026, 7, 29), kind, hours=0.5, pain=1.0)
        assert ledger.row_weight(row, RPE_TARGETS) == (0.0, False)


def test_a_palpation_enters_neither_pain_series():
    """Une douleur PROVOQUÉE à la pression n'est ni un pic d'effort ni une lecture
    spontanée au repos : c'est une localisation. Les séries étaient définies par
    négation (`not in REST_TYPES`), donc un type nouveau tombait en silence dans la
    série d'effort — celle qui juge le injury (2026-08-08)."""
    week = ledger.pain_trend([Row(dt.date(2026, 7, 29), "EF", pain=1.0),
                              Row(dt.date(2026, 7, 30), "palpation", pain=4.0)], 1)[0]
    assert week.effort.peak == 1.0
    assert week.rest.peak is None


def test_a_palpation_is_not_a_session_the_esculier_rule_can_judge():
    rows = [Row(dt.date(2026, 7, 30), "palpation", pain=4.0)]
    assert ledger.esculier_check(rows) == []


def test_a_palpation_requires_no_cell():
    """Écrite en `lecture`, la palpation du 06/08 réclamait un `Dlr` sur une échelle
    qui n'existe pas pour elle."""
    assert table.required_columns("palpation") == ()
    assert table.required_columns("lecture") == ("Dlr",)


# ---------------------------------------------------------------------- Esculier

def test_a_morning_above_its_own_rest_series_is_flagged():
    """The morning is judged against the rest series, and here there is none to speak of:
    a 2.0 with no history behind it is named once."""
    rows = [Row(dt.date(2026, 7, 30), "EF", pain=1.0),
            Row(dt.date(2026, 7, 31), "reveil", pain=2.0)]
    flags = ledger.esculier_check(rows)
    assert [f.kind for f in flags] == ["TRACE AU RÉVEIL"]
    assert "2.0/10 au réveil" in flags[0].detail


def test_a_morning_inside_its_own_range_no_longer_fires_at_all():
    """The 2026-08-11 failure, fixed at the trigger instead of in the message (2026-08-17).

    A `reveil` at 2.0 was read as a change of category and a stress-fracture hypothesis was
    carried to Sam, while the rest series had sat between 0.5 and 2.0 for five weeks.
    The first patch printed the series alongside the flag; the flag still fired, because it
    compared the morning to the previous day's EFFORT peak -- two currencies, one ruler.
    Sam's /10 scale is written for effort only (2026-08-17), so that subtraction is gone:
    2.0 equals the peak of its own eight weeks, therefore it is not news, therefore silence.
    """
    rows = [Row(dt.date(2026, 7, 9), "lecture", pain=2.0),
            Row(dt.date(2026, 7, 22), "lecture", pain=1.5),
            Row(dt.date(2026, 7, 29), "lecture", pain=0.5),
            Row(dt.date(2026, 8, 10), "EF", pain=1.0),
            Row(dt.date(2026, 8, 11), "reveil", pain=2.0)]
    assert ledger.esculier_check(rows) == []


def test_a_morning_that_leaves_its_own_range_still_fires_with_its_series():
    """Above the rest peak, it is a signal -- and it goes out with the series that frames
    it, so the reader can see how far out it actually is."""
    rows = [Row(dt.date(2026, 7, 9), "lecture", pain=2.0),
            Row(dt.date(2026, 7, 22), "lecture", pain=1.5),
            Row(dt.date(2026, 8, 10), "EF", pain=1.0),
            Row(dt.date(2026, 8, 11), "reveil", pain=2.5)]
    flags = ledger.esculier_check(rows)
    assert [f.kind for f in flags] == ["TRACE AU RÉVEIL"]
    assert "pic 2.0" in flags[0].detail
    assert "médiane 1.75" in flags[0].detail


def test_a_calm_morning_after_a_painful_session_is_not_a_violation():
    """The session hurt more than the morning: that is the rule being PASSED, and it used
    to be the only way to stay silent."""
    rows = [Row(dt.date(2026, 8, 10), "EF", pain=2.0),
            Row(dt.date(2026, 8, 11), "reveil", pain=0.0)]
    assert ledger.esculier_check(rows) == []


def test_the_baseline_only_looks_backwards():
    """A reading cannot be its own reference: the morning being judged, and everything
    after it, stay out of the window."""
    rows = [Row(dt.date(2026, 8, 10), "EF", pain=1.0),
            Row(dt.date(2026, 8, 11), "reveil", pain=2.0),
            Row(dt.date(2026, 8, 12), "lecture", pain=4.0)]
    detail = ledger.esculier_check(rows)[0].detail
    assert "aucun historique de repos" in detail


def test_the_baseline_window_forgets_old_readings():
    """Eight weeks: a rest series from last spring says nothing about this block."""
    rows = [Row(dt.date(2026, 5, 1), "lecture", pain=3.0),
            Row(dt.date(2026, 8, 10), "EF", pain=1.0),
            Row(dt.date(2026, 8, 11), "reveil", pain=2.0)]
    assert "aucun historique de repos" in ledger.esculier_check(rows)[0].detail


def test_a_session_with_pain_and_no_morning_reading_is_flagged():
    """An untested rule is not a passed rule."""
    rows = [Row(dt.date(2026, 7, 30), "côtes", pain=1.5)]
    assert [f.kind for f in ledger.esculier_check(rows)] == ["non vérifié"]


def test_the_missing_reading_flag_does_not_judge_the_past():
    """Before `reveil` existed, no morning reading could have been taken. Flagging
    those rows would print six lines nobody can act on, every single run."""
    rows = [Row(dt.date(2026, 7, 10), "EF", pain=1.5)]
    assert ledger.esculier_check(rows) == []


def test_pain_above_the_cap_is_flagged_whatever_the_next_day_says():
    rows = [Row(dt.date(2026, 7, 30), "EF", pain=3.0),
            Row(dt.date(2026, 7, 31), "reveil", pain=0.0)]
    assert [f.kind for f in ledger.esculier_check(rows)] == ["AU-DESSUS DU PLAFOND"]


def test_the_descent_of_konigssee_no_longer_weighs_zero():
    """⭐ Le renversement de la confrontation n°2 (volet dénivelé, 2026-08-16).

    Le 21/07, la descente Gotzenalm → lac : **1096 m de D− pour 20 m de D+**. L'ancienne
    formule ne comptait que le D+, donc la journée la plus chargée mécaniquement de
    l'été pesait **zéro** de dénivelé. Le lendemain de cette même journée, la montée
    pure du 19/07 (1108 m de D+, 17 m de D−) était, elle, surfacturée.

    Ce que les heures ne captent pas, c'est la descente : on y va plus vite, donc elle
    coûte peu de temps, alors qu'elle coûte le plus en mécanique.
    """
    rows = [Row(dt.date(2026, 7, 21), "rando", hours=2.5, dplus=20, dminus=1096)]
    assert ledger.week_descent(rows) == 1096
    assert ledger.equivalent_hours(rows, Instruments(), RPE_TARGETS)[0] == \
        pytest.approx(1.25 + (1096 / 600) * 0.5, abs=0.01)


def test_a_pure_climb_is_no_longer_billed_as_a_descent():
    """Le contrôle symétrique : la montée du 19/07 (1108 m D+, 17 m D−) est payée par
    le temps qu'elle prend, pas par un terme de dénivelé qui la compte deux fois."""
    rows = [Row(dt.date(2026, 7, 19), "rando", hours=4.0, dplus=1108, dminus=17)]
    assert ledger.equivalent_hours(rows, Instruments(), RPE_TARGETS)[0] == \
        pytest.approx(2.0 + (17 / 600) * 0.5, abs=0.01)


# ------------------------------------------------------------------------ the volume

def _w31_rows():
    """W31 as it really happened -- the week the coach announced as « 22 km »."""
    return [
        Row(dt.date(2026, 7, 28), "EF", km=7.19, hours=0.756, dplus=44, dminus=41),
        Row(dt.date(2026, 7, 29), "côtes", km=8.32, hours=0.805, dplus=175, dminus=176),
        Row(dt.date(2026, 7, 31), "renfoB", hours=0.416),
        Row(dt.date(2026, 8, 1), "EF", km=9.00, hours=0.969, dplus=87, dminus=87),
    ]


def test_week_volume_is_the_sum_of_the_ledger_not_the_plan():
    """2026-08-02: « 22 km » announced for a 24.51 km week. The 22 was the `Vol. cible`
    of current-plan.md, quoted as a fact. The gauge has to be able to print the real
    number, or the only figure at hand stays the planned one."""
    volume = ledger.week_volume(_w31_rows())
    assert volume.km_run == 24.51
    assert volume.runs == 3                      # renfoB carries no km
    assert "24.51 km courus (3 sorties)" in str(volume)


def test_rounding_does_not_accumulate_over_a_week():
    """Sam's hypothesis, tested against Strava's own metres: 8995.5 + 8316.3 +
    7189.9 = 24501.7 m. The ledger's two decimals cost 8 m ON THE WEEK, and the error
    is bounded per session, not cumulative -- the 2.5 km gap was never rounding."""
    strava_m = [8995.5, 8316.3, 7189.9]
    assert abs(ledger.week_volume(_w31_rows()).km_run - sum(strava_m) / 1000) < 0.01


def test_walking_km_are_counted_apart_from_running_km():
    rows = [Row(dt.date(2026, 7, 20), "rando", km=12.0, hours=4.0),
            Row(dt.date(2026, 7, 22), "EF", km=6.0, hours=0.6)]
    volume = ledger.week_volume(rows)
    assert (volume.km_run, volume.km_walk) == (6.0, 12.0)
    assert "marchés" in str(volume)


def test_an_unknown_week_is_refused_never_answered_with_zero():
    """`--semaine 31` used to match no row, print « 0.00 h-éq (0/100) », and look
    exactly like a rest week. A reader that meets a non-conforming input REFUSES."""
    known = ["2026-W30", "2026-W31"]
    assert ledger.normalize_week("31", known) == "2026-W31"
    assert ledger.normalize_week("w30", known) == "2026-W30"
    assert ledger.normalize_week("2026-W31", known) == "2026-W31"
    for bad in ["99", "2026-W29", "bidule", ""]:
        try:
            ledger.normalize_week(bad, known)
        except ValueError:
            continue
        raise AssertionError(f"« {bad} » accepté au lieu d'être refusé")


def _week_of_runs(monday: dt.date, kms: list[float]) -> list[Row]:
    return [Row(monday + dt.timedelta(days=i * 2), "EF", km=km, hours=km / 10)
            for i, km in enumerate(kms)]


def test_the_week_in_progress_is_marked_and_kept_out_of_the_slope():
    """Objection de Sam, 2026-08-20 : « on est jeudi donc forcément le volume est
    plus faible ». Une semaine ouverte comparée à des semaines closes fabrique un
    effondrement qui n'existe pas — c'est l'erreur que cet instrument doit rendre
    impossible, pas reproduire."""
    rows: list[Row] = []
    monday = dt.date(2026, 7, 6)          # 2026-W28, un lundi
    for index in range(6):
        rows += _week_of_runs(monday + dt.timedelta(weeks=index), [6.0, 6.0, 8.0])
    thursday = monday + dt.timedelta(weeks=6, days=3)
    rows += _week_of_runs(monday + dt.timedelta(weeks=6), [5.0])

    traces = ledger.trajectory(rows, thursday)
    assert traces[-1].open and traces[-1].elapsed == 4
    assert all(not trace.open for trace in traces[:-1])
    # Six semaines closes restent disponibles malgré la semaine ouverte : sans ça le
    # verdict ne se calculerait jamais un jour de bilan.
    assert sum(1 for trace in traces if not trace.open) == ledger.TRAJECTORY_WEEKS
    verdict = ledger.trajectory_verdict(traces)
    assert not any("non calculable" in line for line in verdict)
    # Semaines closes toutes identiques : la pente est nulle, et le 5 km du jeudi ne
    # doit surtout pas la faire plonger.
    assert all("+0 %" in line for line in verdict)


def test_the_longest_of_thirty_days_is_a_landmark_not_a_ceiling():
    """Aucun pourcentage fixe (décision du 2026-09-27) : le registre donne le repère,
    rien ne le transforme en plafond."""
    today = dt.date(2026, 8, 20)
    rows = [Row(dt.date(2026, 8, 16), "SL", km=10.31, hours=1.4),
            Row(dt.date(2026, 8, 19), "EF", km=7.04, hours=0.75),
            Row(dt.date(2026, 6, 30), "SL", km=22.0, hours=2.5)]   # hors fenêtre
    assert ledger.longest_recent(rows, today) == 10.31
    assert ledger.longest_recent([], today) is None
    assert not hasattr(ledger, "next_long_run_cap")


# ------------------------------------------------ scénario 9 : la pente, calculée

def _long_runs(series: dict[str, float]) -> list[Row]:
    """Une sortie longue le dimanche de chaque semaine, plus une EF courte le mardi."""
    rows = []
    for label, km in series.items():
        monday = ledger._week_start(label)
        rows.append(Row(monday + dt.timedelta(days=1), "EF", km=6.0, hours=0.6))
        rows.append(Row(monday + dt.timedelta(days=6), "SL", km=km, hours=km / 8))
    return rows


# La série réelle de W33 à W39 (journaux), W36 inconnue dans Git et posée à 10,4.
SEPTEMBER = {"2026-W33": 10.31, "2026-W34": 7.0, "2026-W35": 10.41, "2026-W36": 10.4,
             "2026-W37": 11.53, "2026-W38": 10.80, "2026-W39": 10.5}


def test_the_old_three_against_three_verdict_reads_a_stall_as_a_rise():
    """Le défaut que ce bloc corrige : au bilan W40 (dimanche 04/10), la plus longue
    n'a plus bougé depuis le record de W37, et le verdict 3 contre 3 affiche pourtant
    environ +11 %. Le compteur de stagnation, lui, dit la vérité ; il se déclenche
    dès que W40 se clôt sans record."""
    rows = _long_runs({**SEPTEMBER, "2026-W40": 11.0})
    bilan_w40 = dt.date(2026, 10, 4)
    verdict = ledger.trajectory_verdict(ledger.trajectory(rows, bilan_w40))
    longest_line = next(line for line in verdict if line.startswith("Plus longue"))
    assert "+11 %" in longest_line
    stall = ledger.long_run_stall(rows, bilan_w40)
    assert stall is not None and stall.weeks == 2 and not stall.triggered
    stall = ledger.long_run_stall(rows, dt.date(2026, 10, 5))
    assert stall is not None and stall.triggered
    assert stall.weeks == 3 and stall.record_week == "2026-W37"
    assert "protocole d'instruction des baisses" in str(stall)


def test_a_stall_counts_closed_weeks_only_and_a_record_resets_it():
    rows = _long_runs(SEPTEMBER)
    # Dimanche 27/09 : W39 est en cours, donc seule W38 est close après le record.
    stall = ledger.long_run_stall(rows, dt.date(2026, 9, 27))
    assert stall is not None and stall.weeks == 1 and not stall.triggered
    # Lundi 28/09 : W39 close, deux semaines sans record.
    assert ledger.long_run_stall(rows, dt.date(2026, 9, 28)).weeks == 2
    # Un vrai record efface le compteur ; 11,6 contre 11,53 est du bruit GPS.
    noisy = _long_runs({**SEPTEMBER, "2026-W40": 11.6})
    assert ledger.long_run_stall(noisy, dt.date(2026, 10, 5)).weeks == 3
    record = _long_runs({**SEPTEMBER, "2026-W40": 12.6})
    assert ledger.long_run_stall(record, dt.date(2026, 10, 5)).weeks == 0


def test_a_week_without_any_run_is_not_progress():
    rows = _long_runs({"2026-W36": 10.0, "2026-W37": 11.0})
    rows += _long_runs({"2026-W40": 9.0})
    stall = ledger.long_run_stall(rows, dt.date(2026, 10, 5))
    assert stall is not None and stall.weeks == 3        # W38 et W39 vides, W40 plus courte


def test_the_projection_says_what_a_target_would_require_not_what_is_allowed():
    rows = _long_runs(SEPTEMBER)
    projection = ledger.race_projection(rows, dt.date(2026, 9, 27), dt.date(2026, 11, 28))
    assert projection is not None
    assert projection.start_km == 11.53
    # W40 à W45 inclus : l'affûtage commence en W46 pour une course en W48.
    assert projection.weeks == [
        "2026-W40", "2026-W41", "2026-W42", "2026-W43", "2026-W44", "2026-W45"]
    assert projection.taper_from == "2026-W46"
    assert round(projection.required_growth(30.0), 3) == round((30 / 11.53) ** (1 / 6), 3)
    assert projection.required_growth(10.0) == 1.0
    assert projection.required_growth(30.0, lost=6) is None
    lines = ledger.projection_lines(projection, target_km=30.0)
    assert any("+17 %/sem" in line and "+21 %/sem" in line for line in lines)
    assert not any("10 %" in line for line in lines)
    assert "--cible-sl" in ledger.projection_lines(projection)[-1]
    assert ledger.race_projection(rows, dt.date(2026, 12, 1), dt.date(2026, 11, 28)) is None


def test_the_race_date_comes_from_a_typed_race_deadline_only():
    class Store:
        def deadline_rows(self):
            return [
                {"kind": "general", "status": "open", "due_on": dt.date(2026, 10, 4)},
                {"kind": "race", "status": "done", "due_on": dt.date(2026, 9, 1)},
                {"kind": "race", "status": "open", "due_on": "2026-11-28"},
            ]
    today = dt.date(2026, 9, 27)
    assert ledger.next_race_date(Store(), today) == dt.date(2026, 11, 28)
    Store.deadline_rows = lambda self: []  # type: ignore[method-assign]
    assert ledger.next_race_date(Store(), today) is None


def test_a_planned_run_shows_its_gap_to_the_month_without_any_verdict():
    rows = _long_runs(SEPTEMBER)
    sessions = [
        {"id": "sl-dim", "kind": "run", "status": "planned", "date": "2026-10-04",
         "distance_cap_km": 14.0},
        {"id": "ef-mar", "kind": "run", "status": "cancelled", "date": "2026-09-29",
         "distance_cap_km": 7.0},
        {"id": "renfo", "kind": "strength", "status": "planned", "date": "2026-09-30"},
        {"id": "ef-passee", "kind": "run", "status": "planned", "date": "2026-09-20",
         "distance_cap_km": 7.0},
    ]
    notes = ledger.transition_notes(rows, sessions, dt.date(2026, 9, 27))
    assert notes == ["sl-dim 14.0 km = +21 % de 11.5 km (plus longue des 30 j)"]


def test_an_undeclared_week_is_neither_ok_nor_multiplied():
    # Audit du 2026-10-09 : le bilan W40 écrivait « sommeil ok, charge normale » pour une
    # semaine sans aucune déclaration.
    class Store:
        def weekly_instrument_rows(self):
            return [{"iso_week": "2026-W39", "sleep": None, "life_load": None}]

        def daily_rows(self):
            return [{"checked_on": dt.date(2026, 10, 8), "sleep": "bonne",
                     "life_load": None, "desire": None}]

    loaded = ledger.load_instruments(Store())
    assert loaded["2026-W39"] == Instruments(sleep=None, life_load=None)
    assert loaded["2026-W41"] == Instruments(sleep="ok", life_load=None)
    rows = _etalon_rows()
    assert ledger.equivalent_hours(rows, Instruments(), RPE_TARGETS)[0] == \
        ledger.equivalent_hours(rows, Instruments(sleep="ok", life_load="normale"),
                                RPE_TARGETS)[0]


def _gate_rows(*, peak_before=1.0, peak_now=1.0, wake_now=(1.0,)):
    rows = [Row(dt.date(2026, 9, 1), "reveil", pain=1.0),
            Row(dt.date(2026, 9, 29), "EF", km=8, hours=0.8, pain=peak_before),
            Row(dt.date(2026, 10, 6), "EF", km=8, hours=0.8, pain=peak_now)]
    rows += [Row(dt.date(2026, 10, 7 + i), "reveil", pain=value)
             for i, value in enumerate(wake_now)]
    return rows


def test_the_gate_is_green_when_every_case_is_established():
    lines = ledger.gate(_gate_rows(), "2026-W41", Instruments(sleep="ok"))
    assert lines[0] == "Porte (règle po, calculée) : verte"


def test_a_rise_of_half_a_point_closes_the_gate():
    lines = ledger.gate(_gate_rows(peak_now=1.5), "2026-W41", Instruments(sleep="ok"))
    assert lines[0].endswith(": rouge")
    assert "  ✗ tendance : 1 → 1.5" in lines


def test_a_wake_told_in_prose_is_not_a_measured_wake():
    # W40 : « réveils à la base » lu dans la prose, aucune valeur chiffrée au registre.
    lines = ledger.gate(_gate_rows(wake_now=(None, None)), "2026-W41",
                        Instruments(sleep="ok"))
    assert lines[0].endswith(": incomplète")
    assert any(line.startswith("  ? réveils : 0 chiffré(s) sur 2") for line in lines)


def test_an_undeclared_sleep_leaves_the_gate_incomplete_not_green():
    lines = ledger.gate(_gate_rows(), "2026-W41", Instruments())
    assert lines[0].endswith(": incomplète")
    assert "  ? sommeil non déclaré" in lines
