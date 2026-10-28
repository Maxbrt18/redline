"""Behavioral contracts carried by the injected prompts."""

import datetime

from conftest import SERVER_DIR

from redline.service import prompts
from redline.service.prompts import (
    DEPTH_NOTES,
    EMOTION_NOTE,
    PROACTIVE_TRIGGERS,
    STYLE_PROMPT,
    TRIAGE_PROMPT,
    TRIAGE_RULES,
    TRIAGE_STRUCTURED_PROMPT,
)


def _flat(text: str) -> str:
    """Le sens d'une consigne ne dépend pas de l'endroit où elle revient à la ligne."""
    return " ".join(text.split())


def test_n2_distinguishes_a_missing_fact_from_an_exploration():
    note = DEPTH_NOTES["N2"]
    assert "seulement si la décision actuelle dépend d'un fait absent" in note
    assert "preview" in note
    assert "UNE QUESTION" in note
    assert "révision typée validée" in note


def test_n1_reads_the_postgres_projection_not_the_deleted_ledger():
    note = DEPTH_NOTES["N1"]
    assert "fact-read.py observations" in note
    assert "athlete/ledger.md" in note and "n'existe plus" in note


def test_triage_covers_depth_emotion_waiting_and_plan_change():
    assert all(level in TRIAGE_PROMPT for level in ("N0", "N1", "N2", "N3"))
    assert "découragement" in TRIAGE_PROMPT
    assert "CONCRET" in TRIAGE_PROMPT
    assert "aucune durée" in TRIAGE_PROMPT
    assert "P (champ `plan_change`) si le message peut modifier" in TRIAGE_PROMPT


def test_triage_separates_a_plan_that_moves_from_a_plan_that_is_asked_for():
    """Les deux drapeaux répondent à deux questions, et les confondre a fait répéter
    le programme à chaque message (2026-08-12). Le format de sortie porte les six
    champs, sinon le parseur lit une colonne qui n'existe pas."""
    assert TRIAGE_PROMPT.splitlines()[1] == (
        "NIVEAU | E ou - | phrase d'attente | P ou - | D ou - | plan_scope")
    assert "D (champ `plan_asked`) si ⟨athlète⟩ DEMANDE son programme" in TRIAGE_PROMPT
    assert "jamais D, même quand c'est P" in TRIAGE_PROMPT


def test_the_shared_rules_name_the_json_fields_not_only_the_text_tokens():
    """⭐ `TRIAGE_RULES` est payé par les DEUX moteurs, et ne parlait que du protocole
    texte : « E », « P », « D » sont les jetons d'une ligne à cinq colonnes que Codex
    n'a pas le droit de produire. Son schéma attend `emotional`, `plan_change`,
    `plan_asked`, `holding`, et la correspondance n'était écrite nulle part -- pour la
    paire P/D précisément, dont la confusion a fait répéter le programme à chaque
    message le 2026-08-12. La prod tourne `AGENT_PROVIDER=claude`, donc c'était une
    faute dormante : elle se serait réveillée à la bascule, sans rien pour la nommer.
    """
    for field in ("emotional", "plan_change", "plan_asked", "holding"):
        assert f"`{field}`" in TRIAGE_RULES, (
            f"le champ {field} du schéma Codex n'est nommé nulle part dans la doctrine "
            "partagée : le moteur structuré doit deviner la correspondance")


def test_a_familiar_question_about_a_run_never_lands_on_the_social_rung():
    """2026-08-14 : « T'as vu ma sortie de ce matin ? » classé N0, donc « le hot-cache
    suffit », donc une réponse sans données. Les deux moteurs partagent ces règles."""
    assert "PRENDS LE PLUS ÉLEVÉ" in TRIAGE_RULES
    assert "N1 AU MINIMUM" in TRIAGE_RULES
    assert "est un débrief, jamais un message social" in TRIAGE_RULES


def test_worked_examples_belong_to_the_text_protocol_only():
    """Le format à une ligne n'existe que pour le moteur texte : montrer ces exemples à
    Codex lui décrirait une sortie que son schéma JSON interdit."""
    assert "Je suis encore sur ta réponse." in TRIAGE_PROMPT
    assert "Exemples de sortie" not in TRIAGE_STRUCTURED_PROMPT
    assert " | " not in TRIAGE_STRUCTURED_PROMPT


def test_the_coach_names_its_ai_identity_when_asked_and_never_signs():
    """Le nom, perdu dans le ménage du 2026-08-11, est d'autant plus exposé que le
    moteur en production n'est plus Claude. La SIGNATURE, elle, est une autre affaire :
    « c'est sous ce nom que tu signes » a produit un « — Redline » collé à la fin de
    presque chaque texto, jusqu'à « il faut que tu arrêtes de signer tous tes messages »
    (2026-08-16). Le nom se porte, il ne se répète pas."""
    style = " ".join(STYLE_PROMPT.split())
    assert "Redline" in style
    assert "dis clairement que tu es un coach IA" in style
    assert "sans prétendre être humain ou soignant" in style
    assert "TU NE SIGNES JAMAIS TES MESSAGES" in style
    assert "c'est sous ce nom que tu signes" not in style


