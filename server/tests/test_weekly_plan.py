"""Le contrat relationnel du programme hebdomadaire et ses projections."""

import datetime as dt
import io
import json

import pytest

from scripts.model import session_kinds, weekly_plan
from scripts.model.schema import SchemaError


def session(identifier="w32-lun-ef", date="2026-08-03", workout="easy", **overrides):
    payload = {
        "id": identifier, "title": overrides.pop("title", workout), "date": date,
        "kind": session_kinds.family(workout),
        "workout": workout, "status": "planned", "distance_target_km": 7.0,
        "distance_cap_km": 9.0, "rpe_min": 2, "rpe_max": 3,
        "demanding": workout in {"long", "threshold", "hills"},
        "sources": ["[[current-plan]]"],
    }
    if payload["kind"] != "run":
        for key in ("distance_target_km", "distance_cap_km", "rpe_min", "rpe_max"):
            payload.pop(key)
    if workout == session_kinds.STRENGTH:
        payload["strength"] = {}
        payload["routine"] = "a"
    payload.update(overrides)
    return payload


def revision(number=1, sessions=None, **overrides):
    payload = {
        "revision": number, "computed_at": "2026-08-03T12:00:00+02:00",
        "reason": "recalcul depuis le réalisé", "evidence": ["registre W31"],
        "constraints": ["douleur >2/10"], "sessions": sessions or [session()],
    }
    payload.update(overrides)
    return payload


def plan(sessions=None):
    return {"week": "2026-W32", "revisions": [revision(sessions=sessions)]}


def gate(identifier="w32-injury-48h", due="2026-08-08",
         question="trace de douleur encore présente au réveil ?",
         on_fail="repos et recalcul", **overrides):
    payload = {"id": identifier, "due": due, "when": "apres",
               "question": question, "on_fail": on_fail}
    payload.update(overrides)
    return payload


LE_JOUR_DE_LECRITURE = "2026-08-04T09:00:00+02:00"


def written_today(**overrides):
    overrides.setdefault("volume_target_km", 30.0)
    return revision(computed_at=LE_JOUR_DE_LECRITURE, **overrides)


def frozen_clock(monkeypatch, week_plan):
    """`revise` horodate lui-même depuis le 25/08 : sans horloge figée, une révision
    de test pour W32 serait jugée écrite aujourd'hui, et ses contrôles datés périmés."""
    monkeypatch.setattr(week_plan, "now_stamp", lambda: LE_JOUR_DE_LECRITURE)


def with_session(**session_overrides):
    return {"week": "2026-W32",
            "revisions": [written_today(sessions=[session(**session_overrides)])]}


def test_remaining_drops_what_is_settled_and_what_is_behind():
    """⭐ Le calcul unique de « ce qui reste à faire », et la raison d'exister de ce
    module : trois rendus le refaisaient à la main, et celui qui affirmait le plus fort
    (« la suite de la semaine », sur le téléphone de Sam) était le seul à ignorer
    `status`. Le 2026-08-16 il lui a redonné à faire la SL qu'il venait de courir.
    """
    ran = session("w33-dim-sl", date="2026-08-16", workout="long", status="done")
    dropped = session("w33-jeu-b", date="2026-08-13", workout="strength", routine="b", status="cancelled")
    ahead = session("w33-lun-ef", date="2026-08-17")
    behind = session("w33-sam-ef", date="2026-08-15")
    sessions = [ran, dropped, ahead, behind]

    assert weekly_plan.remaining(sessions, since="2026-08-16") == [ahead]
    assert weekly_plan.scheduled(sessions) == [ran, ahead, behind]
    # Borne inclusive : la journée en cours reste à faire tant qu'elle n'est pas faite.
    assert ahead in weekly_plan.remaining(sessions, since="2026-08-17")


def test_only_a_settled_status_leaves_the_prescription():
    """Écrit en négatif exprès : un statut ajouté plus tard reste prescriptible, parce
    qu'oublier une séance à faire est pire que d'en afficher une de trop."""
    assert set(weekly_plan.STATUSES) > weekly_plan.SETTLED_STATUSES
    for status in weekly_plan.STATUSES:
        kept = weekly_plan.remaining([session(status=status)], since="2026-08-01")
        assert bool(kept) is (status not in weekly_plan.SETTLED_STATUSES)


def test_valid_plan_passes():
    assert weekly_plan.active(weekly_plan.validate(plan()))["revision"] == 1


