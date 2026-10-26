"""`--patch` doit écrire ce qu'on lui cite, et refuser tout le reste.

Le 2026-08-15, recaler `legacy-deadline:19` (1400 caractères d'`item`) d'une date à
une autre a dû passer par un script de relecture ad hoc : l'upsert exigeait TOUS les
champs, donc changer `due_on` obligeait à renvoyer le texte entier. Le risque n'est
pas la date, qu'on relit — c'est le texte long réécrit de mémoire et tronqué en
silence.

Ces tests tiennent les deux bouts. D'un côté la fusion préserve vraiment ce qu'on ne
cite pas, et le payload reste COMPLET (sinon `command_key` et `apply_turn_with_receipts` changent de
contrat sans que personne ne le voie). De l'autre les deux refus tiennent : `--patch`
sur une clé absente, et champ obligatoire manquant sans `--patch` — cette dernière
obligation ayant quitté `required=True` d'argparse, elle n'est plus garantie par la
bibliothèque et doit donc être testée ici.
"""

from __future__ import annotations

import datetime as dt

import pytest
from conftest import load_script

fact_write = load_script("fact-write")


def test_question_review_cli_preserves_partial_and_deferred_states():
    args = parse("question", "--question-id", "q1", "--answer-id", "m1",
                 "--status", "deferred", "--note", "Reprise lundi.",
                 "--follow-up-on", "2026-09-07")
    _, kind, payload = fact_write._payload(args)
    assert kind == "question.review"
    assert payload["status"] == "deferred"
    assert payload["follow_up_on"] == dt.date(2026, 9, 7)


def test_ask_cli_names_only_the_measurement_actually_requested():
    args = parse("ask", "--text", "Quel était ton RPE ?", "--cell", "2026-09-05:EF:RPE")
    _, kind, payload = fact_write._payload(args)
    assert kind == "question.ask"
    assert payload == {"text": "Quel était ton RPE ?", "cells": ["2026-09-05:EF:RPE"]}


def parse(*argv: str):
    return fact_write.parser().parse_args(argv)


def deadline_row(**overrides) -> dict:
    row = {"deadline_key": "d-1", "due_on": dt.date(2026, 8, 11),
           "item": "texte long " * 200, "fact_owner": "`coaching/strength.md`",
           "blocks": "dose pliométrique de W34", "status": "todo",
           "completed_on": None}
    return {**row, **overrides}


def test_patch_ne_touche_que_le_champ_cite():
    existing = deadline_row()
    args = parse("deadline", "--key", "d-1", "--patch", "--due-on", "2026-08-23")
    _, _, payload = fact_write._payload(args, existing)
    assert payload["due_on"] == dt.date(2026, 8, 23)
    assert payload["item"] == existing["item"]
    assert payload["fact_owner"] == existing["fact_owner"]
    assert payload["blocks"] == existing["blocks"]
    assert payload["status"] == "todo"


def test_le_payload_reste_complet_donc_la_mise_en_file_garde_son_contrat():
    """Un payload à trous casserait `apply_turn_with_receipts` sans rien afficher."""
    args = parse("deadline", "--key", "d-1", "--patch", "--due-on", "2026-08-23")
    _, kind, payload = fact_write._payload(args, deadline_row())
    assert kind == "deadline.upsert"
    assert set(payload) == {"deadline_key", "due_on", "item", "fact_owner",
                            "blocks", "status", "completed_on", "kind", "starts_on",
                            "ends_on", "context", "athlete_label"}


def test_patch_sur_un_fil_preserve_le_corps():
    existing = {"thread_key": "t-1", "opened_on": dt.date(2026, 8, 11),
                "kind": "promesse", "body": "corps très long", "status": "ouvert",
                "follow_up_on": dt.date(2026, 8, 12)}
    args = parse("thread", "--key", "t-1", "--patch", "--status", "clos")
    _, _, payload = fact_write._payload(args, existing)
    assert payload["status"] == "clos"
    assert payload["body"] == "corps très long"
    assert payload["kind"] == "promesse"
    assert payload["follow_up_on"] == dt.date(2026, 8, 12)


def test_sans_patch_un_champ_obligatoire_manquant_est_refuse_et_nomme():
    """L'obligation a quitté argparse : si elle n'est plus testée, elle disparaît."""
    args = parse("deadline", "--key", "d-1", "--due-on", "2026-08-23")
    with pytest.raises(SystemExit) as refus:
        fact_write._payload(args, None)
    message = str(refus.value)
    assert "--item" in message and "--fact-owner" in message and "--status" in message
    assert "--due-on" not in message


