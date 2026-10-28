"""Ce que la CLI rend au coach APRÈS qu'il a écrit une révision.

Le 2026-08-25, Sam annonce en un message trois faits : repos aujourd'hui, renfo
demain matin, sortie avec des amis jeudi. La révision 3 n'en encode qu'un, et son
propre motif affirme les trois. La 4 en corrige deux et reconduit l'oubli du jeudi.
Entre les deux, la CLI a répondu « révision N enregistrée et validée » : le coach n'a
jamais relu l'objet qu'il venait d'écrire, il a raconté celui qu'il croyait avoir écrit.

⭐ Une révision se relit avant de se raconter, et la relecture montre la semaine ENTIÈRE
   -- jours vides et séances annulées compris, seuls endroits où un oubli se voit.
"""

import pytest

from scripts.model import weekly_plan


def _session(session_id, date, title="EF", status="planned", workout="easy", routine=None):
    session = {"id": session_id, "date": date, "title": title, "kind": "run",
               "workout": workout, "status": status, "demanding": False}
    if routine:
        session["routine"] = routine
    return session


R4 = {
    "revision": 4,
    "sessions": [
        _session("w35-mer-a", "2026-08-26", "Renfo A", workout="strength", routine="a"),
        _session("w35-jeu-b", "2026-08-27", "Renfo B", status="cancelled",
                 workout="strength", routine="b"),
        _session("w35-ven-ef", "2026-08-28"),
    ],
}


def test_le_jour_ampute_se_voit_dans_la_relecture():
    """Le trou du 25/08 : jeudi n'a plus qu'une annulation, et ça se lit."""
    lignes = weekly_plan.revision_week_lines("2026-W35", R4)

    assert "jeu 27/08 : Renfo B (cancelled)" in lignes


def test_un_jour_sans_aucune_seance_se_nomme():
    lignes = weekly_plan.revision_week_lines("2026-W35", R4)

    assert "mar 25/08 : AUCUNE SÉANCE" in lignes
    assert "dim 30/08 : AUCUNE SÉANCE" in lignes


def test_la_relecture_couvre_les_sept_jours():
    assert len(weekly_plan.revision_week_lines("2026-W35", R4)) == 7


@pytest.mark.parametrize("mutation, attendu", [
    ({"sessions": [*R4["sessions"], _session("w35-jeu-jog", "2026-08-27", "EF amis")]},
     "w35-jeu-jog : AJOUTÉE le jeu 27/08 (EF amis)"),
    ({"sessions": [{**R4["sessions"][0], "date": "2026-08-25"}, *R4["sessions"][1:]]},
     "w35-mer-a : DÉPLACÉE du mer 26/08 au mar 25/08"),
    ({"sessions": [*R4["sessions"][:2],
                   {**R4["sessions"][2], "status": "cancelled"}]},
     "w35-ven-ef : ANNULÉE le ven 28/08 (EF)"),
    ({"sessions": [*R4["sessions"][:2],
                   {**R4["sessions"][2], "distance_cap_km": 8.0}]},
     "w35-ven-ef : MODIFIÉE le ven 28/08 (EF)"),
    ({"sessions": R4["sessions"][:2]},
     "w35-ven-ef : RETIRÉE de la révision (elle était le ven 28/08)"),
])
def test_le_diff_nomme_ce_qui_a_bouge(mutation, attendu):
    r5 = {**R4, "revision": 5, **mutation}

    assert attendu in weekly_plan.revision_diff(R4, r5)


def test_une_revision_qui_ne_change_rien_le_dit_franchement():
    """Le cas le plus dangereux : le coach croit avoir consigné, l'objet est identique."""
    texte = weekly_plan.revision_readback("2026-W35", R4, {**R4, "revision": 5})

    assert "AUCUNE séance ajoutée, annulée, déplacée ni modifiée" in texte


def test_la_premiere_revision_na_pas_de_predecesseur():
    texte = weekly_plan.revision_readback("2026-W35", None, R4)

    assert "rien (première révision)" in texte
    assert "w35-mer-a : AJOUTÉE" in texte


def test_la_relecture_reclame_une_relecture():
    """Un rendu que le modèle survole ne sert à rien : il porte sa propre consigne."""
    texte = weekly_plan.revision_readback("2026-W35", R4, {**R4, "revision": 5})

    assert "RELIS LA SEMAINE AVANT DE RÉPONDRE" in texte
    assert "Chaque fait du message de l'athlète doit se retrouver ci-dessus" in texte


R2 = {
    "revision": 2,
    "sessions": [
        _session("w35-mar-a", "2026-08-25", "Renfo A", workout="strength", routine="a"),
        _session("w35-mer-ef", "2026-08-26"),
        _session("w35-jeu-b", "2026-08-27", "Renfo B", workout="strength", routine="b"),
    ],
}