def test_overshooting_a_target_is_scolded_not_nuanced():
    """Mandat explicite de Sam, que le ménage du 2026-08-11 avait effacé du style."""
    assert "SÉANCE RATÉE" in STYLE_PROMPT
    assert "réprimande" in STYLE_PROMPT
    debrief = " ".join(PROACTIVE_TRIGGERS["strava_debrief"].split())
    assert "réserve dépensée ou all-in final = séance ratée" in debrief
    assert "réprimande franche" in debrief


def test_a_slightly_faster_last_rep_is_not_scolded():
    """Décision du 2026-10-26 : la comparaison de la dernière répétition à la première
    est retirée. Le coach ne gronde plus 5 s/km de mieux sur la dernière quand il en
    restait deux ; seule une fin qui dépense la réserve rate la séance."""
    debrief = " ".join(PROACTIVE_TRIGGERS["strava_debrief"].split())
    assert "dernier intervalle plus rapide que le premier" not in debrief
    assert "Une dernière un peu plus rapide n'est PAS une faute" in debrief
    assert "une allure ne se compare qu'au sein de la séance" in debrief


def test_the_reprimand_stays_inside_the_perimeter_the_vault_gave_it():
    """La décision du 2026-08-05 (`coaching/current-plan.md`) a RESTREINT le mandat aux
    séances de qualité : étendu aux footings il sonnerait à chaque journée chaude, et un
    garde-fou qui crie sur des faux positifs finit ignoré. Restaurer la formule large
    d'avant le ménage aurait remis le prompt en contradiction avec son fichier
    propriétaire — le prompt ne tranche pas contre le vault, il l'applique."""
    style = " ".join(STYLE_PROMPT.split())
    assert "SÉANCE DE QUALITÉ" in style
    assert "n'est PAS une séance ratée" in style
    assert "2026-08-05" in style
    debrief = " ".join(PROACTIVE_TRIGGERS["strava_debrief"].split())
    assert "Une EF un peu rapide n'entre pas dans ce mandat" in debrief


def test_emotional_turn_follows_the_request_without_a_mandatory_preamble():
    """Audit du 2026-09-30, constat 2 : « reconnais », puis l'aide « après cet
    accueil » imposait le schéma émotion, validation, conseil, y compris devant
    « je suis déçu, donne-moi juste le total »."""
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
    assert "fact-read.py proactive" in debrief
    assert "non servi" in debrief
    # La consigne sépare désormais ce qui se LIT de ce qui s'ÉCRIT : le coach a
    # annoncé « je pose la batterie sans ouvrir directement par la douleur »
    # le 2026-08-19, en paraphrasant un « n'ouvre pas » qui parlait du message.
    assert "AVANT D'ÉCRIRE" in debrief and "DANS LE MESSAGE" in debrief
    # ⭐ Et l'angle a un ENDROIT, sans quoi la séparation ci-dessus ne tient pas : le
    # 2026-08-21 le coach a ouvert par « Angle non répété jusqu'ici [...] Ici je vais
    # partir du titre Strava ». Deux durcissements de prompt avaient déjà échoué, parce
    # qu'ils fermaient la seule sortie que ce raisonnement avait.
    assert "fact-write.py angle" in debrief
    assert "ledger-add.py" in debrief
    assert "DÉJÀ capturé" in debrief
    assert "D- et WBGT compris" in debrief
    assert "NE RÉVISE PAS le plan" in debrief


def test_deferred_debrief_is_about_the_morning_after():
    deferred = PROACTIVE_TRIGGERS["strava_debrief_differe"]
    assert "déjà capturés par le service" in deferred
    assert "AU RÉVEIL" in deferred
    assert "AU RÉVEIL DIRECTEMENT" in deferred
    assert "ne se reporte jamais au soir" in deferred
    assert "`wake-trace`" in deferred
    assert "capacité n°4" in deferred


def test_morning_collects_the_previous_day_wake_reference_immediately():
    morning = PROACTIVE_TRIGGERS["morning"]
    assert "Au réveil même" in morning
    assert "aucune, très légère, légère ou nette" in morning
    assert "Ne demande jamais un chiffre /10 pour le" in morning
    assert "réveil et ne reporte jamais" in morning
    assert "ne reporte jamais cette question au soir ou au lendemain" in morning
    assert "`--dlr` et son mot" in morning


