"""Noms des intentions transactionnelles comprises par le stockage."""

OBSERVATION_ADD = "observation.add"
ACTIVITY_LINK = "activity.link"
MEASUREMENT_COMPLETE = "measurement.complete"
#: FC moyenne déclarée douteuse (sans brassard, ou contexte hors fatigue), 2026-10-20.
HR_DOUBT = "measurement.hr_doubt"
EXPOSURE_UPSERT = "exposure.upsert"
PLAN_REVISE = "plan.revise"
DAILY_UPSERT = "daily.upsert"
CAPABILITY_EVALUATE = "capability.evaluate"
THREAD_UPSERT = "thread.upsert"
QUESTION_REVIEW = "question.review"
QUESTION_ASK = "question.ask"
DEADLINE_UPSERT = "deadline.upsert"
SESSION_START = "session.start"
SESSION_MATCH = "session.match"
#: L'angle d'attaque choisi pour un message proactif. Une commande de tour, donc annulée
#: avec le tour ; elle ne mute rien, elle DÉPOSE — voir `OperationalStore.turn_angle`.
PROACTIVE_ANGLE = "proactive.angle"
GEAR_UPSERT = "gear.upsert"
PREDICTION_UPSERT = "prediction.upsert"
CORRECTION_REGISTER = "correction.register"
#: Permis EF : le choix du coach, scellé avant que Sam ne décide (2026-09-27).
PERMIT_SEAL = "permit.seal"

ACTIVE = (
    OBSERVATION_ADD, ACTIVITY_LINK, MEASUREMENT_COMPLETE, HR_DOUBT, EXPOSURE_UPSERT, PLAN_REVISE,
    DAILY_UPSERT, CAPABILITY_EVALUATE,
    THREAD_UPSERT, QUESTION_REVIEW, QUESTION_ASK, DEADLINE_UPSERT,
    SESSION_START, SESSION_MATCH,
    PROACTIVE_ANGLE,
    GEAR_UPSERT, PREDICTION_UPSERT, CORRECTION_REGISTER, PERMIT_SEAL,
)