def _r3(reason, sessions=None):
    return {"revision": 3, "reason": reason,
            "sessions": sessions if sessions is not None else [
                {**s, "status": "cancelled"} if s["id"] == "w35-mer-ef" else s
                for s in R2["sessions"]]}


def test_la_r3_reelle_du_25_aout_est_refusee():
    """Son motif annonçait le jeudi ; son objet ne touchait que le mercredi."""
    fautes = weekly_plan.coherence_errors("2026-W35", R2, _r3(
        "Sam a couru avec des amis jeudi matin au lieu de l'EF de mercredi ; le "
        "renfo B du 27/08 est decale, date a arbitrer au moment venu."))

    assert len(fautes) == 1
    assert "jeu 27/08" in fautes[0]


@pytest.mark.parametrize("reason", [
    "L'EF de mercredi est annulée.",
    "L'EF du 26/08 est annulée.",
    "L'EF du 2026-08-26 est annulée.",
])
def test_un_motif_qui_ne_nomme_que_le_jour_touche_passe(reason):
    """Les trois écritures d'un jour se valent : nom, date courte, date ISO."""
    assert weekly_plan.coherence_errors("2026-W35", R2, _r3(reason)) == []


def test_un_jour_hors_semaine_ne_compte_pas():
    """« l'avis du 03/09 » parle d'une autre semaine : ce plan ne peut pas le porter."""
    assert weekly_plan.coherence_errors(
        "2026-W35", R2, _r3("L'EF de mercredi saute ; avis clinique le 03/09.")) == []


def test_la_premiere_revision_nest_pas_jugee_sur_ses_jours():
    """Elle les touche tous par construction : la règle n'y dirait rien."""
    assert weekly_plan.coherence_errors("2026-W35", None, _r3("lundi mardi dimanche")) == []


def test_la_faute_ouvre_les_deux_chemins_corrects():
    """Un garde-fou qui ne dit que non fabrique son contournement (règle du 06/08)."""
    faute = weekly_plan.coherence_errors("2026-W35", R2, _r3("Rien ne bouge dimanche."))[0]

    assert "retire-le du motif" in faute
    assert "`constraints`" in faute


def test_un_jour_vide_par_cette_revision_est_signale():
    orphelins = weekly_plan.sessions_orphelines(R2, _r3("mercredi"))

    assert orphelins == ["mer 26/08 ne porte plus que des annulations (EF) et aucune "
                         "séance à la place. Voulu, ou remplaçante oubliée ?"]


def test_un_jour_deja_vide_a_la_revision_precedente_ne_se_resignale_pas():
    """Le signal porte sur ce que CETTE révision fait, pas sur un état hérité."""
    deja = _r3("mercredi")

    assert weekly_plan.sessions_orphelines(deja, {**deja, "revision": 4}) == []


def test_a_future_change_requires_exact_evidence_rule_and_fields():
    current = _r3("mercredi")
    current.update({
        "computed_at": "2026-08-25T10:00:00+02:00",
        "evidence": ["messenger:mid-42"],
        "changes": [{
            "session_id": "w35-mer-ef", "fields": ["status"],
            "trigger": "messenger:mid-42", "context": "indisponibilité déclarée",
            "rule": "retirer la séance impossible",
            "motif": {"regle": "demande", "fait": "messenger:mid-42"},
        }],
    })

    assert weekly_plan.change_evidence_errors(R2, current) == []


def test_a_session_done_today_is_not_a_change_to_justify():
    """27/10 : l'EF et le renfo du matin, rendus `done` par le cycle, rendaient
    impossible toute révision du jour, `done` étant refusé à l'écriture."""
    previous = {"revision": 3, "sessions": [
        _session("w44-mar-ef", "2026-10-27", status="done"),
        _session("w44-jeu-sl", "2026-10-29", workout="long")]}
    current = {"revision": 4, "computed_at": "2026-10-27T11:00:00+01:00",
               "sessions": [_session("w44-mar-ef", "2026-10-27"),
                            _session("w44-jeu-sl", "2026-10-29", workout="long")]}

    assert weekly_plan.change_evidence_errors(previous, current) == []


def _cancel(evidence, motif=None):
    current = _r3("mercredi")
    entry = {"session_id": "w35-mer-ef", "fields": ["status"], "trigger": evidence[0],
             "context": "séance du mercredi", "rule": "annuler"}
    if motif is not None:
        entry["motif"] = motif
    current.update({"computed_at": "2026-08-25T10:00:00+02:00", "evidence": evidence,
                    "changes": [entry]})
    return current


def test_a_restriction_without_a_typed_motif_is_refused():
    """Audit du 2026-10-09 : 19 restrictions sans prémisse, dont 7 par une révision."""
    errors = weekly_plan.change_evidence_errors(R2, _cancel(["observation:2026-08-24-ef"]))

    assert any("w35-mer-ef.motif : restriction (annulée) sans motif typé" in error
               for error in errors)
    assert any("Sans prémisse, la séance reste" in error for error in errors)


