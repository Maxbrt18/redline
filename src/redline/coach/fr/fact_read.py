"""What `redline fact-read` prints for the coach."""

from __future__ import annotations

ALL_FEMININE = "toutes"

CONFIRMED = "confirmées"

ALL = "tous"

OPEN = "ouverts"

NO_FOLLOW_UP = "sans relance"

ACTIVE = "actives"

NOT_SET = "non fixée"

OBSERVATIONS = "Observations récentes ({rows})"

KM = " km"

BPM = " bpm"

RPE = "/10 RPE"

ENJOYMENT = "/10 plaisir"

TO_COMPLETE = "à compléter : "

KEY = "clé={idempotency_key}"

EXPOSURES = "Expositions {scope} ({rows})"

DOSE_MINUTES = " · {duration_min} min"

DOSE_TARGET = " · cible {target_value} {target_unit}"

EXPOSURE = (
    "{occurred_on} · {exposure_type} · {origin} · {status} · tolérance {tolerance}{dose} "
    "· observation={observation_key} · clé={exposure_key}")

NOTE = " · {note}"

STRENGTH_SESSIONS = "Séances de renfo récentes ({rows})"

STRENGTH = "renfo {workout}"

MINUTES = "{duration_minutes} min"

PAIN = "douleur "

STRENGTH_KEY = "clé={session_key}"

DIFFICULTY = "difficulté {difficulty}/10"

SKIPPED = "passé"

SETS = "séries "

SIDE = "{side}="

STRENGTH_NOTE = "  note : "

THREADS = "Fils {scope} ({rows})"

THREAD = "{thread_key} · {opened_on} · {kind} · {follow_up} · {status} · {body}"

DEADLINES = "Échéances {scope} ({rows})"

BLOCKS = " · bloque {blocks}"

PERIOD = " · période {start}→{end}"

DEADLINE = (
    "{deadline_key} · {due_on} · {kind} · {status} · {item} · propriétaire "
    "{fact_owner}{blocked}")

NOTEBOOK_LABEL = " · carnet de l'athlète : « {athlete_label} »"

CAPABILITIES = "Capacités ({rows})"

LOCK = " · verrou {lock_reason}"

NEXT_TEST = " · prochain test {next_test}"

CAPABILITY = (
    "{capability_number} · {slug} · {score}/{scale} · {status} · évaluée le "
    "{evaluated_on}{test}{lock}")

PROACTIVE = "Messages proactifs récents ({rows})"

ANGLE = " · angle {angle}"

CORRECTIONS = "Corrections enregistrées ({rows})"

DELIVERED = "livrée le {delivered_at}"

NOT_ANNOUNCED = "non annoncée, délai dépassé"

TO_ANNOUNCE = "à annoncer"

RECURRENCES = " · {revisions} récidive(s) depuis le {since}"

CORRECTION = "{correction_key} · {delivery}{recurrence} · {summary}"

GEAR = "Matériel ({rows})"

GEAR_LINE = (
    "{gear_id} · {name} · {role} · {distance_km:.1f} km · seuil contrôle "
    "{retire_at_km:.1f} km · {status}")

PRIMARY = " · principale"

PREDICTIONS_DUE = "Prédictions échues"

PREDICTIONS = "Prédictions"

CONTRADICTED = " · contredite"

CONSUMED = " · consommée"

BRIER = " · Brier {brier_score:.3f}"

ZONE = " ({resolves_zone})"

BOUND = " · se solde au {kind}{zone} du {resolves_on} si {success_op}{success_value:g}"

PREDICTION = (
    "{prediction_key} · {domain} · p={probability:.0%} · échéance {due_on} · "
    "{status}{score}{bound}{challenge}{used} · {statement}")

RACE_PLAN = "Rétroplanning course ({races} course(s))"

MILESTONE = "  {date} · {title} · {action} · preuve : {evidence}"

QUESTION = (
    "{id} · {status} · posée {asked_at} · reprise {follow_up} · {question} · "
    "{review_note}")

MESSAGE = "Message {id} · {created_at} · {role} : "

MESSAGE_CONTEXT = "  {label} · {created_at} · {role} : "

NO_EXCHANGE = (
    "Aucun échange retrouvé. Une absence de résultat ne prouve pas que le sujet n'a "
    "jamais été discuté.")

NO_PERMIT = "Permis EF : aucun choix scellé."