def test_sans_patch_la_forme_complete_ecrit_toute_la_ligne():
    args = parse("deadline", "--key", "d-1", "--due-on", "2026-08-23",
                 "--item", "neuf", "--fact-owner", "`coaching/strength.md`",
                 "--status", "todo")
    _, _, payload = fact_write._payload(args, None)
    assert payload["item"] == "neuf"
    assert payload["blocks"] is None and payload["completed_on"] is None


def test_patch_sur_une_cle_inexistante_refuse_au_lieu_de_creer_a_trous():
    class Store:
        def deadline_rows(self):
            return [deadline_row(deadline_key="autre")]

    with pytest.raises(SystemExit, match="clé inexistante"):
        fact_write._existing(Store(), "deadline", "d-1")


def test_une_valeur_vide_citee_ecrase_vraiment():
    """`--blocks ''` est une valeur, pas une absence — sinon on ne peut rien vider."""
    args = parse("deadline", "--key", "d-1", "--patch", "--blocks", "")
    _, _, payload = fact_write._payload(args, deadline_row())
    assert payload["blocks"] == ""


def test_exposure_carries_intent_separately_from_the_observed_cadence():
    args = parse(
        "exposure", "--key", "cadence:2026-09-02:ef",
        "--observation-key", "messenger:2026-09-02:ef",
        "--exposure-type", "cadence", "--origin", "voluntary",
        "--status", "confirmed", "--duration-min", "44",
        "--target-value", "172", "--target-unit", "spm", "--tolerance", "easy")

    entity, kind, payload = fact_write._payload(args)

    assert entity == "exposure:cadence:2026-09-02:ef"
    assert kind == "exposure.upsert"
    assert payload["origin"] == "voluntary"
    assert payload["target_value"] == 172


def test_le_libelle_du_carnet_survit_au_patch_et_se_retire_par_une_chaine_vide():
    """Une échéance de Sam garde son libellé quand on décale sa date."""
    existing = deadline_row(athlete_label="Acheter la frontale")
    args = parse("deadline", "--key", "d-1", "--patch", "--due-on", "2026-08-23")
    _, _, payload = fact_write._payload(args, existing)
    assert payload["athlete_label"] == "Acheter la frontale"
    args = parse("deadline", "--key", "d-1", "--patch", "--athlete-label", " ")
    _, _, payload = fact_write._payload(args, existing)
    assert payload["athlete_label"] is None


def test_une_correction_n_entre_en_base_qu_ecrite_au_journal(tmp_path, monkeypatch):
    # Audit du 2026-10-09 : douze corrections n'existaient que dans PostgreSQL, hors de
    # portée du contrôle des porteurs et du registre de rejeu.
    entry = "2026-09-17 — Une distance disponible sur Strava dite invérifiable"
    (tmp_path / "coaching").mkdir()
    (tmp_path / "coaching" / "corrections.md").write_text(f"## {entry}\n\nTexte.\n")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    args = parse("correction", "--correction-key", "k", "--summary", "s", "--entry", entry)
    _, kind, payload = fact_write._payload(args)
    assert kind == "correction.register"
    assert payload == {"correction_key": "k", "summary": "s"}
    with pytest.raises(SystemExit, match="n'est pas une entrée"):
        fact_write._payload(parse("correction", "--correction-key", "k", "--summary", "s",
                                  "--entry", "2026-09-17 — inventée"))


def test_une_prediction_soldee_se_rectifie_avec_son_motif():
    _, _, payload = fact_write._payload(parse("prediction", "--key", "w40", "--rectify",
                                              "condition non tenue"))
    assert payload == {"prediction_key": "w40", "operation": "rectify",
                       "reason": "condition non tenue"}


def test_une_prediction_corps_se_rattache_au_reveil_qui_la_tranche():
    base = ("prediction", "--key", "w42-sl", "--domain", "corps", "--statement", "trace",
            "--probability", "0.75", "--due-on", "2026-10-19")
    _, _, payload = fact_write._payload(parse(*base, "--resolves", "reveil", "--resolves-on",
                                              "2026-10-19", "--success-if", "<=1"))
    assert payload["resolves_on"] == {"kind": "reveil", "date": "2026-10-19",
                                      "success_if": "<=1"}
    assert "resolves_on" not in fact_write._payload(parse(*base))[2]
    with pytest.raises(SystemExit, match="vont ensemble"):
        fact_write._payload(parse(*base, "--resolves", "reveil"))
