"""Behavioral contracts carried by the injected prompts."""

import datetime

from conftest import AGENT_CLI_DIR

from redline.service import prompts
from redline.service.prompts import (
    DEPTH_NOTES,
    EMOTION_NOTE,
    PROACTIVE_TRIGGERS,
    STYLE_PROMPT,
    TRIAGE_PROMPT,
    TRIAGE_RULES,
)


def _flat(text: str) -> str:
    """The meaning of an instruction does not depend on where it wraps."""
    return " ".join(text.split())


def test_n2_distinguishes_a_missing_fact_from_an_exploration():
    note = DEPTH_NOTES["N2"]
    assert "seulement si la décision actuelle dépend d'un fait absent" in note
    assert "preview" in note
    assert "UNE QUESTION" in note
    assert "révision typée validée" in note


def test_n1_reads_the_postgres_projection_not_the_deleted_ledger():
    note = DEPTH_NOTES["N1"]
    assert "fact-read observations" in note
    assert "athlete/ledger.md" in note and "n'existe plus" in note


def test_triage_covers_depth_emotion_waiting_and_plan_change():
    assert all(level in TRIAGE_PROMPT for level in ("N0", "N1", "N2", "N3"))
    assert "découragement" in TRIAGE_PROMPT
    assert "CONCRET" in TRIAGE_PROMPT
    assert "aucune durée" in TRIAGE_PROMPT
    assert "P (champ `plan_change`) si le message peut modifier" in TRIAGE_PROMPT


def test_triage_separates_a_plan_that_moves_from_a_plan_that_is_asked_for():
    """The two flags answer two different questions; conflating them makes the coach
    repeat the plan on every message."""
    assert "D (champ `plan_asked`) si ⟨athlète⟩ DEMANDE son programme" in TRIAGE_PROMPT
    assert "jamais D, même quand c'est P" in TRIAGE_PROMPT


def test_the_rules_name_the_json_fields():
    """Both engines answer under `TRIAGE_SCHEMA`: the rules must name its fields,
    notably for the P/D pair, whose confusion makes the coach repeat the plan on every
    message."""
    for field in ("emotional", "plan_change", "plan_asked", "holding"):
        assert f"`{field}`" in TRIAGE_RULES, (
            f"le champ {field} du schéma Codex n'est nommé nulle part dans la doctrine "
            "partagée : le moteur structuré doit deviner la correspondance")


def test_a_familiar_question_about_a_run_never_lands_on_the_social_rung():
    """A casual question about a run ("did you see my run this morning?") classified N0
    would mean "the hot-cache is enough" and an answer without data. Both engines share
    these rules."""
    assert "PRENDS LE PLUS ÉLEVÉ" in TRIAGE_RULES
    assert "N1 AU MINIMUM" in TRIAGE_RULES
    assert "est un débrief, jamais un message social" in TRIAGE_RULES


def test_the_triage_prompt_describes_no_text_protocol():
    """One output, the JSON schema: a one-line format shown to the model would describe
    an output its schema forbids."""
    assert " | " not in TRIAGE_PROMPT
    assert "selon le schéma JSON fourni" in TRIAGE_PROMPT


def test_the_coach_names_its_ai_identity_when_asked_and_never_signs():
    """The name is more exposed when the production engine is not the one that defines
    it. The SIGNATURE is a different matter: telling the coach to sign under that name
    appends a signature to nearly every text message. The name is worn, not repeated."""
    style = " ".join(STYLE_PROMPT.split())
    assert "Redline" in style
    assert "dis clairement que tu es un coach IA" in style
    assert "sans prétendre être humain ou soignant" in style
    assert "TU NE SIGNES JAMAIS TES MESSAGES" in style
    assert "c'est sous ce nom que tu signes" not in style


def test_overshooting_a_target_is_scolded_not_nuanced():
    """An explicit mandate the style must keep: overshooting a target is a reprimand."""
    assert "SÉANCE RATÉE" in STYLE_PROMPT
    assert "réprimande" in STYLE_PROMPT
    debrief = " ".join(PROACTIVE_TRIGGERS["strava_debrief"].split())
    assert "réserve dépensée ou all-in final = séance ratée" in debrief
    assert "réprimande franche" in debrief


def test_a_slightly_faster_last_rep_is_not_scolded():
    """Comparing the last repetition with the first is not a criterion. The coach does
    not scold a last rep a few s/km faster when reserve remained; only a finish that
    spends the reserve fails the session."""
    debrief = " ".join(PROACTIVE_TRIGGERS["strava_debrief"].split())
    assert "dernier intervalle plus rapide que le premier" not in debrief
    assert "Une dernière un peu plus rapide n'est PAS une faute" in debrief
    assert "une allure ne se compare qu'au sein de la séance" in debrief


def test_the_reprimand_stays_inside_the_perimeter_the_vault_gave_it():
    """The mandate is restricted to quality sessions (`coaching/current-plan.md`):
    extended to easy runs it would fire on every hot day, and a guard that shouts at
    false positives ends up ignored. The prompt applies the vault, it does not rule
    against it."""
    style = " ".join(STYLE_PROMPT.split())
    assert "SÉANCE DE QUALITÉ" in style
    assert "n'est PAS une séance ratée" in style
    assert "mandat de réprimande limité" in style
    debrief = " ".join(PROACTIVE_TRIGGERS["strava_debrief"].split())
    assert "Une EF un peu rapide n'entre pas dans ce mandat" in debrief


