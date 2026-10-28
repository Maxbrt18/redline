"""`decisions-index.py --due` : une prédiction se ferme, et la clôture est datée.

Sans marqueur de clôture, `--due` réaffichait indéfiniment ce qui avait déjà été jugé.
Au bilan du 2026-08-16 il listait sept prédictions, dont trois échues depuis W30 et W32 :
le bilan hebdo ne les lisait pas, et quand il a fini par les lire, le seul moyen de les
faire taire aurait été de repousser leur semaine de revue — c'est-à-dire de réécrire
l'histoire pour éteindre un voyant.
"""

import datetime as dt

import pytest
from conftest import VAULT_ROOT, load_script

decisions_index = load_script("decisions-index")

APRES = dt.date(2026, 8, 16)

ENTREE = """## 2026-07-29 (soir) — Trois instruments rallumés

**Prédiction falsifiable** : RPE renseigné sur ≥90 % des lignes. Revue : bilan W33.
"""

VERDICT = "\n**Verdict** (2026-08-16) : ✅ tenue — 93 %.\n"


def _decision(texte: str):
    (decision,) = decisions_index.parse(texte)
    return decision


def test_une_prediction_echue_sans_verdict_reste_due():
    decision = _decision(ENTREE)
    assert decision.review_week == 33
    assert decision.verdict_on is None
    assert decision.is_due(APRES)


def test_un_verdict_date_ferme_la_prediction():
    decision = _decision(ENTREE + VERDICT)
    assert decision.verdict_on == "2026-08-16"
    assert not decision.is_due(APRES)
    # La semaine de revue ne bouge pas : on ferme la prédiction, on ne la redate pas.
    assert decision.review_week == 33


def test_l_index_montre_la_cloture_plutot_que_la_cloche():
    ouvert = decisions_index.render_index([_decision(ENTREE)], APRES)
    ferme = decisions_index.render_index([_decision(ENTREE + VERDICT)], APRES)
    assert "W33 🔔" in ouvert
    assert "W33 ✔ 2026-08-16" in ferme
    assert "🔔" not in ferme


def test_prediction_courte_et_predictions_au_pluriel_sont_indexees():
    texte = """## 2026-08-23 — Libellé court

**Prédiction** : un résultat mesurable. Revue : bilan W38.

## 2026-08-22 — Libellé pluriel

**Prédictions** : deux résultats mesurables. Revue : bilan W39.
"""
    decisions = decisions_index.parse(texte)
    assert [decision.has_prediction for decision in decisions] == [True, True]
    assert [decision.review_week for decision in decisions] == [38, 39]


def test_la_semaine_dans_le_libelle_de_prediction_est_indexee():
    decision = _decision("""## 2026-08-23 — Revue dans le titre de rubrique

**Prédiction (revue au bilan W38, 2026-09-20).** Le signal change.
""")
    assert decision.has_prediction
    assert decision.review_week == 38


def test_une_rubrique_mal_fermee_refuse_au_lieu_de_disparaitre():
    texte = """## 2026-08-23 — Rubrique cassée

**Prédiction : ce texte ne ferme jamais le gras. Revue W38.
"""
    with pytest.raises(decisions_index.DecisionFormatError, match="rubrique non indexée"):
        decisions_index.parse(texte)


def test_toutes_les_rubriques_du_vault_reel_sont_indexables():
    """Garantie de forme, pas verdict calendaire : le contenu change sans le code."""
    texte = (VAULT_ROOT / decisions_index.DECISIONS).read_text(encoding="utf-8")
    decisions = decisions_index.parse(texte)
    assert sum(len(decision.predictions) for decision in decisions) == len(
        decisions_index.PREDICTION_START.findall(texte)
    )


def test_une_decision_volumineuse_se_retrouve_sans_relire_tout_le_journal():
    decisions = decisions_index.parse(
        "## 2026-08-24 — Cockpit minimal\n\nLe test du pied est en pause.\n\n"
        "## 2026-08-23 — Autre sujet\n\nAucune mention.\n")
    matches = decisions_index.find(decisions, "test du pied")
    assert [decision.title for decision in matches] == ["Cockpit minimal"]