def test_morning_checks_plan_threads_and_upcoming_dates():
    morning = PROACTIVE_TRIGGERS["morning"]
    assert "week-plan.py show/check" in morning
    assert "fact-read.py threads" in morning
    assert "échéances" in morning and "PostgreSQL" in morning
    assert "PRÉVENANCE" in morning
    assert "fact-read.py proactive" in morning
    assert "chart-strength.py" not in morning
    assert "coaching/strength.md" in morning
    assert "le service génère et joint la carte" in morning


def test_weekly_closes_the_loop_and_prepares_next_week():
    weekly = " ".join(PROACTIVE_TRIGGERS["weekly"].split())
    assert "adherence.md" in weekly
    assert "changement de levier" in weekly
    assert "mental-bank.md" in weekly
    # Audit du 2026-09-30, constat 3 : le bilan garde ses opérations, plus ses formules.
    assert "tu t'engages sur quoi" not in weekly
    assert "minimisé" not in weekly
    assert "ce qui change la semaine" in weekly
    assert "Une question seulement si un choix utile reste ouvert" in weekly
    assert "Une condition du jour va dans la `note` de sa séance" in _flat(weekly)
    # Le jeu de calibration est arrêté le 2026-10-09 : plus de tendance à rendre.
    assert "session-cycle.py report" not in weekly


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
    """Les 4 contrôles du § D n'ont pas de colonne au registre : ils se lisent chez
    Strava ou nulle part. Un silence de notes n'est pas une donnée : la batterie dit
    qu'il ne prouve rien."""
    weekly = " ".join(PROACTIVE_TRIGGERS["weekly"].split())
    assert "§ D" in weekly
    assert "son silence ne rassure ni n'alarme" in weekly
    assert "son silence est donc une donnée" not in weekly


def test_weekly_closes_the_predictions_that_came_due():
    """Une prédiction qu'aucun bilan ne ferme n'a jamais été falsifiable : au bilan du
    2026-08-16, trois traînaient ouvertes depuis W30 et W32 faute de lecteur."""
    weekly = " ".join(PROACTIVE_TRIGGERS["weekly"].split())
    assert "fact-read.py predictions --due" in weekly
    assert "fact-write.py prediction --key <clé> --outcome true|false|void" in weekly
    assert "verdict sur CHAQUE prédiction échue" in weekly
    # Audit du 2026-10-09 : 7 prédictions corps confrontées sur 20. Celles des SL et des
    # hausses se rattachent au réveil du lendemain et se soldent depuis le carnet.
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
    # Audit du 2026-09-30, constat 10 : une objection peut révéler une inférence
    # fausse sans apporter de fait nouveau (récidive des 27-28 août).
    assert "S'IL INSISTE" not in _flat(STYLE_PROMPT)
    assert "UNE OBJECTION fait réexaminer tes prémisses" in _flat(STYLE_PROMPT)
    assert "Un désaccord n'est pas un feu vert" in _flat(STYLE_PROMPT)
    assert '"intermittent" n\'est pas "persistant"' in _flat(STYLE_PROMPT)
    assert "si le critère est ambigu, demande" in STYLE_PROMPT
    assert "porte de sortie chiffrée" in STYLE_PROMPT
    assert "UN CHIFFRE NE SE CITE PAS" in STYLE_PROMPT
    assert f"{prompts.AGENT_CLI} ledger.py --semaine" in STYLE_PROMPT
    assert "Vol. cible" in STYLE_PROMPT
    assert "n'annonce pas leur arrivée" in STYLE_PROMPT
    assert f"{prompts.AGENT_CLI} fact-read.py corrections" in STYLE_PROMPT
    assert "Même incident = même clé" in STYLE_PROMPT


def test_the_rating_scales_ride_with_the_levels_where_numbers_are_given():
    """⭐ 2026-08-17 — le porteur des barèmes, et il ne peut pas être la fiche.

    `coaching/protocols.md` ne s'ouvre qu'en N2, or Sam déclare ses douleurs en N1 :
    une règle qui n'existerait que dans la fiche se rendormirait. Elle n'est pas non plus
    dans `STYLE_PROMPT` — cuit au niveau de la session, donc invisible d'une conversation
    déjà ouverte, et son budget était plein. Ce test est là pour que la suppression du
    pointeur soit un choix, pas un effet de bord.
    """
    for level in ("N1", "N2"):
        assert prompts.PAIN_SCALE_POINTER in DEPTH_NOTES[level]
    assert "plafond douleur d'effort = 2" in prompts.PAIN_SCALE_POINTER
    assert "pas de note /10" in prompts.PAIN_SCALE_POINTER
    assert "séance entière" in prompts.PAIN_SCALE_POINTER
    # La tolérance « 2-3 » n'est citée QUE comme interdiction, et nulle part ailleurs.
    assert sum('"2-3 acceptable"' in text for text in DEPTH_NOTES.values()) == 2
    assert "2-3" not in prompts.STYLE_PROMPT