def test_a_new_revision_refuses_session_results_in_intent_text():
    current = written_today(sessions=[session(
        note="Réalisée le 04/08 : 7,02 km, FC moy 153, WBGT 22.")])
    errors = weekly_plan.intention_errors(current, "W32 révision 2")
    assert any("résultat recopié dans le plan" in error for error in errors)


def test_a_new_revision_keeps_forward_looking_session_notes():
    current = written_today(sessions=[session(
        note="EF plate ; se consigne au registre après coup.")])
    assert weekly_plan.intention_errors(current, "W32 révision 2") == []


def test_a_new_revision_refuses_a_companion_name_in_the_plan():
    current = written_today(sessions=[session(
        note="Footing avec Alexis, allure libre.")])
    errors = weekly_plan.intention_errors(current, "W32 révision 2")
    assert any("nom de tiers 'Alexis'" in error for error in errors)


def test_the_athlete_name_is_allowed_in_plan_evidence():
    current = written_today(evidence=["Réponse de Sam enregistrée dans PostgreSQL"])
    assert weekly_plan.intention_errors(current, "W32 révision 2") == []


def test_a_session_time_is_optional_but_strictly_local():
    assert weekly_plan.validate(plan([session(start_time="18:30")]))
    with pytest.raises(SchemaError, match="HH:MM"):
        weekly_plan.validate(plan([session(start_time="18h30")]))


def test_location_and_gut_target_are_typed():
    long_run = session("w32-dim-sl", "2026-08-09", "long", start_time="08:00",
                       location_label="Bois de Vincennes", carbs_target_g_h=60)
    assert weekly_plan.validate(plan([long_run]))
    with pytest.raises(SchemaError, match="cible digestive"):
        weekly_plan.validate(plan([session(carbs_target_g_h=60)]))


@pytest.mark.parametrize("retired", [
    {"latitude": 48.85, "longitude": 2.35}, {"mental_cue_minute": 90},
    {"weather": {"wbgt_max_c": 24, "on_alert": "Décale le départ à 7 h."}},
])
def test_retired_proactive_fields_are_refused_not_silently_stored(retired):
    """Repère mental, coordonnées et météo sont retirés le 2026-09-28 : un plan qui les
    écrit encore doit échouer bruyamment, pas porter un réglage que rien ne lit."""
    with pytest.raises(SchemaError):
        weekly_plan.validate(plan([session("sl", "2026-08-09", "long",
                                           start_time="08:00", **retired)]))


def test_current_schema_owns_strength_modulation():
    strength = session(
        "w32-jeu-a", "2026-08-06", "strength", routine="a", kind="strength",
        demanding=False)
    strength.pop("strength")
    revision = written_today(sessions=[strength])

    with pytest.raises(SchemaError, match="strength manque"):
        weekly_plan.validate({"week": "2026-W32", "revisions": [revision]})

    strength["strength"] = {}
    assert weekly_plan.validate({"week": "2026-W32", "revisions": [revision]})


def test_active_strength_variant_must_exist_in_the_programme():
    strength = session(
        "w32-jeu-a", "2026-08-06", "strength", routine="a", kind="strength",
        demanding=False, strength={"variant": "full"})
    with pytest.raises(SchemaError, match="variante « full » non déclarée"):
        weekly_plan.validate(plan([strength]), today=dt.date(2026, 8, 4))


def test_active_strength_drop_must_name_an_exercise_in_the_programme():
    strength = session(
        "w32-jeu-b", "2026-08-06", "strength", routine="b", kind="strength",
        demanding=False, strength={"drop": ["box-drop"]})
    with pytest.raises(SchemaError, match="box-drop n'est pas dans la séance b"):
        weekly_plan.validate(plan([strength]), today=dt.date(2026, 8, 4))


def test_a_closed_week_is_not_judged_against_todays_strength_programme():
    """W39 : un « drop » d'exercice retiré du programme après coup vidait l'archive."""
    strength = session(
        "w32-jeu-b", "2026-08-06", "strength", routine="b", kind="strength",
        demanding=False, strength={"drop": ["box-drop"]})
    assert weekly_plan.validate(plan([strength]), today=dt.date(2026, 8, 10))