def test_le_retard_se_compte_depuis_la_fin_de_la_semaine_de_revue():
    decision = _decision(ENTREE)                        # revue W33, close le 16/08
    assert decisions_index.overdue_days(decision, dt.date(2026, 8, 30)) == 14
    assert decisions_index.overdue_days(decision, dt.date(2026, 8, 10)) == 0
    assert decisions_index.overdue_days(_decision(ENTREE + VERDICT),
                                        dt.date(2026, 9, 30)) == 0


def test_le_modele_et_la_cli_partagent_le_meme_parseur():
    """vault-check lit la dette par le modèle ; l'index par la CLI : un seul parseur."""
    from scripts.model import decisions
    assert decisions_index.parse is decisions.parse
    assert "**Verdict** (2026-09-27)" in decisions.verdict_template(dt.date(2026, 9, 27))


def test_un_pointeur_postgresql_reste_visible_sans_creer_une_seconde_dette():
    decision = _decision("""## 2026-08-24 — Cockpit

**Prédiction PostgreSQL** : `cockpit-minimal-w38`. Revue W38, verdict dans PostgreSQL.
""")
    assert decision.has_prediction
    assert not decision.is_due(dt.date(2026, 10, 2))
    index = decisions_index.render_index([decision], dt.date(2026, 10, 2))
    assert "W38 ↗ PostgreSQL" in index
    assert "🔔" not in index


def test_un_pointeur_postgresql_ne_cache_pas_la_dette_narrative_de_la_meme_decision():
    decision = _decision(ENTREE + "\n**Prédiction PostgreSQL** : `autre-test`. Revue W39.\n")
    assert decision.review_week == 33
    assert decision.is_due(APRES)


# --- Calibration : l'issue d'un verdict est un vocabulaire fermé (décision 2026-10-04) ---

def _jugee(verdict: str):
    return _decision(ENTREE + f"\n**Verdict** {verdict}\n")


def test_un_verdict_recent_porte_son_issue_et_son_qualificatif():
    refutee = _jugee("(2026-10-11) : réfutée (optimiste) — 3 séances sur 6.")
    assert (refutee.outcome, refutee.qualifier) == ("réfutée", "optimiste")
    non_testable = _jugee("(2026-10-11) : non testable (instrument) — aucune mesure.")
    assert (non_testable.outcome, non_testable.qualifier) == ("non testable", "instrument")
    tenue = _jugee("(2026-10-11) : tenue — 5 sur 6.")
    assert (tenue.outcome, tenue.qualifier) == ("tenue", None)


@pytest.mark.parametrize("verdict", [
    "(2026-10-11) : réfutée — sans le sens de l'erreur.",
    "(2026-10-11) : non testable — sans cause.",
    "(2026-10-11) : non testable (dans son ensemble) — cause hors liste.",
    "(2026-10-11) : tenue (optimiste) — une tenue n'a pas de sens d'erreur.",
    "(2026-10-11) : ✅ **tenue** — décor autour de l'issue.",
])
def test_un_verdict_recent_hors_vocabulaire_est_refuse(verdict):
    with pytest.raises(decisions_index.DecisionFormatError, match="hors vocabulaire"):
        decisions_index.parse(ENTREE + f"\n**Verdict** {verdict}\n")


def test_l_historique_en_prose_libre_reste_lisible_sans_qualificatif_devine():
    libre = _jugee("(2026-08-16) : ✅ **tenue** — 93 %.")
    assert libre.verdict_on == "2026-08-16" and libre.outcome is None
    tete = _jugee("(2026-09-27) : non testable sur le quantificateur « 100 % » — détail.")
    assert (tete.outcome, tete.qualifier) == ("non testable", None)