def test_the_morning_debrief_never_asks_for_a_mark_out_of_ten():
    """Son constat du 2026-08-17 : le réveil est « complètement différent et difficile à
    évaluer ». Le trigger porte la formulation, pas seulement l'interdiction."""
    differe = PROACTIVE_TRIGGERS["strava_debrief_differe"]
    assert "NE DEMANDE PAS UNE NOTE SUR 10" in differe
    assert "(aucune, très légère, légère, nette)" in _flat(differe)


def test_the_two_examples_differ_only_by_naming_the_machinery():
    """⭐ Un contre-exemple n'enseigne que ce qui le SÉPARE de son pendant.

    Le premier OUI écrit ici était « Alors, cette EF ? Elle t'a fait quoi ? » : deux
    fois plus court que le NON, et sans aucun angle. Deux défauts que Sam a relevés
    tout de suite. Le contraste enseignait « sois bref » -- pas la règle -- et montrait
    un coach qui demande dans le vide, ce qui est un moins bon coaching que le message
    fautif. Il avait en plus été INVENTÉ, alors que `coaching/protocols.md` déclare la
    batterie : le OUI porte maintenant son révélateur n°9, le comparatif forcé.

    Ce test garde la seule chose qui fait marcher la paire : à longueur comparable, la
    seule différence visible est qu'un des deux nomme sa mécanique.
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

    Le triage porte DEUX budgets parce qu'il a deux clients. `TRIAGE_RULES` est la
    doctrine, payée par les deux moteurs : c'est là que la dérive coûte, donc c'est là
    que le plafond serre. Les deux prompts qui l'enveloppent n'ajoutent que leur
    protocole de sortie — le schéma JSON pour Codex, quatre exemples écrits pour le
    moteur texte, qui n'a rien d'autre pour apprendre le format. Un plafond unique sur
    le tout facturait cet échafaudage au prix de la doctrine, et poussait à couper les
    exemples : ceux qui manquaient le 2026-08-14.

    Le plafond du style est passé de 700 à 800 le 2026-08-17 pour loger
    `TYPOGRAPHY_RULE`, et pour une raison qui vaut d'être nommée : une centaine de mots
    payés une fois par SESSION valent mieux qu'une consigne d'une ligne que le coach
    enfreint à chaque message. Le budget mesure un coût, pas une vertu.

    De 800 à 840 le 2026-08-19, même raison et même forme. Le coach a écrit à Sam
    « Maintenant je pose la batterie sans ouvrir directement par la douleur » : il a
    annoncé sa consigne au lieu de l'appliquer. La règle qui l'interdisait existait,
    abstraite (« ne raconte jamais ta machinerie »), et n'a pas tenu -- « la batterie »
    ne ressemble pas à de la machinerie, ça ressemble à du vocabulaire de coach.
    Ce qui a été acheté avec ces mots est un EXEMPLE, pas une reformulation : le
    2026-08-16 a montré qu'un modèle imite ses propres tours avant d'obéir à une
    consigne, donc un contre-exemple daté vaut plus qu'un adjectif de plus.

    Trois échanges ont été cherchés avant de relever : la consigne de carte de renfo a
    été remise (la retirer rejouerait le 18/08), et la règle a été fondue avec celle
    qu'elle remplace plutôt qu'ajoutée à côté.

    Les déclencheurs proactifs passent de 500 à 520 le 2026-08-21, et pour la
    TROISIÈME fois sur la même faute -- le coach a ouvert son débrief par « Angle non
    répété jusqu'ici [...] Ici je vais partir du titre Strava ». Ce qui est acheté ici
    n'est ni un exemple ni un adjectif : c'est une COMMANDE. `fact-write.py angle`
    donne enfin à l'angle un endroit où aller, parce qu'il n'en avait aucun -- la
    colonne `proactive_messages.angle` n'a jamais été écrite, et un raisonnement à qui
    on ne donne pas de champ finit dans la prose. Les deux durcissements de prompt
    précédents ne pouvaient pas tenir : ils interdisaient la seule sortie disponible.
    """
    # Le lanceur explicite ajoute un mot aux anciennes mentions nues de CLI. Ce coût
    # achète l'alignement matériel avec `dontAsk` après l'incident du 26/08.
    # De 860 à 890 le 30/08 : Redline a reçu les trois ressentis d'une SL, sa CLI a
    # échoué, puis il a répondu comme si l'écriture avait réussi et les a redemandés.
    # Le coût est le contrôle explicite d'exit_code, pas une nouvelle règle éditoriale.
    # Ce plafond borne le CONTEXTE statique en CI. Il ne juge jamais une réponse,
    # ne déclenche aucune régénération et ne peut donc pas faire boucler le coach.
    assert len(STYLE_PROMPT.split()) <= 1040
    assert len(TRIAGE_RULES.split()) <= 300
    assert len(TRIAGE_PROMPT.split()) <= 430
    assert len(TRIAGE_STRUCTURED_PROMPT.split()) <= 350
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
    """RWF-023 : aucun code ne cherche plus « SILENCE » dans la prose d'une initiative."""
    for kind in PROACTIVE_TRIGGERS:
        prompt = prompts.build_proactive_prompt(kind, "42")
        assert "SILENCE" not in prompt, kind
        assert "`send` faux et `text` vide" in prompt, kind