def test_an_invalid_historical_strength_variant_does_not_freeze_the_append_only_plan():
    broken = revision(1, sessions=[session(
        "w32-jeu-a", "2026-08-06", "strength", routine="a", kind="strength",
        demanding=False, strength={"variant": "full"})])
    repaired = revision(2, sessions=[session(
        "w32-jeu-a", "2026-08-06", "strength", routine="a", kind="strength",
        demanding=False, strength={})])
    assert weekly_plan.validate(
        {"week": "2026-W32", "revisions": [broken, repaired]})


@pytest.mark.parametrize("obsolete", [
    "active_revision", "ledger_rows", "daily_through", "replan_triggers", "catch_up",
])
def test_obsolete_plan_fields_are_refused(obsolete):
    candidate = plan()
    target = candidate if obsolete == "active_revision" else candidate["revisions"][0]
    target[obsolete] = 1
    with pytest.raises(SchemaError, match=f"champ « {obsolete} » inconnu"):
        weekly_plan.validate(candidate)


def test_target_above_cap_is_refused():
    broken = plan([session(distance_target_km=10.0, distance_cap_km=9.0)])
    with pytest.raises(SchemaError, match="au-dessus du plafond"):
        weekly_plan.validate(broken)


def test_human_title_is_required_and_is_the_rendered_label():
    custom = session(title="Footing souple")
    assert weekly_plan.validate(plan([custom]))["revisions"][0]["sessions"][0]["title"] \
        == "Footing souple"
    missing = dict(custom)
    del missing["title"]
    with pytest.raises(SchemaError, match="title"):
        weekly_plan.validate(plan([missing]))


def test_a_note_that_cites_a_gate_id_is_refused():
    broken = with_session(note="Repos. Réévaluation demain (gate w33-renfoa-report).")
    broken["revisions"][0]["gates"] = [{
        "id": "w33-renfoa-report", "due": "2026-08-08",
        "when": "apres",
        "question": "La blessure au réveil, comparé à hier ?",
        "on_fail": "Renfo A reste suspendu."}]
    with pytest.raises(SchemaError, match="contrôle daté"):
        weekly_plan.validate(broken)


def test_a_note_that_cites_a_revision_number_is_refused():
    with pytest.raises(SchemaError, match="cite une révision"):
        weekly_plan.validate(with_session(note="Reporté (trigger replan de rev.1)."))


@pytest.mark.parametrize("text", ["Forme corrigee : step-down 30 cm",
                                  "SL vallonnee", "Sac leste gele"])
def test_schema_does_not_guess_french_spelling(text):
    assert weekly_plan.validate(with_session(note=text))


def test_every_revision_shares_the_visible_text_rule():
    broken = {"week": "2026-W32",
              "revisions": [revision(
                  sessions=[session(note="SL vallonnee (gate w32-injury)")],
                  gates=[gate(identifier="w32-injury")])]}
    with pytest.raises(SchemaError, match="contrôle daté"):
        weekly_plan.validate(broken)


def test_a_session_outside_its_iso_week_is_refused():
    with pytest.raises(SchemaError, match="hors de 2026-W32"):
        weekly_plan.validate(plan([session(date="2026-08-10")]))


def test_two_real_hard_days_in_a_row_are_refused_but_easy_jogs_are_not():
    weekly_plan.validate(plan([session("lun", "2026-08-03"),
                               session("mar", "2026-08-04")]))
    hard = [session("lun-sl", "2026-08-03", "long"),
            session("mar-seuil", "2026-08-04", "threshold")]
    with pytest.raises(SchemaError, match="jours exigeants consécutifs"):
        weekly_plan.validate(plan(hard))


def test_a_routine_keeps_the_gap_its_note_declares():
    """renfo-b.md declares `min_gap_h: {strength: 48, long: 48}`."""
    with pytest.raises(SchemaError, match="routine b à moins de 48 h d'une séance long"):
        weekly_plan.validate(plan([
            session("lun-sl", "2026-08-03", "long"),
            session("mar-b", "2026-08-04", "strength", routine="b"),
        ]), today=dt.date(2026, 8, 3))
    assert weekly_plan.validate(plan([
        session("lun-sl", "2026-08-03", "long"),
        session("mer-b", "2026-08-05", "strength", routine="b"),
    ]), today=dt.date(2026, 8, 3))