def test_emotional_turn_follows_the_request_without_a_mandatory_preamble():
    """The emotion note must not impose an emotion, validation, advice sequence, even
    when the request is "I'm disappointed, just give me the total"."""
    assert "prends l'émotion exprimée en compte" in EMOTION_NOTE
    assert "la reformuler n'est pas obligatoire" in EMOTION_NOTE
    assert "après cet accueil" not in EMOTION_NOTE
    assert "Sa dernière demande fixe la posture" in EMOTION_NOTE
    assert "une aide précise peut recevoir une réponse directe" in EMOTION_NOTE
    assert "sans conseil ni question" in EMOTION_NOTE
    assert "au plus une question" in EMOTION_NOTE
    assert "vérification des données ni les garde-fous" in EMOTION_NOTE
    assert "mental-bank.md" in EMOTION_NOTE
    assert "jamais servir à contester son ressenti" in EMOTION_NOTE


def test_emotion_does_not_hide_a_correction_or_reopen_a_refused_subject():
    assert "reconnais l'erreur" in EMOTION_NOTE
    assert "refus de creuser" in EMOTION_NOTE
    assert "laisse le sujet fermé" in EMOTION_NOTE
    assert "E, P et D se cumulent" in TRIAGE_RULES
    assert "une correction reste N3 même avec E" in TRIAGE_RULES


def test_social_closure_does_not_generate_another_question_or_commitment():
    assert "ni question, ni reprise" in DEPTH_NOTES["N0"]
    assert "Ne transforme pas un acquiescement en engagement nouveau" in DEPTH_NOTES["N0"]


def test_debrief_reads_the_activity_and_varies_its_question():
    debrief = PROACTIVE_TRIGGERS["strava_debrief"]
    assert "TITRE ET SA DESCRIPTION" in debrief
    assert "Batterie" in debrief
    assert "fact-read proactive" in debrief
    assert "non servi" in debrief
    # The instruction separates what is READ from what is WRITTEN: paraphrasing a
    # "do not open with" rule meant for the message made the coach announce its method.
    assert "AVANT D'ÉCRIRE" in debrief and "DANS LE MESSAGE" in debrief
    # The angle also needs a PLACE to go, otherwise the separation above does not hold:
    # a reasoning given no field ends up in the prose, and hardening the prompt alone
    # closes the only exit it had.
    assert "fact-write angle" in debrief
    assert "ledger-add" in debrief
    assert "DÉJÀ capturé" in debrief
    assert "D- et WBGT compris" in debrief
    assert "NE RÉVISE PAS le plan" in debrief


def test_deferred_debrief_is_about_the_morning_after():
    deferred = PROACTIVE_TRIGGERS["strava_debrief_late"]
    assert "déjà capturés par le service" in deferred
    assert "AU RÉVEIL" in deferred
    assert "AU RÉVEIL DIRECTEMENT" in deferred
    assert "ne se reporte jamais au soir" in deferred
    assert "`wake-trace`" in deferred
    assert "capacité de nuit" in deferred


def test_morning_collects_the_previous_day_wake_reference_immediately():
    morning = PROACTIVE_TRIGGERS["morning"]
    assert "Au réveil même" in morning
    assert "aucune, très légère, légère ou nette" in morning
    assert "Ne demande jamais un chiffre /10 pour le" in morning
    assert "réveil et ne reporte jamais" in morning
    assert "ne reporte jamais cette question au soir ou au lendemain" in morning
    assert "`--pain` et son mot" in morning


def test_morning_checks_plan_threads_and_upcoming_dates():
    morning = PROACTIVE_TRIGGERS["morning"]
    assert "week-plan show/check" in morning
    assert "fact-read threads" in morning
    assert "échéances" in morning and "PostgreSQL" in morning
    assert "PRÉVENANCE" in morning
    assert "fact-read proactive" in morning
    assert "chart-strength" not in morning
    assert "coaching/strength.md" in morning
    assert "le service génère et joint la carte" in morning


def test_weekly_closes_the_loop_and_prepares_next_week():
    weekly = " ".join(PROACTIVE_TRIGGERS["weekly"].split())
    assert "adherence.md" in weekly
    assert "changement de levier" in weekly
    assert "mental-bank.md" in weekly
    # The review keeps its operations, no longer its stock phrases.
    assert "tu t'engages sur quoi" not in weekly
    assert "minimisé" not in weekly
    assert "ce qui change la semaine" in weekly
    assert "Une question seulement si un choix utile reste ouvert" in weekly
    assert "Une condition du jour va dans la `note` de sa séance" in _flat(weekly)
    # The calibration game is stopped: no trend to report.
    assert "session-cycle report" not in weekly


def test_a_known_session_time_has_a_carrier_without_becoming_a_questionnaire():
    for level in ("N1", "N2"):
        assert prompts.SESSION_TIME_POINTER in DEPTH_NOTES[level]
    assert "sinon ne l'invente ni la demande" in prompts.SESSION_TIME_POINTER