def test_a_rule_motif_must_cite_an_observed_fact():
    evidence = ["observation:2026-08-24-ef", "messenger:mid-42"]
    good = _cancel(evidence, {"regle": "n3", "fait": "observation:2026-08-24-ef"})
    vague = _cancel(evidence, {"regle": "n3", "fait": "messenger:mid-42"})
    absent = _cancel(evidence, {"regle": "n3", "fait": "observation:inventée"})

    assert weekly_plan.change_evidence_errors(R2, good) == []
    assert any("s'applique à un fait observé" in error
               for error in weekly_plan.change_evidence_errors(R2, vague))
    assert any("motif.fait doit recopier" in error
               for error in weekly_plan.change_evidence_errors(R2, absent))


def test_a_request_motif_must_come_from_a_message():
    current = _cancel(["observation:2026-08-24-ef"],
                      {"regle": "demande", "fait": "observation:2026-08-24-ef"})

    assert any("regle=demande exige un message" in error
               for error in weekly_plan.change_evidence_errors(R2, current))


def test_an_unknown_rule_id_is_refused_by_the_schema():
    entry = _cancel(["observation:x-1"], {"regle": "prudence", "fait": "observation:x-1"})

    assert weekly_plan.CHANGE_SCHEMA.errors(entry["changes"][0], "c")


@pytest.mark.parametrize("before, after, attendu", [
    ({"distance_cap_km": 10.0}, {"distance_cap_km": 8.0}, "plafond de distance abaissé"),
    ({"distance_target_km": 10.0}, {"distance_target_km": 10.0, "distance_cap_km": 8.0},
     "plafond de distance abaissé"),
    ({"distance_target_km": 12.0}, {"distance_target_km": 9.0}, "cible de distance abaissée"),
    ({"duration_cap_min": 60}, {"duration_cap_min": 45}, "plafond de durée abaissé"),
    ({"workout": "long"}, {"workout": "easy"}, "SL remplacée par EF"),
    ({"strength": {}}, {"strength": {"drop": ["rdl"]}}, "exercice retiré du renfo"),
    ({"strength": {}}, {"strength": {"half_dose": True}}, "renfo en demi-dose"),
])
def test_every_kind_of_cut_is_a_restriction(before, after, attendu):
    old = {**_session("s", "2026-08-26"), **before}
    assert weekly_plan.restrictions(old, {**old, **after}) == [attendu]


@pytest.mark.parametrize("before, after", [
    ({"distance_cap_km": 8.0}, {"distance_cap_km": 10.0}),
    ({"distance_target_km": 9.0}, {"distance_target_km": 10.0}),
    ({"date": "2026-08-26"}, {"date": "2026-08-27"}),
    ({"workout": "easy"}, {"workout": "long"}),
    ({"status": "cancelled"}, {"status": "cancelled", "note": "toujours annulée"}),
])
def test_adding_moving_or_lengthening_is_not_a_restriction(before, after):
    old = {**_session("s", "2026-08-26"), **before}
    assert weekly_plan.restrictions(old, {**old, **after}) == []


def test_a_removed_session_is_a_restriction():
    assert weekly_plan.restrictions(_session("s", "2026-08-26"), None) == ["retirée"]


def test_a_raw_correction_cannot_be_the_trigger_of_a_plan_change():
    current = _r3("mercredi")
    current.update({
        "computed_at": "2026-08-25T10:00:00+02:00",
        "evidence": ["correction:la cadence était volontaire"],
        "changes": [{
            "session_id": "w35-mer-ef", "fields": ["status"],
            "trigger": "correction:la cadence était volontaire",
            "context": "intention de cadence corrigée",
            "rule": "annuler la séance",
        }],
    })

    errors = weekly_plan.change_evidence_errors(R2, current)
    assert any("preuve autoritative" in error for error in errors)


def test_change_fields_must_match_the_actual_object():
    current = _r3("mercredi")
    current.update({
        "computed_at": "2026-08-25T10:00:00+02:00",
        "evidence": ["request:annulation explicite"],
        "changes": [{
            "session_id": "w35-mer-ef", "fields": ["date"],
            "trigger": "request:annulation explicite", "context": "demande confirmée",
            "rule": "annuler",
        }],
    })

    errors = weekly_plan.change_evidence_errors(R2, current)
    assert any("attendu ['status']" in error for error in errors)


def test_une_annulation_remplacee_le_meme_jour_ne_se_signale_pas():
    remplace = _r3("mercredi", sessions=[
        *[{**s, "status": "cancelled"} if s["id"] == "w35-mer-ef" else s
          for s in R2["sessions"]],
        _session("w35-mer-jog", "2026-08-26", "EF amis")])

    assert weekly_plan.sessions_orphelines(R2, remplace) == []
