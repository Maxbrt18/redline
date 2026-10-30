"""Names of the transactional intents understood by the storage layer."""

OBSERVATION_ADD = "observation.add"
ACTIVITY_LINK = "activity.link"
MEASUREMENT_COMPLETE = "measurement.complete"
#: Average heart rate declared doubtful (no armband, or a non-fatigue context).
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
#: The angle chosen for a proactive message. A turn command, so cancelled with the turn;
#: it mutates nothing, it DEPOSITS -- see `OperationalStore.turn_angle`.
PROACTIVE_ANGLE = "proactive.angle"
GEAR_UPSERT = "gear.upsert"
PREDICTION_UPSERT = "prediction.upsert"
CORRECTION_REGISTER = "correction.register"
#: Easy-run permit: the coach's choice, sealed before the athlete decides.
PERMIT_SEAL = "permit.seal"

ACTIVE = (
    OBSERVATION_ADD, ACTIVITY_LINK, MEASUREMENT_COMPLETE, HR_DOUBT, EXPOSURE_UPSERT, PLAN_REVISE,
    DAILY_UPSERT, CAPABILITY_EVALUATE,
    THREAD_UPSERT, QUESTION_REVIEW, QUESTION_ASK, DEADLINE_UPSERT,
    SESSION_START, SESSION_MATCH,
    PROACTIVE_ANGLE,
    GEAR_UPSERT, PREDICTION_UPSERT, CORRECTION_REGISTER, PERMIT_SEAL,
)