def test_session_cycle_survives_a_forgotten_departure_message():
    for level in ("N1", "N2"):
        assert prompts.SESSION_CYCLE_POINTER in DEPTH_NOTES[level]
    assert "start` est facultatif" in prompts.SESSION_CYCLE_POINTER
    assert "Strava reconstruit l'oubli" in prompts.SESSION_CYCLE_POINTER
    assert "start_local" in PROACTIVE_TRIGGERS["strava_debrief"]
    assert "session_followup" not in PROACTIVE_TRIGGERS
    assert "followup" not in prompts.SESSION_CYCLE_POINTER
    weekly = PROACTIVE_TRIGGERS["weekly"]
    assert "start_time: HH:MM" in weekly
    assert "sinon laisse le champ absent" in weekly


def test_weekly_reads_the_strava_only_checks_without_treating_silence_as_data():
    """The Strava-only checks have no register column: they are read from Strava or
    nowhere. Silence in the notes is not data: the battery says it proves nothing."""
    weekly = " ".join(PROACTIVE_TRIGGERS["weekly"].split())
    assert "§ D" in weekly
    assert "son silence ne rassure ni n'alarme" in weekly
    assert "son silence est donc une donnée" not in weekly


def test_weekly_closes_the_predictions_that_came_due():
    """A prediction that no review closes is never falsifiable: every due one gets a
    verdict at the review."""
    weekly = " ".join(PROACTIVE_TRIGGERS["weekly"].split())
    assert "fact-read predictions --due" in weekly
    assert "fact-write prediction --key <clé> --outcome true|false|void" in weekly
    assert "verdict sur CHAQUE prédiction échue" in weekly
    # Body predictions for long runs and increases attach to the next-morning wake
    # reading and are settled from the notebook.
    assert "Chaque SL et chaque hausse de la porte reçoit sa prédiction `corps`" in weekly
    assert "--resolves wake-trace" in weekly and "probabilité explicite" in weekly


def test_monthly_includes_mental_work_and_coach_feedback():
    monthly = PROACTIVE_TRIGGERS["monthly"]
    assert "TRAVAIL MENTAL" in monthly
    assert "sans objet concret, n'en force aucun" in " ".join(monthly.split())
    assert "mental-bank.md" in monthly
    assert "Le retour sur le coach se propose" in " ".join(monthly.split())
    assert "ni question ni conclusion fixe" in " ".join(monthly.split())
    assert "de travers" not in monthly


def test_style_keeps_channel_and_decision_guardrails():
    assert "droit de contredire" in STYLE_PROMPT
    # An objection makes the coach re-examine its premises: it can reveal a wrong
    # inference without bringing any new fact.
    assert "S'IL INSISTE" not in _flat(STYLE_PROMPT)
    assert "UNE OBJECTION fait réexaminer tes prémisses" in _flat(STYLE_PROMPT)
    assert "Un désaccord n'est pas un feu vert" in _flat(STYLE_PROMPT)
    assert '"intermittent" n\'est pas "persistant"' in _flat(STYLE_PROMPT)
    assert "si le critère est ambigu, demande" in STYLE_PROMPT
    assert "porte de sortie chiffrée" in STYLE_PROMPT
    assert "UN CHIFFRE NE SE CITE PAS" in STYLE_PROMPT
    assert f"{prompts.AGENT_CLI} ledger --week" in STYLE_PROMPT
    assert "Vol. cible" in STYLE_PROMPT
    assert "n'annonce pas leur arrivée" in STYLE_PROMPT
    assert f"{prompts.AGENT_CLI} fact-read corrections" in STYLE_PROMPT
    assert "Même incident = même clé" in STYLE_PROMPT


def test_the_rating_scales_ride_with_the_levels_where_numbers_are_given():
    """The rating scales' carrier cannot be the vault card.

    `coaching/protocols.md` opens only at N2, but the athlete reports pain at N1: a
    rule that lives only in the card would go dormant. It is not in `STYLE_PROMPT`
    either: baked at session level it is invisible to an already open conversation, and
    its budget is full. This test makes removing the pointer a choice, not a side effect.
    """
    pointer = prompts.pain_scale_pointer()
    for level in ("N1", "N2"):
        assert pointer in DEPTH_NOTES[level]
    # The cap is the rulebook's, not a number written in the text.
    assert "plafond douleur d'effort = 2," in pointer
    assert "pas de note /10" in pointer
    assert "séance entière" in pointer


def test_the_morning_debrief_never_asks_for_a_mark_out_of_ten():
    """The morning-after wake reading is "completely different and hard to rate". The
    trigger carries the wording, not only the prohibition."""
    differe = PROACTIVE_TRIGGERS["strava_debrief_late"]
    assert "NE DEMANDE PAS UNE NOTE SUR 10" in differe
    assert "(aucune, très légère, légère, nette)" in _flat(differe)