def test_closed_initiatives_have_no_agent_prompt_left():
    """RWF-025 : un type rendu sans modèle ne garde pas un prompt contradictoire."""
    from redline.service.proactive_messages import CLOSED_KINDS

    assert not CLOSED_KINDS & set(PROACTIVE_TRIGGERS)


def test_style_names_the_real_file_reading_path():
    assert "fichiers via `sed`/`rg`" in _flat(STYLE_PROMPT)
    assert "jamais par script inventé" in STYLE_PROMPT


def test_style_never_treats_a_failed_cli_as_a_persisted_fact():
    assert "Code non nul" in STYLE_PROMPT
    assert "ne prétends jamais avoir réussi" in _flat(STYLE_PROMPT)


def test_style_names_the_shell_forms_the_tool_refuses():
    """Audit du 2026-10-11 : 23 appels sur 206 refusés, dont les réveils du 09/10 et du
    11/10, parce que le coach ajoutait `; echo "exit=$?"` pour lire le code de sortie."""
    flat = _flat(STYLE_PROMPT)
    assert "Une CLI par appel, sans `$`, boucle ni heredoc" in flat
    assert "JSON via `--input` (FICHIER DU TOUR)" in flat
    assert "/tmp" not in flat


def test_every_turn_names_its_own_input_file():
    """11/10 : `< /tmp/x.json` refusé par Claude Code 2.1.287, W42 jamais écrite. Le
    tour reçoit un chemin à lui, dans le vault, que la CLI lit par `--input`."""
    note = prompts.turn_input_note("11111111-2222-3333-4444-555555555555")
    path = ".agent-tmp/11111111-2222-3333-4444-555555555555.json"
    assert f"Write dans `{path}`" in note
    assert f"`--input {path}`" in note
    assert " < " not in note


def test_missing_cells_publish_the_real_one_metric_cli_contract():
    note = prompts.missing_cells_note(
        [{"date": "2026-08-30", "type": "long",
          "missing": ["RPE", "Dlr", "Plaisir"]}], [])

    assert (f"{prompts.AGENT_CLI} ledger-complete.py --key <clé> "
            "--metric rpe --value 5") in note
    assert "N'utilise jamais `--rpe`, `--pain`" in note


def test_style_names_relational_memory_and_its_closed_vocabularies():
    assert f"{prompts.AGENT_CLI} fact-write.py thread" in STYLE_PROMPT
    assert "`ouvert` ou `clos`" in _flat(STYLE_PROMPT)
    assert "ces deux mots exactement" in STYLE_PROMPT
    assert "coaching/adherence.md" in STYLE_PROMPT
    assert "coaching/corrections.md" in STYLE_PROMPT
    assert "réservé à TES erreurs" in STYLE_PROMPT


def test_conversation_knows_the_strength_card_exists():
    for depth in ("N1", "N2"):
        assert "chart-strength.py" not in DEPTH_NOTES[depth]
        assert "générée et jointe déterministement" in DEPTH_NOTES[depth]
    assert "Vérifie avant de dire" in STYLE_PROMPT


def test_strava_is_declared_on_every_path_the_coach_can_take():
    """Une capacité déclarée sur un seul chemin n'existe pas sur les autres. Strava ne
    vivait que dans un MCP de `~/.claude.json` : sous Codex, le coach a cherché un accès
    pendant cinq appels avant de rendre SILENCE (2026-08-14). Elle se nomme donc là où
    tous les tours passent — le style, la marche de lecture d'un débrief, et les deux
    déclencheurs qui partent d'une activité."""
    assert f"{prompts.AGENT_CLI} strava.py" in STYLE_PROMPT
    assert "TU AS ACCÈS À STRAVA" in STYLE_PROMPT
    assert f"{prompts.AGENT_CLI} strava.py" in DEPTH_NOTES["N1"]
    for kind in ("strava_debrief", "strava_debrief_differe"):
        assert "INSTANTANÉ STRAVA" in PROACTIVE_TRIGGERS[kind]
        assert f"{prompts.AGENT_CLI} strava.py" not in PROACTIVE_TRIGGERS[kind]


def test_a_debrief_uses_the_captured_snapshot_instead_of_refetching_strava():
    """La capture déterministe précède le modèle : un second appel ne peut que
    réintroduire une panne après que la donnée est déjà acquise."""
    debrief = PROACTIVE_TRIGGERS["strava_debrief"]
    assert "INSTANTANÉ STRAVA" in debrief
    assert "Ne refais pas l'appel fournisseur" in debrief


