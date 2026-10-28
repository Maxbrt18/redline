"""The operational queue of predictions comes from PostgreSQL, not from the Markdown journal."""

import datetime as dt

from conftest import load_script

fact_read = load_script("fact-read")


class Store:
    def prediction_rows(self):
        return [
            {
                "prediction_key": "due-open", "domain": "adherence",
                "probability": 0.7, "due_on": dt.date(2026, 8, 24),
                "status": "open", "brier_score": None, "challenge": None,
                "used_at": None, "statement": "à juger",
            },
            {
                "prediction_key": "future", "domain": "charge",
                "probability": 0.6, "due_on": dt.date(2026, 9, 1),
                "status": "open", "brier_score": None, "challenge": None,
                "used_at": None, "statement": "plus tard",
            },
            {
                "prediction_key": "closed", "domain": "charge",
                "probability": 0.5, "due_on": dt.date(2026, 8, 1),
                "status": "resolved", "brier_score": 0.25, "resolves_kind": "wake-trace",
                "resolves_on": dt.date(2026, 7, 31), "success_op": "<=", "success_value": 1, "challenge": None,
                "used_at": None, "statement": "déjà jugée",
            },
        ]


def test_due_ne_rend_que_les_predictions_ouvertes_arrivees_a_echeance():
    lines = fact_read.predictions(Store(), due=True, today=dt.date(2026, 8, 24))
    assert lines[0] == "Prédictions échues (1)"
    assert "due-open" in lines[1]
    assert all("future" not in line and "closed" not in line for line in lines)


def test_strength_publie_le_fait_canonique_sans_le_reconstruire():
    class StrengthStore:
        def strength_history(self):
            return [{
                "performed_on": dt.date(2026, 8, 26), "workout": "a",
                "duration_minutes": 60, "pains": {"genou": {"before": 0, "after": 1}},
                "session_key": "w35-mer-ef-2026-08-26",
                "note": "Une série de planche faite sur les deux pieds.",
                "exercises": [{
                    "exercise_slug": "plank-toe", "difficulty": 8,
                    "skipped": False,
                    "sets": [{"side": "both", "set_index": 1, "repetitions": 30}],
                }],
            }]

    lines = fact_read.strength(StrengthStore(), 3)

    text = "\n".join(lines)
    assert "w35-mer-ef-2026-08-26" in text
    assert "douleur genou 0->1/10" in text
    assert "plank-toe · difficulté 8/10 · séries both=30" in text
    assert "Une série de planche faite sur les deux pieds" in text


def test_corrections_accepte_les_dates_deja_normalisees_par_le_depot():
    class CorrectionStore:
        def correction_rows(self):
            return [{
                "correction_key": "ef-intermittente",
                "delivered_at": "2026-08-28T05:30:00+00:00",
                "summary": "Le pic intermittent ne suffit pas à retirer l'EF.",
            }]

    lines = fact_read.corrections(CorrectionStore(), 20)

    assert lines == [
        "Corrections enregistrées (1)",
        "ef-intermittente · livrée le 2026-08-28T05:30:00+00:00 · "
        "Le pic intermittent ne suffit pas à retirer l'EF.",
    ]


def test_une_correction_jamais_annoncee_dans_sa_semaine_le_dit():
    class CorrectionStore:
        def correction_rows(self):
            return [
                {"correction_key": "vieille", "delivered_at": None, "revisions": 2,
                 "registered_at": "2026-09-01T08:00:00+00:00",
                 "first_registered_at": "2026-08-20T08:00:00+00:00", "summary": "Récidive."},
                {"correction_key": "recente", "delivered_at": None, "revisions": 0,
                 "registered_at": "2026-10-08T08:00:00+00:00", "summary": "Nouvelle."},
            ]

    lines = fact_read.corrections(CorrectionStore(), 20,
                                  now=dt.datetime(2026, 10, 9, tzinfo=dt.UTC))

    assert lines[1] == ("vieille · non annoncée, délai dépassé · 2 récidive(s) depuis le "
                        "2026-08-20 · Récidive.")
    assert lines[2] == "recente · à annoncer · Nouvelle."


def test_expositions_ne_rend_par_defaut_que_les_faits_confirmes():
    class ExposureStore:
        def exposure_rows(self, confirmed_only=False):
            assert confirmed_only
            return [{
                "occurred_on": "2026-09-02", "exposure_type": "cadence",
                "origin": "voluntary", "status": "confirmed", "duration_min": 44,
                "target_value": 172, "target_unit": "spm", "tolerance": "easy",
                "observation_key": "messenger:2026-09-02:ef",
                "exposure_key": "cadence:2026-09-02:ef", "note": "",
            }]

    text = "\n".join(fact_read.exposures(ExposureStore(), 20, False))

    assert "Expositions confirmées (1)" in text
    assert "voluntary" in text and "172 spm" in text


def test_la_calibration_par_domaine_montre_ce_qui_echappe_au_score():
    lines = fact_read.predictions(Store(), today=dt.date(2026, 8, 25))
    assert "se solde au réveil du 2026-07-31 si <=1" in lines[3]
    assert lines[-2:] == [
        "Calibration adherence · soldées 0 (dont 0 par observation) · void 0 · "
        "échues ouvertes 1 · Brier moyen —",
        "Calibration charge · soldées 1 (dont 1 par observation) · void 0 · "
        "échues ouvertes 0 · Brier moyen 0.250"]