def test_the_two_examples_differ_only_by_naming_the_machinery():
    """A counter-example teaches only what SEPARATES it from its counterpart.

    The pair must have comparable length: otherwise the contrast teaches "be brief"
    instead of the rule, and a bare question asked in a vacuum is worse coaching than
    the faulty message. The good example must be grounded in `coaching/protocols.md`,
    which declares the battery, not invented.

    This test keeps the one thing that makes the pair work: at comparable length, the
    only visible difference is that one of the two names its machinery.
    """
    lignes = {debut: ligne.split(":", 1)[1].strip()
              for ligne in STYLE_PROMPT.splitlines()
              for debut in ("NON", "OUI") if ligne.strip().startswith(debut + " :")}
    assert set(lignes) == {"NON", "OUI"}, "la paire d'exemples a disparu du style"
    non, oui = len(lignes["NON"].split()), len(lignes["OUI"].split())
    assert abs(non - oui) <= 3, (
        f"NON {non} mots, OUI {oui} : un écart de longueur fait porter au contraste "
        "une leçon de brièveté au lieu de la règle qu'il illustre")
    assert "batterie" in lignes["NON"].lower()
    assert "batterie" not in lignes["OUI"].lower()


def test_runtime_prompts_stay_within_explicit_context_budgets():
    """These strings are paid in addition to CLAUDE.md; the vault-file budget cannot see them.

    Triage carries two budgets: `TRIAGE_RULES` is the doctrine, so the ceiling is tight;
    the prompt that wraps it only adds the call to the JSON schema.

    The style ceiling pays for `TYPOGRAPHY_RULE` and a counter-example: words paid once
    per SESSION are worth more than a one-line instruction the coach breaks at every
    message, and a model imitates its own turns before obeying an instruction. The budget
    measures a cost, not a virtue.

    The proactive-trigger ceiling pays for a COMMAND, `fact-write angle`, which gives
    the angle a place to go: a reasoning given no field ends up in the prose.
    """
    # The explicit launcher adds a word to the former bare CLI mentions. That cost buys
    # exact alignment with `dontAsk`.
    # The extra headroom is the explicit exit_code check, not a new editorial rule.
    # This ceiling bounds the static CONTEXT in CI. It never judges an answer and
    # triggers no regeneration, so it cannot make the coach loop.
    assert len(STYLE_PROMPT.split()) <= 1040
    assert len(TRIAGE_RULES.split()) <= 300
    assert len(TRIAGE_PROMPT.split()) <= 350
    assert max(len(text.split()) for text in DEPTH_NOTES.values()) <= 180
    assert max(len(text.split()) for text in PROACTIVE_TRIGGERS.values()) <= 520


def test_weekly_cannot_use_the_generic_silence_escape_hatch():
    weekly = prompts.build_proactive_prompt("weekly", "")
    repair = prompts.build_proactive_prompt("weekly_repair", "2026-W40")
    morning = prompts.build_proactive_prompt("morning", "")

    assert "ne reste jamais silencieux" in weekly
    assert "ne reste jamais silencieux" in repair
    assert "Si rien n'est utile, reste silencieux" not in weekly
    assert "Si rien n'est utile, reste silencieux" in morning


def test_silence_is_a_typed_decision_never_a_sentinel_word():
    """No code searches for the word "SILENCE" in the prose of an initiative."""
    for kind in PROACTIVE_TRIGGERS:
        prompt = prompts.build_proactive_prompt(kind, "42")
        assert "SILENCE" not in prompt, kind
        assert "`send` faux et `text` vide" in prompt, kind


def test_closed_initiatives_have_no_agent_prompt_left():
    """A kind rendered without a model keeps no contradictory prompt."""
    from redline.service.proactive_messages import CLOSED_KINDS

    assert not CLOSED_KINDS & set(PROACTIVE_TRIGGERS)


def test_style_names_the_real_file_reading_path():
    assert "fichiers via `sed`/`rg`" in _flat(STYLE_PROMPT)
    assert "jamais par script inventé" in STYLE_PROMPT


def test_style_never_treats_a_failed_cli_as_a_persisted_fact():
    assert "Code non nul" in STYLE_PROMPT
    assert "ne prétends jamais avoir réussi" in _flat(STYLE_PROMPT)


def test_style_names_the_shell_forms_the_tool_refuses():
    """The coach must not append `; echo "exit=$?"` to read the exit code: the tool
    refuses such shell forms."""
    flat = _flat(STYLE_PROMPT)
    assert "Une CLI par appel, sans `$`, boucle ni heredoc" in flat
    assert "JSON via `--input` (FICHIER DU TOUR)" in flat
    assert "/tmp" not in flat


def test_every_turn_names_its_own_input_file():
    """A `< /tmp/x.json` redirect is refused by the tool. The turn gets its own path in
    the vault, which the CLI reads through `--input`."""
    note = prompts.turn_input_note("11111111-2222-3333-4444-555555555555")
    path = ".agent-tmp/11111111-2222-3333-4444-555555555555.json"
    assert f"Write dans `{path}`" in note
    assert f"`--input {path}`" in note
    assert " < " not in note


def test_missing_cells_publish_the_real_one_metric_cli_contract():
    note = prompts.missing_cells_note(
        [{"date": "2026-08-30", "type": "long",
          "missing": ["rpe", "pain", "enjoyment"]}], [])

    assert (f"{prompts.AGENT_CLI} ledger-complete --key <clé> "
            "--metric rpe --value 5") in note
    assert "N'utilise jamais `--rpe`, `--pain`" in note