def test_every_command_the_prompts_hand_out_is_runnable_as_written():
    """Le prompt ne peut citer que le lanceur effectivement autorisé par Claude."""
    for text in (STYLE_PROMPT, *DEPTH_NOTES.values(), *PROACTIVE_TRIGGERS.values()):
        flat = " ".join(text.split())
        assert "python3 scripts/" not in flat
        assert "`scripts/" not in flat.replace("`scripts/` direct", "")
        for command in ("strava.py", "descent.py", "weather.py"):
            if f"`{prompts.AGENT_CLI} {command}" in flat:
                assert f"{prompts.AGENT_CLI} {command}" in flat


def test_logging_a_session_never_rewrites_the_plan_execution_state():
    """Le réalisé appartient au cycle ; seule une prescription future justifie rN+1."""
    style = " ".join(STYLE_PROMPT.split())
    assert "jamais pour marquer `done`" in style
    assert "le réalisé appartient au cycle" in style
    debrief = " ".join(PROACTIVE_TRIGGERS["strava_debrief"].split())
    assert "NE RÉVISE PAS le plan" in debrief
    assert "prescription future change" in debrief


def test_a_stale_plan_asks_for_a_revision_before_the_turn_not_after():
    """Le contrôle qui détectait la péremption tournait APRÈS la réponse : il ne pouvait
    que détruire du travail déjà fait. La même information, servie en tête du tour,
    devient actionnable."""
    note = prompts.stale_plan_note("2026-W33")
    assert "invisible pour ⟨athlète⟩" in note
    assert "2026-W33" in note
    assert "tu réponds normalement" in note
    assert "AUCUNE prescription" in note
    assert "week-plan.py revise --week 2026-W33" in note
    assert "Ne parle jamais de ce contrôle" in note


def test_the_cadence_is_never_doubled_twice():
    """Le ×2 est passé dans scripts/strava.py, qui sait distinguer un pas d'un tour de
    pédalier. Le prompt doit donc dire de NE PAS le refaire, sinon 168 devient 336."""
    assert "ne la multiplie pas une deuxième fois" in PROACTIVE_TRIGGERS["strava_debrief"]


def test_the_daily_question_stays_subjective():
    """La question d'état reste une question de coach, jamais un relevé."""
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


PENDING = [{"date": "2026-08-04", "type": "strength", "missing": ["RPE"]}]
LAPSED = [{"date": "2026-08-01", "type": "easy", "missing": ["Plaisir"]}]


def test_no_missing_cell_means_no_note():
    assert prompts.missing_cells_note([], []) == ""


def test_wake_cells_do_not_enter_the_generic_missing_cells_note():
    wake = [{"date": "2026-09-14", "type": "wake-trace", "missing": ["Dlr"]}]

    assert prompts.missing_cells_note(wake, wake) == ""


def test_pending_cells_do_not_force_a_questionnaire_or_block_partial_answers():
    note = prompts.missing_cells_note(
        [{"date": "2026-08-10", "type": "easy",
          "missing": ["RPE", "Dlr", "Plaisir"]}], []
    )
    assert "une seule information" in note
    assert "Ne bloque pas sur les autres cellules" in note
    assert "réponse partielle" in note
    assert "questionnaire" in note
    assert "Ne mentionne jamais cette note" in note


def test_with_the_notebook_pending_cells_are_left_to_it_except_pain():
    note = prompts.missing_cells_note(
        [{"date": "2026-10-08", "type": "easy", "missing": ["RPE", "Plaisir"]}], [],
        carnet=True)
    assert "Le carnet les recueille : ne les demande pas" in note
    assert "douleur si le message montre une gêne" in note
    assert "une seule information" not in note
    # Une valeur donnée spontanément s'écrit toujours par le même contrat.
    assert "--metric rpe" in note