def test_due_gate_is_exposed_only_when_due():
    current = week_with_a_later_session([precondition()])
    assert weekly_plan.gate_reasons(current, today=dt.date(2026, 8, 7)) == []
    reasons = weekly_plan.gate_reasons(current, today=dt.date(2026, 8, 8))
    assert reasons[0].startswith("contrôle daté du 2026-08-08 non levé")
    assert "DEMANDE À L'ATHLÈTE" in reasons[0]
    assert "jamais en le retirant du programme" in reasons[0]


def test_a_gate_is_lifted_only_by_a_new_revision_without_it():
    current = week_with_a_later_session([precondition()])
    assert weekly_plan.gate_reasons(current, today=dt.date(2026, 8, 8))
    current["revisions"].append(revision(
        2, [session(status="done")], computed_at="2026-08-08T08:30:00+02:00",
        reason="genou calme au réveil du 08/08, contrôle levé"))
    assert weekly_plan.gate_reasons(current, today=dt.date(2026, 8, 8)) == []


def test_a_gate_beyond_sunday_belongs_to_the_echeancier():
    broken = plan()
    broken["revisions"][0]["gates"] = [gate(due="2026-08-11")]
    with pytest.raises(SchemaError, match=r"fact-write\.py deadline"):
        weekly_plan.validate(broken)


def test_a_gate_already_expired_when_written_is_refused():
    broken = plan()
    broken["revisions"][0]["gates"] = [gate(due="2026-08-03")]
    broken["revisions"][0]["computed_at"] = "2026-08-06T17:15:00+02:00"
    with pytest.raises(SchemaError, match="antérieure au calcul"):
        weekly_plan.validate(broken)


def test_renderers_show_only_the_active_revision_and_postgresql_owner():
    current = plan()
    current["revisions"].append(revision(
        2, [session("new-sl", "2026-08-03", "long", distance_target_km=10.0,
                    distance_cap_km=10.0, status="done")]))
    journal = weekly_plan.render_journal(current)
    hot = weekly_plan.render_hot_cache(current)
    assert "révision 2" in journal and "10 km" in journal
    assert "PostgreSQL →" in hot and "≤10 km" in hot


# --------------------------------------------------- codes de sortie de la CLI