def test_style_names_relational_memory_and_its_closed_vocabularies():
    assert f"{prompts.AGENT_CLI} fact-write thread" in STYLE_PROMPT
    assert "`open` ou `closed`" in _flat(STYLE_PROMPT)
    assert "ces deux mots exactement" in STYLE_PROMPT
    assert "coaching/adherence.md" in STYLE_PROMPT
    assert "coaching/corrections.md" in STYLE_PROMPT
    assert "réservé à TES erreurs" in STYLE_PROMPT


def test_conversation_knows_the_strength_card_exists():
    for depth in ("N1", "N2"):
        assert "chart-strength" not in DEPTH_NOTES[depth]
        assert "générée et jointe déterministement" in DEPTH_NOTES[depth]
    assert "Vérifie avant de dire" in STYLE_PROMPT


def test_strava_is_declared_on_every_path_the_coach_can_take():
    """A capability declared on a single path does not exist on the others. Strava is
    therefore named where every turn passes: the style, the debrief reading walk, and
    the two triggers that start from an activity."""
    assert f"{prompts.AGENT_CLI} strava" in STYLE_PROMPT
    assert "TU AS ACCÈS À STRAVA" in STYLE_PROMPT
    assert f"{prompts.AGENT_CLI} strava" in DEPTH_NOTES["N1"]
    for kind in ("strava_debrief", "strava_debrief_late"):
        assert "INSTANTANÉ STRAVA" in PROACTIVE_TRIGGERS[kind]
        assert f"{prompts.AGENT_CLI} strava" not in PROACTIVE_TRIGGERS[kind]


def test_a_debrief_uses_the_captured_snapshot_instead_of_refetching_strava():
    """Deterministic capture precedes the model: a second call can only reintroduce an
    outage after the data is already acquired."""
    debrief = PROACTIVE_TRIGGERS["strava_debrief"]
    assert "INSTANTANÉ STRAVA" in debrief
    assert "Ne refais pas l'appel fournisseur" in debrief


def test_every_command_the_prompts_hand_out_is_runnable_as_written():
    """The prompt may only cite the launcher that Claude actually allows."""
    for text in (STYLE_PROMPT, *DEPTH_NOTES.values(), *PROACTIVE_TRIGGERS.values()):
        flat = " ".join(text.split())
        assert "python3 scripts/" not in flat
        assert "`scripts/" not in flat.replace("`scripts/` direct", "")
        for command in ("strava", "descent", "weather"):
            if f"`{prompts.AGENT_CLI} {command}" in flat:
                assert f"{prompts.AGENT_CLI} {command}" in flat


def test_logging_a_session_never_rewrites_the_plan_execution_state():
    """What was done belongs to the cycle; only a future prescription justifies rN+1."""
    style = " ".join(STYLE_PROMPT.split())
    assert "jamais pour marquer `done`" in style
    assert "le réalisé appartient au cycle" in style
    debrief = " ".join(PROACTIVE_TRIGGERS["strava_debrief"].split())
    assert "NE RÉVISE PAS le plan" in debrief
    assert "prescription future change" in debrief


def test_a_stale_plan_asks_for_a_revision_before_the_turn_not_after():
    """A staleness check that ran AFTER the answer could only destroy finished work.
    The same information, served at the head of the turn, becomes actionable."""
    note = prompts.stale_plan_note("2026-W33")
    assert "invisible pour ⟨athlète⟩" in note
    assert "2026-W33" in note
    assert "tu réponds normalement" in note
    assert "AUCUNE prescription" in note
    assert "week-plan revise --week 2026-W33" in note
    assert "Ne parle jamais de ce contrôle" in note


def test_the_cadence_is_never_doubled_twice():
    """The x2 is applied in redline strava, which tells a stride from a pedal turn.
    The prompt must say NOT to redo it, otherwise 168 becomes 336."""
    assert "ne la multiplie pas une deuxième fois" in PROACTIVE_TRIGGERS["strava_debrief"]


def test_the_daily_question_stays_subjective():
    """The state question stays a coach question, never a readout."""
    morning = PROACTIVE_TRIGGERS["morning"]
    assert "ni score, ni courbe, ni moyenne" in morning
    assert "signal de CHARGE" in morning


def test_every_trigger_survives_the_real_builder():
    for kind in PROACTIVE_TRIGGERS:
        rendered = prompts.build_proactive_prompt(kind, "peu importe")
        assert "{detail}" not in rendered
        assert rendered.strip()


def test_builder_only_replaces_detail_and_preserves_literal_braces(monkeypatch):
    monkeypatch.setitem(PROACTIVE_TRIGGERS, "test", "objet {a: 1}; détail {detail}")
    rendered = prompts.build_proactive_prompt("test", "42")
    assert "{a: 1}" in rendered
    assert "détail 42" in rendered


PENDING = [{"date": "2026-08-04", "type": "strength", "missing": ["rpe"]}]
LAPSED = [{"date": "2026-08-01", "type": "easy", "missing": ["enjoyment"]}]


def test_no_missing_cell_means_no_note():
    assert prompts.missing_cells_note([], []) == ""