def test_walking_is_never_asked():
    """Décision de Sam du 2026-10-11 : la marche ne se demande jamais, même quand un
    contrôle daté ancien la nomme encore ; il signale lui-même une gêne."""
    for level in ("N1", "N2"):
        assert "jamais la marche, il signale une gêne" in DEPTH_NOTES[level]
    note = prompts.gate_note([{
        "id": "injury", "holds": ["threshold"],
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

    claim = compose_closed_proactive("registre_incomplet", json.dumps({
        "date": "2026-09-24", "type": "easy", "missing": ["RPE"],
        "markers": ["2026-09-24:easy:RPE"]}))
    assert claim is not None
    assert "registre" not in claim.text and "donnée" not in claim.text
    assert claim.markers == ("2026-09-24:easy:RPE",)


def test_no_injected_text_types_a_glyph_a_phone_keyboard_does_not_carry():
    """⭐ LE PORTEUR DE LA RÈGLE TYPOGRAPHIQUE. « Pas de tiret cadratin » vivait depuis
    des semaines dans le style, et le coach en mettait quand même (Sam, 2026-08-17 :
    « tu utilises encore des tirets quadratins et autres symboles pas naturels »). La
    consigne n'était pas fausse, elle était CONTREDITE PAR SON PROPRE CONTEXTE : les
    notes de niveau, les déclencheurs proactifs et les instructions invisibles étaient
    truffés de « — », de « · » et de guillemets français. Un modèle imite ce qu'il lit
    avant d'obéir à ce qu'on lui dit, et une règle démontrée à l'envers ne tient pas un
    tour. Ce test tient l'autre bout : le prompt ne peut plus taper ce qu'il interdit.

    Seule `TYPOGRAPHY_RULE` y échappe, parce qu'elle doit NOMMER les signes pour les
    interdire; elle est retranchée avant le scan.
    """
    injected = {
        "STYLE_PROMPT": STYLE_PROMPT.replace(prompts.TYPOGRAPHY_RULE, ""),
        "TRIAGE_PROMPT": TRIAGE_PROMPT,
        "TRIAGE_STRUCTURED_PROMPT": TRIAGE_STRUCTURED_PROMPT,
        "EMOTION_NOTE": EMOTION_NOTE,
        "STALE_VAULT_NOTE": prompts.STALE_VAULT_NOTE,
        "STRENGTH_CARD_POINTER": prompts.STRENGTH_CARD_POINTER,
        "HELP_TEXT": prompts.HELP_TEXT,
        "stale_plan_note": prompts.stale_plan_note("2026-W34"),
        "gate_note": prompts.gate_note(
            [{"due": "2026-08-17", "holds": ["w34-dim-sl"],
              "question": "La blessure a tenu la sortie longue ?",
              "on_fail": "on retire les côtes de jeudi"}]),
        # Les deux moitiés se scannent séparément : la note d'observation est un texte
        # injecté à part entière, et elle est arrivée après ce test (2026-08-18).
        "gate_note[observation]": prompts.gate_note(
            [], [{"due": "2026-08-18", "question": "La cheville a parlé pendant la "
                  "pliométrie ?", "on_fail": "elle sort de la séance A"}]),
        # Les quatre textes rapatriés de `bot.py` (2026-08-19) : ils étaient injectés
        # à chaque tour sans qu'aucun contrôle puisse les prendre en faute.
        "canonical_plan_note": prompts.canonical_plan_note(""),
        "active_revision_note": prompts.active_revision_note(""),
        "deep_handoff_note": prompts.deep_handoff_note("question puis réponse"),
        "missing_cells_note": prompts.missing_cells_note(
            [{"date": "2026-08-16", "type": "easy", "missing": ["RPE", "Dlr", "D-"]},
             {"date": "2026-08-15", "type": "threshold", "missing": ["Plaisir"]}], LAPSED),
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
    """⭐ LA MOITIÉ QUI MANQUAIT AU PORTEUR DU 2026-08-17.

    Le test au-dessus scanne les chaînes écrites dans le code. Or le plus gros bloc
    injecté à chaque tour prescriptif est le programme lui-même
    (`weekly_plan.as_json()`), et ce sont des textes que le COACH a écrits la veille :
    `reason`, `constraints`, les `question`/`on_fail` des contrôles datés, les
    `title`/`note` des séances. `prompts.gate_note()` en interpole deux VERBATIM dans
    l'instruction invisible.

    Au 2026-08-19 le programme W34 en portait quatre, dont un tiret cadratin dans la
    question du contrôle du 23/08 et un signe moins dans une `note`, qui part sur le
    téléphone de Sam. La consigne se faisait donc démentir par son propre contexte,
    exactement comme avant le 17/08, mais par le canal des données.

    Le refus est posé à l'ÉCRITURE (`week-plan.py revise`) et pas dans `validate` :
    l'historique est append-only et `revise` valide le plan entier, donc la règle
    appliquée à la lecture refuserait la révision du jour à cause du texte de la veille.
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
    # Le message ouvre le chemin correct, il ne se contente pas de fermer le mauvais
    # (⭐ 2026-08-06 : un garde-fou sans chemin correct fabrique son contournement).
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
    """Deux listes qui dérivent, c'est une règle appliquée à un canal sur deux."""
    from redline.model import typography
    assert prompts.BANNED_GLYPHS is typography.BANNED_GLYPHS


def test_the_write_guard_is_wired_into_the_revise_path_not_merely_available():
    """Une fonction de contrôle que personne n'appelle est une intention, pas un
    contrôle -- c'est exactement le défaut que cet audit a trouvé ailleurs
    (`vault-check.py` certifiait qu'une règle NOMMAIT son porteur, jamais qu'il la
    portait). Celui-ci doit donc se voir sur le chemin d'écriture lui-même."""
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
    """Audit du 2026-10-09 : la restriction sans prémisse, faute n°1 sur une prescription."""
    assert "`motif`" in prompts.plan_change_note()
    assert "`demande`" in prompts.plan_change_note()
    assert "`motif`" in DEPTH_NOTES["N2"]
    assert "`motif`" in PROACTIVE_TRIGGERS["weekly_repair"]


def test_weekly_uses_the_dossier_and_closes_every_due_prediction():
    weekly = " ".join(PROACTIVE_TRIGGERS["weekly"].split())
    assert "DOSSIER DU BILAN" in weekly and "ne le recalcule pas" in weekly
    assert "fact-read.py predictions --due" in weekly
    assert "--reason" in weekly and "decisions-index" not in weekly
    assert "protocole d'instruction des baisses" in weekly
    assert "action prête à faire" in weekly and "repli daté" in weekly
    assert "Le service crée le journal" in weekly


def test_the_weekly_commitment_is_gone():
    """Six bilans de suite sans engagement (W33-W38), puis Sam le 2026-09-30 : « de
    toute façon j'ai pas envie de répondre à ça ». Le levier est retiré, pas allégé :
    ni question, ni champ dans le journal de la semaine."""
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
    assert "Juge le RÉSULTAT" in monthly and "--cible-sl" in monthly


def test_a_session_is_bound_to_the_instruction_files_it_actually_read(tmp_path):
    """Audit du 2026-09-30, constat 12 : le contrat ne portait que le NOM de la
    constitution. Une règle corrigée dans CLAUDE.md laissait vivre le fil qui avait lu
    l'ancienne ; un commit de notes, lui, ne doit faire repartir personne de zéro."""
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
    """Constats 1 et 8 : le triage place toute correction en N3. La profondeur de
    lecture ne dicte ni les sources à ouvrir ni la longueur envoyée."""
    note = " ".join(DEPTH_NOTES["N3"].split())
    assert "relis son propriétaire, pas la littérature" in note
    assert "réponds en une phrase si elle suffit" in note
    assert "La profondeur lue ne dicte pas la longueur envoyée" in note
    assert "N'affirme rien sans source lue dans cette session" in note
    style = _flat(STYLE_PROMPT)
    assert "Vérifie autant que nécessaire" in style
    assert "prends la place nécessaire" not in style


def test_tracking_is_reserved_for_expected_information():
    """Constats 6 et 9 : toute question n'est pas une dette, toute inférence n'ouvre
    pas une question."""
    style = _flat(STYLE_PROMPT)
    assert "Déclare toute question" not in style
    assert "Information attendue seulement, jamais une question sociale" in style
    assert "n'ouvre une question que si la réponse change la décision" in style


def test_an_observation_does_not_hijack_a_social_message():
    """Constat 7 : en N0, la branche observation imposait de prescrire la journée et
    de poser la question du soir, contre la note N0 qui demande de respecter une
    clôture. Un contrôle bloquant, lui, reste entier à tous les niveaux."""
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
    """Audit du 2026-10-11 : 13 débriefs sur 26 ouvraient sur trois chiffres de Strava."""
    debrief = _flat(PROACTIVE_TRIGGERS["strava_debrief"])
    assert "Ta première phrase porte l'écart de `ecarts` qui compte" in debrief
    assert "jamais trois chiffres de la montre à la suite" in debrief
    assert "`ecarts`, s'il est présent" in prompts.strava_snapshot_note({})


def test_a_skipped_session_is_cancelled_in_the_same_turn():
    """13/10 : renfo annoncé sauté à 19 h, la charge de vie seule notée, et la relance
    de 22 h l'a redemandé. La relance est un rendu fermé : c'est la révision qui doit
    arriver avant elle (`coaching/corrections.md`)."""
    note = " ".join(prompts.plan_change_note().split())
    assert "QU'IL ANNONCE SAUTER" in note
    assert "`cancelled`, motif `demande`" in note


def test_a_quality_session_is_judged_on_its_written_criteria_only():
    """15/10 : « sans cible de FC » prescrit, puis un doute fondé sur la FC 178."""
    debrief = _flat(PROACTIVE_TRIGGERS["strava_debrief"])
    assert "SEULS critères écrits dans sa séance" in debrief
    assert "la FC n'est ni doute ni reproche" in debrief
    assert "Calibrations de sa fiche dans `coaching/workouts/`" in debrief


def test_the_workout_card_is_no_longer_the_models_choice():
    assert "chart-workout" not in " ".join(PROACTIVE_TRIGGERS.values())
    assert "chart-workout" not in STYLE_PROMPT
    assert "chart-workout.py" not in (SERVER_DIR / "agent-cli-tools.txt").read_text()