def _week_plan_cli():
    """Charge `scripts/week-plan.py`, dont le tiret interdit un import normal."""
    import importlib.util

    from conftest import ENGINE_ROOT
    spec = importlib.util.spec_from_file_location(
        "week_plan_cli", ENGINE_ROOT / "scripts" / "week-plan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeStore:
    def __init__(self, plan, needs_arbitration=False):
        self._plan = plan
        self._needs_arbitration = needs_arbitration

    def load_plan(self, week):
        return self._plan

    def plan_needs_arbitration(self, week, revision):
        return self._needs_arbitration


def _w32_plan(**revision_overrides):
    return {"week": "2026-W32", "revisions": [revision(**revision_overrides)]}


def test_a_prescriptible_week_exits_zero_and_prints_the_plan(capsys):
    cli = _week_plan_cli()
    store = _FakeStore(_w32_plan())
    assert cli._database_command(store, "show", "2026-W32", False) == cli.EXIT_OK
    assert "w32-lun-ef" in capsys.readouterr().out


def test_a_week_to_review_still_prints_its_plan_and_exits_three(capsys):
    """Une charge non rattachée demande un arbitrage sans rendre le plan illisible."""
    cli = _week_plan_cli()
    store = _FakeStore(_w32_plan(), needs_arbitration=True)

    code = cli._database_command(store, "show", "2026-W32", False)

    sortie = capsys.readouterr()
    assert code == cli.EXIT_STALE
    assert code != cli.EXIT_INVALID
    assert "w32-lun-ef" in sortie.out, "le programme doit rester lisible"
    assert "arbitrage obligatoire" in sortie.err
    assert "activité non rattachée" in sortie.err
    assert "reste lisible" in sortie.err


def test_a_due_gate_still_takes_precedence_over_staleness(capsys):
    """Un contrôle daté se pose à Sam, une péremption se répare tout seul : quand
    les deux tiennent, c'est la question qui passe devant."""
    cli = _week_plan_cli()
    plan = _w32_plan()
    plan["revisions"][0]["sessions"] = [session("w32-mer-ef", "2026-08-05")]
    plan["revisions"][0]["gates"] = [
        precondition(due="2026-08-05", holds=["w32-mer-ef"])]
    store = _FakeStore(plan, needs_arbitration=True)

    assert cli._database_command(store, "show", "2026-W32", False) == cli.EXIT_AWAITING_GATE
    assert "w32-mer-ef" in capsys.readouterr().out


# ------------------------------------------- contrôles datés : avant, ou après ?
#
# ⭐ 2026-08-18. Le programme portait « après le bloc de pliométrie de la séance A, la
# cheville gauche a-t-elle parlé ? », échéance le jour de cette séance A. Une
# OBSERVATION, écrite dans le seul moule qui existait — celui d'une PRÉ-CONDITION. Le
# service a fait exactement ce qu'on lui demandait : il a retenu la prescription en
# attendant la réponse à une question qui exigeait de faire la séance retenue. Sam a
# reçu un message qui nommait son renfo, posait la question, et ne livrait pas la carte.


def observation(**overrides):
    return gate(when="apres", **overrides)


def precondition(holds=("w32-ven-ef",), **overrides):
    return gate(when="avant", holds=list(holds), **overrides)


def week_with_a_later_session(gates):
    """Une semaine dont la séance retenue tombe le jour de l'échéance, pas avant."""
    current = plan([session("w32-lun-ef", "2026-08-03", status="done"),
                    session("w32-ven-ef", "2026-08-08")])
    current["revisions"][0]["gates"] = gates
    return current


def test_an_observation_gate_holds_nothing_back():
    """LE test du 18/08. Sa réponse n'existe qu'après la séance : elle ne peut pas
    être la condition de cette séance, donc elle ne retient rien."""
    current = plan([session(status="done")])
    current["revisions"][0]["gates"] = [observation()]
    today = dt.date(2026, 8, 8)
    weekly_plan.validate(current)
    assert len(weekly_plan.due_gates(current, today=today)) == 1
    assert weekly_plan.blocking_gates(current, today=today) == []
    assert weekly_plan.gate_reasons(current, today=today) == []


def test_a_precondition_still_holds_the_week():
    current = week_with_a_later_session([precondition()])
    today = dt.date(2026, 8, 8)
    weekly_plan.validate(current)
    assert weekly_plan.blocking_gates(current, today=today)
    assert weekly_plan.gate_reasons(current, today=today)


def test_the_current_schema_refuses_a_gate_that_never_chose():
    broken = plan([session(status="done")])
    untyped = gate()
    untyped.pop("when")
    broken["revisions"][0]["gates"] = [untyped]
    with pytest.raises(SchemaError, match="when"):
        weekly_plan.validate(broken)


def test_an_observation_may_not_claim_to_hold_a_session():
    broken = plan([session(status="done")])
    broken["revisions"][0]["gates"] = [observation(holds=["w32-lun-ef"])]
    with pytest.raises(SchemaError, match="une observation ne retient rien"):
        weekly_plan.validate(broken)


def test_a_precondition_must_name_what_it_holds():
    """« Avant » se paie d'un `holds`. Écrire « je retiens la séance A » juste à côté
    d'une question qui demande comment la séance A s'est passée est une contradiction
    qu'on ne peut plus ne pas voir — c'est tout ce qu'un schéma peut faire, et c'est
    exactement ce qui manquait le 18/08."""
    broken = plan([session(status="done")])
    broken["revisions"][0]["gates"] = [gate(when="avant")]
    with pytest.raises(SchemaError, match="sans `holds`"):
        weekly_plan.validate(broken)


def test_a_gate_cannot_hold_a_session_that_does_not_exist():
    broken = week_with_a_later_session([precondition(holds=["w32-jeu-fantome"])])
    with pytest.raises(SchemaError, match="n'est pas une séance de cette révision"):
        weekly_plan.validate(broken)


def test_a_gate_cannot_hold_a_session_already_past_when_it_is_asked():
    """Retenir une séance du lundi avec une question du vendredi ne retient rien :
    elle a eu lieu. C'est le signe qu'on voulait une observation."""
    broken = plan([session("w32-lun-ef", "2026-08-03"),
                   session("w32-ven-ef", "2026-08-07")])
    broken["revisions"][0]["gates"] = [precondition(due="2026-08-08",
                                                    holds=["w32-lun-ef"])]
    with pytest.raises(SchemaError, match="est une observation"):
        weekly_plan.validate(broken)


# ------------------------------ la frontière que toute révision neuve doit tenir

def test_a_new_revision_does_not_persist_a_schema_version(monkeypatch, capsys):
    from conftest import load_script

    week_plan = load_script("week-plan")
    written = {}

    class FakeStore:
        def load_plan(self, week):
            return None

        def append_plan_revision(self, week, revision, actor=""):
            written["revision"] = revision
            return {"week": week, "revisions": [revision]}

    fresh = written_today(number=1, sessions=[session()])
    fresh["gates"] = [gate(when="apres")]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(fresh)))
    monkeypatch.delenv("REDLINE_TURN_ID", raising=False)

    frozen_clock(monkeypatch, week_plan)
    week_plan._database_command(FakeStore(), "revise", "2026-W32", optional=False)
    capsys.readouterr()
    assert "text_contract" not in written["revision"]