def test_wake_cells_do_not_enter_the_generic_missing_cells_note():
    wake = [{"date": "2026-09-14", "type": "wake-trace", "missing": ["pain"]}]

    assert prompts.missing_cells_note(wake, wake) == ""


def test_pending_cells_do_not_force_a_questionnaire_or_block_partial_answers():
    note = prompts.missing_cells_note(
        [{"date": "2026-08-10", "type": "easy",
          "missing": ["rpe", "pain", "enjoyment"]}], []
    )
    assert "une seule information" in note
    assert "Ne bloque pas sur les autres cellules" in note
    assert "réponse partielle" in note
    assert "questionnaire" in note
    assert "Ne mentionne jamais cette note" in note


def test_with_the_notebook_pending_cells_are_left_to_it_except_pain():
    note = prompts.missing_cells_note(
        [{"date": "2026-10-08", "type": "easy", "missing": ["rpe", "enjoyment"]}], [],
        notebook=True)
    assert "Le carnet les recueille : ne les demande pas" in note
    assert "douleur si le message montre une gêne" in note
    assert "une seule information" not in note
    # A spontaneously given value is always written through the same contract.
    assert "--metric rpe" in note


def test_walking_is_never_asked():
    """Walking is never asked, even when an old dated check still names it; the athlete
    reports discomfort unprompted."""
    for level in ("N1", "N2"):
        assert "jamais la marche, il signale une gêne" in DEPTH_NOTES[level]
    note = prompts.gate_note([{
        "id": "genou", "holds": ["threshold"],
        "question": "Trace habituelle et marche inchangée ?",
        "on_fail": "remplacer le seuil",
    }])
    assert "Ne demande jamais la marche, même si le contrôle la nomme" in note

def test_lapsed_cells_are_marked_lost_never_invented():
    note = prompts.missing_cells_note([], LAPSED)
    assert "`?`" in note
    assert "n'invente pas" in note
    assert "ne redemande pas" in note


def test_evening_claim_hides_storage_plumbing():
    import json

    from redline.service.proactive_messages import compose_closed_proactive

    claim = compose_closed_proactive("ledger_incomplete", json.dumps({
        "date": "2026-09-24", "type": "easy", "missing": ["rpe"],
        "markers": ["2026-09-24:easy:rpe"]}))
    assert claim is not None
    assert "registre" not in claim.text and "donnée" not in claim.text
    assert claim.markers == ("2026-09-24:easy:rpe",)


def test_no_injected_text_types_a_glyph_a_phone_keyboard_does_not_carry():
    """No injected text may type a glyph a phone keyboard does not carry.

    The typography rule alone is not enough: level notes, proactive triggers and hidden
    instructions that contain such glyphs contradict it by example, and a model imitates
    what it reads before obeying what it is told. This test holds the other end: the
    prompt cannot type what it forbids.

    Only `TYPOGRAPHY_RULE` escapes, because it must NAME the signs to forbid them; it
    is cut out before the scan.
    """
    injected = {
        "STYLE_PROMPT": STYLE_PROMPT.replace(prompts.TYPOGRAPHY_RULE, ""),
        "TRIAGE_PROMPT": TRIAGE_PROMPT,
        "EMOTION_NOTE": EMOTION_NOTE,
        "STALE_VAULT_NOTE": prompts.STALE_VAULT_NOTE,
        "STRENGTH_CARD_POINTER": prompts.STRENGTH_CARD_POINTER,
        "HELP_TEXT": prompts.HELP_TEXT,
        "stale_plan_note": prompts.stale_plan_note("2026-W34"),
        "gate_note": prompts.gate_note(
            [{"due": "2026-08-17", "holds": ["w34-dim-sl"],
              "question": "La blessure a tenu la sortie longue ?",
              "on_fail": "on retire les côtes de jeudi"}]),
        # Both halves are scanned separately: the observation note is a full injected
        # text of its own.
        "gate_note[observation]": prompts.gate_note(
            [], [{"due": "2026-08-18", "question": "La cheville a parlé pendant la "
                  "pliométrie ?", "on_fail": "elle sort de la séance A"}]),
        # Texts moved out of `bot.py`: injected at every turn, so each must be scanned.
        "canonical_plan_note": prompts.canonical_plan_note(""),
        "active_revision_note": prompts.active_revision_note(""),
        "deep_handoff_note": prompts.deep_handoff_note("question puis réponse"),
        "missing_cells_note": prompts.missing_cells_note(
            [{"date": "2026-08-16", "type": "easy", "missing": ["rpe", "pain", "dminus"]},
             {"date": "2026-08-15", "type": "threshold", "missing": ["enjoyment"]}], LAPSED),
        **{f"DEPTH_NOTES[{level}]": note for level, note in DEPTH_NOTES.items()},
        **{f"PROACTIVE_TRIGGERS[{kind}]": prompts.build_proactive_prompt(kind, "424242")
           for kind in PROACTIVE_TRIGGERS},
        **{f"PATIENCE[{index}]": line
           for index, line in enumerate(prompts.PATIENCE_LINES
                                        + prompts.DEEP_PATIENCE_LINES)},
    }
    for name, text in injected.items():
        for glyph in prompts.BANNED_GLYPHS:
            assert glyph not in text, (
                f"« {glyph} » dans {name} : le prompt démontre ce qu'il interdit, "
                f"autour de « {text[max(0, text.find(glyph) - 50):][:110]} »")