def test_le_score_compte_les_issues_et_se_filtre_par_date_de_verdict():
    decisions = [_jugee("(2026-08-16) : tenue — ancien."),
                 _jugee("(2026-10-11) : réfutée (optimiste) — récent."),
                 _jugee("(2026-10-11) : non testable (portée) — récent."),
                 _jugee("(2026-08-20) : ✅ **tenue** — libre."),
                 _decision(ENTREE)]
    tous = decisions_index.calibration(decisions)
    assert tous["verdicts"] == 4 and tous["unclassified"] == 1
    assert tous["outcomes"]["tenue"] == {"sans qualificatif": 1}
    recents = decisions_index.calibration(decisions, dt.date(2026, 10, 5))
    assert recents["verdicts"] == 2
    assert recents["outcomes"] == {"réfutée": {"optimiste": 1}, "non testable": {"portée": 1}}
    rendu = "\n".join(decisions_index.render_calibration(decisions))
    assert "tenue sur les testables : 1/2 = 50%" in rendu
    assert "non testable : 50% (plafond 20%) ⚠" in rendu


def test_le_gabarit_de_verdict_annonce_les_qualificatifs_exiges():
    gabarit = decisions_index.verdict_template(dt.date(2026, 10, 11))
    assert "réfutée (optimiste | prudente)" in gabarit
    assert "non testable (instrument | exposition | portée | remplacée)" in gabarit
    # Le gabarit lui-même, une fois rempli, passe le parseur.
    _jugee("(2026-10-11) : réfutée (prudente) — mieux que prédit.")


def test_la_semaine_ecrite_apres_revue_prime_sur_les_autres_semaines_citees():
    # Le palier de W42 se revoyait « au bilan W36 » ; la plus grande semaine citée le
    # repoussait à W42, six semaines trop tard (audit du 2026-10-09).
    decision = _decision("""## 2026-08-06 — Le palier de W42

**Prédiction falsifiable** : W42 est figée avant W38. Échec si ouvert au bilan W40.
Revue : bilan W36.
""")
    assert decision.review_week == 36


def test_une_prediction_ouverte_sans_semaine_de_revue_est_refusee():
    with pytest.raises(decisions_index.DecisionFormatError, match="sans semaine de revue"):
        decisions_index.parse("""## 2026-09-12 — Revue par événement

**Prédiction** : sur les trois prochaines SL, RPE ≤4. Revue : après la troisième SL.
""")


def test_une_revue_fixee_date_une_prediction_historique_sans_la_reecrire():
    decision = _decision("""## 2026-09-12 — Revue par événement

**Prédiction** : sur les trois prochaines SL, RPE ≤4. Revue : après la troisième SL.

**Revue fixée** (2026-10-09) : W42 — deux SL faites.
""")
    assert decision.review_week == 42
    assert decision.is_due(dt.date(2026, 10, 18))


def test_une_prediction_soldee_sans_semaine_reste_acceptee():
    decision = _decision("""## 2026-07-22 — Revue par événement

**Prédiction** : à l'appel, une valeur chiffrée.

**Verdict** (2026-10-09) : tenue — valeur chiffrée le 27/08.
""")
    assert decision.outcome == "tenue"


def test_un_verdict_rectifie_fait_foi_sans_effacer_l_original():
    decision = _decision(ENTREE + VERDICT
                         + "\n**Verdict rectifié** (2026-10-09) : réfutée (optimiste) — "
                           "8/10 au lieu de 6-7.\n")
    # La dette reste soldée à la date d'origine ; l'issue est celle de la rectification.
    assert decision.verdict_on == "2026-08-16"
    assert (decision.outcome, decision.qualifier) == ("réfutée", "optimiste")
    assert "✅ tenue" in decision.body


def test_une_rectification_obeit_au_vocabulaire_strict():
    with pytest.raises(decisions_index.DecisionFormatError, match="hors vocabulaire"):
        decisions_index.parse(ENTREE + VERDICT
                              + "\n**Verdict rectifié** (2026-10-09) : réfutée — sans sens.\n")


def test_une_rectification_sans_verdict_d_origine_est_refusee():
    with pytest.raises(decisions_index.DecisionFormatError, match="sans verdict d'origine"):
        decisions_index.parse(ENTREE + "\n**Verdict rectifié** (2026-10-09) : tenue — x.\n")