def test_a_new_revision_may_no_longer_carry_an_untyped_gate(monkeypatch):
    """Bout en bout : le contrat que la CLI estampille rend `when` obligatoire, donc la
    révision du 18/08 telle qu'elle a été écrite serait aujourd'hui refusée."""
    from conftest import load_script

    week_plan = load_script("week-plan")

    class FakeStore:
        def load_plan(self, week):
            return None

        def append_plan_revision(self, week, revision, actor=""):
            raise AssertionError("une révision invalide ne doit jamais être écrite")

    fresh = written_today(number=1, sessions=[session()])
    untyped = gate()
    untyped.pop("when")
    fresh["gates"] = [untyped]          # exactement la forme du 2026-08-18
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(fresh)))
    monkeypatch.delenv("REDLINE_TURN_ID", raising=False)

    # `week_plan.SchemaError` et non celui importé en tête : chargé comme un fichier, le
    # script résout `model.*` là où la suite résout `scripts.model.*`. Deux identités
    # pour la même classe, et c'est le script qui décide de celle qu'il lève.
    with pytest.raises(week_plan.SchemaError, match="when"):
        frozen_clock(monkeypatch, week_plan)
        week_plan._database_command(FakeStore(), "revise", "2026-W32", optional=False)


@pytest.mark.parametrize("command", ["preview", "revise"])
def test_plan_cli_distinguishes_preview_from_pending_application(command, monkeypatch, capsys):
    from unittest.mock import MagicMock

    from conftest import load_script

    cli = load_script("week-plan")
    store = MagicMock()
    store.load_plan.return_value = None
    fresh = written_today(number=1, sessions=[session()])
    fresh["gates"] = [gate(when="apres")]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(fresh)))
    monkeypatch.setenv("REDLINE_TURN_ID", "01d5be59-401b-4268-a418-e24d56029fbe")
    frozen_clock(monkeypatch, cli)

    assert cli._database_command(store, command, "2026-W32", False) == 0

    output = capsys.readouterr().out
    store.append_plan_revision.assert_not_called()
    if command == "preview":
        store.preview_command.assert_called_once()
        store.stage_command.assert_not_called()
        assert "SIMULATION VALIDÉE" in output
        assert "aucun changement préparé ou enregistré" in output
    else:
        store.stage_command.assert_called_once()
        store.preview_command.assert_not_called()
        assert "CHANGEMENT PRÉPARÉ" in output
        assert "application en fin de tour encore requise" in output


def test_a_week_with_a_plan_and_no_journal_gets_one(tmp_path):
    """W36 et W39 n'avaient pas de journal : la création était une étape du bilan
    confiée au modèle, sautée deux fois sur quatre (audit du 2026-09-27)."""
    from scripts.storage.projector import ensure_journals

    class Store:
        def load_plan(self, week):
            return {"week": week} if week in {"2026-W39", "2026-W40"} else None

    (tmp_path / "athlete" / "journal").mkdir(parents=True)
    existing = tmp_path / "athlete" / "journal" / "2026-w40.md"
    existing.write_text("déjà écrit par le bilan", encoding="utf-8")
    created = ensure_journals(Store(), tmp_path, today=dt.date(2026, 9, 27))
    assert created == [tmp_path / "athlete" / "journal" / "2026-w39.md"]
    text = created[0].read_text(encoding="utf-8")
    assert text.startswith("---\ntype: bilan\nsemaine: 2026-W39\nmaj: 2026-09-27\n---")
    assert "# Bilan 2026-W39 (21-27 septembre)" in text
    assert "<!-- WEEK_PLAN_START -->\n<!-- WEEK_PLAN_END -->" in text
    assert existing.read_text(encoding="utf-8") == "déjà écrit par le bilan"
    # Une semaine qui chevauche deux mois nomme les deux.
    assert "(28 septembre - 4 octobre)" in weekly_plan.journal_skeleton(
        "2026-W40", dt.date(2026, 9, 27))