def test_the_glyph_rule_reaches_the_data_channel_not_only_the_written_prompts():
    """The glyph rule also reaches the data channel, not only the written prompts.

    The test above scans strings written in the code. But the largest block injected at
    every prescriptive turn is the plan itself (`weekly_plan.as_json()`), made of texts
    the COACH wrote the day before: `reason`, `constraints`, the `question`/`on_fail`
    of dated checks, the `title`/`note` of sessions. `prompts.gate_note()` interpolates
    two of them VERBATIM into the hidden instruction, and a note goes out to the
    athlete's phone.

    The refusal is applied at WRITE time (`week-plan revise`), not in `validate`:
    history is append-only and `revise` validates the whole plan, so a rule applied at
    read time would refuse today's revision because of yesterday's text.
    """
    from redline.model import weekly_plan

    sale = {
        "reason": "EF réalisée — rien à signaler",
        "constraints": ["plafond à 1200 m D−"],
        "gates": [{"id": "g1", "due": "2026-08-23",
                   "question": "la blessure dans les premiers pas — mieux ou pire ?",
                   "on_fail": "la semaine démarre sans sortie longue"}],
        "sessions": [{"id": "s1", "title": "EF", "note": "allure libre · plat"}],
    }
    fautes = weekly_plan.written_text_errors(sale, "r7")
    champs = {"reason", "constraints[0]", "question", "note"}
    for champ in champs:
        assert any(champ in faute for faute in fautes), (
            f"{champ} n'est pas jugé : ce texte est réinjecté au coach à chaque tour")
    # The message opens the correct path, it does not merely close the wrong one: a
    # guard without a correct path breeds its own workaround.
    assert any("À la place :" in faute for faute in fautes)

    propre = {
        "reason": "EF realisee, rien a signaler",
        "constraints": ["plafond a 1200 m de D-"],
        "gates": [{"id": "g1", "due": "2026-08-23",
                   "question": "la blessure dans les premiers pas, mieux ou pire ?",
                   "on_fail": "la semaine demarre sans sortie longue"}],
        "sessions": [{"id": "s1", "title": "EF", "note": "allure libre, plat"}],
    }
    assert weekly_plan.written_text_errors(propre, "r7") == []


def test_both_guards_read_the_same_list_of_glyphs():
    """Two drifting lists mean a rule applied to one channel out of two."""
    from redline.model import typography
    assert prompts.BANNED_GLYPHS is typography.BANNED_GLYPHS


def test_the_write_guard_is_wired_into_the_revise_path_not_merely_available():
    """A guard function nobody calls is an intention, not a guard. It must be visible
    on the write path itself."""
    from conftest import ENGINE_ROOT
    source = (ENGINE_ROOT / "src" / "redline" / "cli" / "week_plan.py").read_text()
    assert "written_text_errors(current" in source, (
        "le garde-fou typographique n'est pas appelé sur la révision neuve")
    avant = source.index("written_text_errors(current")
    apres = source.index("validate(candidate)")
    assert avant < apres, (
        "le texte doit être jugé avant le plan qui le contient : sinon une révision "
        "sale est acceptée dès que le plan, lui, valide")


def test_tentative_logistics_does_not_inherit_a_plan_change_obligation():
    assert 'confirmation promise' in TRIAGE_RULES
    assert 'sans conseil demandé = N0, sans P ni D' in TRIAGE_RULES
    note = prompts.plan_change_note()
    assert 'aucune révision, question ou scénario' in note
    assert 'POUR UN CHANGEMENT CONFIRMÉ' in note
    assert 'AVANT DE RÉPONDRE, reprends son message fait par fait' not in note


def test_a_restriction_names_its_rule_and_fact_wherever_the_plan_is_revised():
    """A restriction without a premise is the first fault on a prescription."""
    assert "`restriction`" in prompts.plan_change_note()
    assert "`request`" in prompts.plan_change_note()
    assert "`restriction`" in DEPTH_NOTES["N2"]
    assert "`restriction`" in PROACTIVE_TRIGGERS["weekly_repair"]


def test_weekly_uses_the_dossier_and_closes_every_due_prediction():
    weekly = " ".join(PROACTIVE_TRIGGERS["weekly"].split())
    assert "DOSSIER DU BILAN" in weekly and "ne le recalcule pas" in weekly
    assert "fact-read predictions --due" in weekly
    assert "--reason" in weekly and "decisions-index" not in weekly
    assert "protocole d'instruction des baisses" in weekly
    assert "action prête à faire" in weekly and "repli daté" in weekly
    assert "Le service crée le journal" in weekly


def test_the_weekly_commitment_is_gone():
    """The weekly commitment lever is removed, not softened: no question, and no field
    in the week's journal."""
    from redline.model import weekly_plan

    weekly = " ".join(PROACTIVE_TRIGGERS["weekly"].split())
    assert "Ne demande à ⟨athlète⟩ aucun engagement pour la semaine" in weekly
    assert "jamais une formule fixe ni un présupposé de défaut" in weekly
    journal = weekly_plan.journal_skeleton("2026-W41", datetime.date(2026, 10, 5))
    assert "Engagement" not in journal and "minimisé" not in journal


def test_the_race_countdown_defers_to_the_plan():
    countdown = " ".join(PROACTIVE_TRIGGERS["race_countdown"].split())
    assert "le plan décide" in countdown
    assert "ne pousse jamais une séance que le plan n'a pas prévue" in countdown


def test_monthly_judges_outcomes_not_only_the_process():
    monthly = " ".join(PROACTIVE_TRIGGERS["monthly"].split())
    assert "Juge le RÉSULTAT" in monthly and "--long-run-target" in monthly


def test_a_session_is_bound_to_the_instruction_files_it_actually_read(tmp_path):
    """The contract carries the instruction files the session actually read, not only
    the constitution's NAME. A rule fixed in CLAUDE.md must not leave alive a thread
    that read the old one; a notes commit must not restart anyone from zero."""
    assert prompts.instruction_digest(tmp_path) == prompts.STYLE_DIGEST
    (tmp_path / "CLAUDE.md").write_text("règle A", encoding="utf-8")
    first = prompts.instruction_digest(tmp_path)
    assert first != prompts.STYLE_DIGEST
    (tmp_path / "ai").mkdir()
    (tmp_path / "ai" / "hot-cache.md").write_text("cache du jour", encoding="utf-8")
    assert prompts.instruction_digest(tmp_path) == first
    (tmp_path / "CLAUDE.md").write_text("règle B", encoding="utf-8")
    second = prompts.instruction_digest(tmp_path)
    assert second != first
    (tmp_path / "AGENTS.md").write_text("aiguillage", encoding="utf-8")
    assert prompts.instruction_digest(tmp_path) != second
    assert set(prompts.instruction_manifest(tmp_path)) == {"CLAUDE.md", "AGENTS.md"}


def test_a_contested_simple_fact_does_not_become_a_literature_review():
    """Triage places every correction at N3. Reading depth dictates neither the sources
    to open nor the length sent."""
    note = " ".join(DEPTH_NOTES["N3"].split())
    assert "relis son propriétaire, pas la littérature" in note
    assert "réponds en une phrase si elle suffit" in note
    assert "La profondeur lue ne dicte pas la longueur envoyée" in note
    assert "N'affirme rien sans source lue dans cette session" in note
    style = _flat(STYLE_PROMPT)
    assert "Vérifie autant que nécessaire" in style
    assert "prends la place nécessaire" not in style


def test_tracking_is_reserved_for_expected_information():
    """Not every question is a debt, and not every inference opens a question."""
    style = _flat(STYLE_PROMPT)
    assert "Déclare toute question" not in style
    assert "Information attendue seulement, jamais une question sociale" in style
    assert "n'ouvre une question que si la réponse change la décision" in style


def test_an_observation_does_not_hijack_a_social_message():
    """At N0 the observation branch must not force prescribing the day and asking the
    evening question, against the N0 note that asks to respect a closure. A blocking
    check stays whole at every level."""
    blocking = [{"id": "b", "holds": ["s1"], "question": "la blessure ?", "on_fail": "rest"}]
    observing = [{"id": "o", "question": "la cheville ?", "on_fail": "on retire"}]
    closing = prompts.gate_note(blocking, observing, closing=True)
    interdiction, observation = closing.split("\n\n")
    assert interdiction == prompts.gate_note(blocking)
    assert "ne change pas de sujet pour la poser ni pour prescrire" in observation
    assert "prescris la journée normalement" not in observation
    assert "la cheville ?" in observation
    assert "prescris la journée normalement" in prompts.gate_note([], observing)


def test_the_debrief_opens_on_a_computed_gap_not_on_the_watch():
    """A debrief must not open on three raw watch figures in a row."""
    debrief = _flat(PROACTIVE_TRIGGERS["strava_debrief"])
    assert "Ta première phrase porte l'écart de `ecarts` qui compte" in debrief
    assert "jamais trois chiffres de la montre à la suite" in debrief
    assert "`ecarts`, s'il est présent" in prompts.strava_snapshot_note({})


def test_a_skipped_session_is_cancelled_in_the_same_turn():
    """A skipped session announced by the athlete is cancelled in the same turn: the
    follow-up is a closed rendering, so the revision must come before it
    (`coaching/corrections.md`)."""
    note = " ".join(prompts.plan_change_note().split())
    assert "QU'IL ANNONCE SAUTER" in note
    assert "`cancelled`, restriction `request`" in note


def test_a_quality_session_is_judged_on_its_written_criteria_only():
    """A prescribed "no HR target" must not be followed by a doubt based on the HR."""
    debrief = _flat(PROACTIVE_TRIGGERS["strava_debrief"])
    assert "SEULS critères écrits dans sa séance" in debrief
    assert "la FC n'est ni doute ni reproche" in debrief
    assert "Calibrations de sa fiche dans `coaching/workouts/`" in debrief


def test_the_workout_card_is_no_longer_the_models_choice():
    assert "chart-workout" not in " ".join(PROACTIVE_TRIGGERS.values())
    assert "chart-workout" not in STYLE_PROMPT
    assert "chart-workout" not in (AGENT_CLI_DIR / "agent-cli-tools.txt").read_text()