def test_a_running_week_states_its_volume_in_a_number():
    """« Environ 41 km » en prose ne se lit pas : le carnet a besoin du chiffre."""
    assert weekly_plan.typed_target_errors(written_today()) == []
    fresh = written_today()
    del fresh["volume_target_km"]
    assert "volume_target_km" in weekly_plan.typed_target_errors(fresh)[0]
    strength_only = written_today(sessions=[session("w32-mar-renfo", workout="strength", routine="a")])
    del strength_only["volume_target_km"]
    assert weekly_plan.typed_target_errors(strength_only) == []
    zoned = written_today(sessions=[session(hr_zone="Z1-Z2, 130-157 bpm")])
    assert "hr_cap_bpm" in weekly_plan.typed_target_errors(zoned)[0]
    capped = written_today(sessions=[session(hr_zone="Z1-Z2, 130-157 bpm", hr_cap_bpm=157)])
    assert weekly_plan.typed_target_errors(capped) == []
    # L'historique d'avant le 2026-10-07 reste lisible sans eux.
    weekly_plan.validate({"week": "2026-W32", "revisions": [revision()]})


def test_the_cli_refuses_a_running_week_without_volume(monkeypatch, capsys):
    from conftest import load_script

    week_plan = load_script("week-plan")

    class FakeStore:
        def load_plan(self, week):
            return None

    fresh = written_today()
    del fresh["volume_target_km"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(fresh)))
    monkeypatch.delenv("REDLINE_TURN_ID", raising=False)
    frozen_clock(monkeypatch, week_plan)
    with pytest.raises(SchemaError, match="volume_target_km"):
        week_plan._database_command(FakeStore(), "revise", "2026-W32", optional=False)


def test_the_volume_is_read_back_and_projected():
    current = written_today(volume_target_km=41.0)
    assert "Volume couru visé : ~41 km" in weekly_plan.revision_readback(
        "2026-W32", None, current)
    assert "Volume couru visé : ~41 km" in weekly_plan.render_journal(
        {"week": "2026-W32", "revisions": [current]})


def test_a_new_gate_never_asks_about_walking():
    """Décision de Sam du 2026-10-11 : la marche ne se demande jamais, il signale
    lui-même une gêne. Le refus porte sur la révision neuve seulement."""
    asking = revision(gates=[gate(question="Trace au réveil et marche inchangée ?")])
    assert any("question sur la marche" in problem
               for problem in weekly_plan.written_text_errors(asking))
    assert not any("marche" in problem
                   for problem in weekly_plan.written_text_errors(revision(gates=[gate()])))


def test_a_new_precondition_no_longer_holds_a_session():
    """Décision du 2026-10-11 : 19 contrôles bloquants, aucun échec sur son critère ;
    23 changements de séance sur 25 venaient de Sam. La condition va dans la séance."""
    holding = revision(gates=[gate(when="avant", holds=["w32-sam-sl"])])
    assert any("ne retient plus de séance" in problem
               for problem in weekly_plan.written_text_errors(holding))
    assert not any("ne retient plus" in problem
                   for problem in weekly_plan.written_text_errors(revision(gates=[gate()])))


def test_a_strength_session_names_its_routine_and_only_it_does():
    strength = session("w32-jeu-a", "2026-08-06", "strength", demanding=False)
    strength.pop("routine")
    with pytest.raises(SchemaError, match="routine manque"):
        weekly_plan.validate(plan([strength]))
    easy = session(routine="a")
    with pytest.raises(SchemaError, match="routine ne vaut que pour une séance strength"):
        weekly_plan.validate(plan([easy]))


def test_a_routine_the_vault_does_not_declare_is_refused():
    strength = session("w32-jeu-z", "2026-08-06", "strength", routine="z", demanding=False)
    with pytest.raises(SchemaError, match="séance « z » inconnue"):
        weekly_plan.validate(plan([strength]), today=dt.date(2026, 8, 3))


def test_the_kind_is_the_family_of_the_workout():
    with pytest.raises(SchemaError, match="kind doit valoir run pour easy"):
        weekly_plan.validate(plan([session(kind="bike")]))
