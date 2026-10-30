CREATE TYPE redline.gate_when AS ENUM (
    'before',
    'after'
);

CREATE FUNCTION redline.forbid_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
          RAISE EXCEPTION 'the audit log is append-only';
        END; $$;

CREATE FUNCTION redline.forbid_plan_revision_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
          RAISE EXCEPTION 'a plan revision is append-only';
        END; $$;

CREATE FUNCTION redline.forbid_proactive_message_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
          RAISE EXCEPTION 'the proactive message trace is append-only';
        END; $$;

CREATE FUNCTION redline.protect_measurement_history() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
          IF (OLD.distance_km_state = 'known' AND (NEW.distance_km_state, NEW.distance_km) IS DISTINCT FROM (OLD.distance_km_state, OLD.distance_km)) OR (OLD.duration_s_state = 'known' AND (NEW.duration_s_state, NEW.duration_s) IS DISTINCT FROM (OLD.duration_s_state, OLD.duration_s)) OR (OLD.pace_s_per_km_state = 'known' AND (NEW.pace_s_per_km_state, NEW.pace_s_per_km) IS DISTINCT FROM (OLD.pace_s_per_km_state, OLD.pace_s_per_km)) OR (OLD.hr_avg_bpm_state = 'known' AND (NEW.hr_avg_bpm_state, NEW.hr_avg_bpm) IS DISTINCT FROM (OLD.hr_avg_bpm_state, OLD.hr_avg_bpm)) OR (OLD.hr_max_bpm_state = 'known' AND (NEW.hr_max_bpm_state, NEW.hr_max_bpm) IS DISTINCT FROM (OLD.hr_max_bpm_state, OLD.hr_max_bpm)) OR (OLD.cadence_spm_state = 'known' AND (NEW.cadence_spm_state, NEW.cadence_spm) IS DISTINCT FROM (OLD.cadence_spm_state, OLD.cadence_spm)) OR (OLD.elevation_gain_m_state = 'known' AND (NEW.elevation_gain_m_state, NEW.elevation_gain_m) IS DISTINCT FROM (OLD.elevation_gain_m_state, OLD.elevation_gain_m)) OR (OLD.elevation_loss_m_state = 'known' AND (NEW.elevation_loss_m_state, NEW.elevation_loss_m) IS DISTINCT FROM (OLD.elevation_loss_m_state, OLD.elevation_loss_m)) OR (OLD.wbgt_c_state = 'known' AND (NEW.wbgt_c_state, NEW.wbgt_c) IS DISTINCT FROM (OLD.wbgt_c_state, OLD.wbgt_c)) OR (OLD.rpe_state = 'known' AND (NEW.rpe_state, NEW.rpe) IS DISTINCT FROM (OLD.rpe_state, OLD.rpe)) OR (OLD.enjoyment_state = 'known' AND (NEW.enjoyment_state, NEW.enjoyment) IS DISTINCT FROM (OLD.enjoyment_state, OLD.enjoyment)) OR (OLD.carbs_g_h_state = 'known' AND (NEW.carbs_g_h_state, NEW.carbs_g_h) IS DISTINCT FROM (OLD.carbs_g_h_state, OLD.carbs_g_h)) OR (OLD.gut_symptoms_state = 'known' AND (NEW.gut_symptoms_state, NEW.gut_symptoms) IS DISTINCT FROM (OLD.gut_symptoms_state, OLD.gut_symptoms)) OR (OLD.reps_in_reserve_state = 'known' AND (NEW.reps_in_reserve_state, NEW.reps_in_reserve) IS DISTINCT FROM (OLD.reps_in_reserve_state, OLD.reps_in_reserve)) THEN
            RAISE EXCEPTION 'a known measurement is immutable; record a correcting observation';
          END IF;
          RETURN NEW;
        END;
        $$;

CREATE FUNCTION redline.protect_pain_reading() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
          IF OLD.state = 'known' AND (NEW.state, NEW.value) IS DISTINCT FROM
             (OLD.state, OLD.value) THEN
            RAISE EXCEPTION 'a known measurement is immutable; record a correcting observation';
          END IF;
          RETURN NEW;
        END;
        $$;

CREATE FUNCTION redline.protect_plan_revision() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE expected integer;
        BEGIN
          SELECT COALESCE(MAX(revision), 0) + 1 INTO expected
          FROM redline.plan_revisions WHERE plan_id = NEW.plan_id;
          IF NEW.revision <> expected THEN
            RAISE EXCEPTION 'expected revision %, got %', expected, NEW.revision;
          END IF;
          RETURN NEW;
        END;
        $$;

CREATE FUNCTION redline.protect_strength_session_link() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
          IF NEW.token_hash IS DISTINCT FROM OLD.token_hash
             OR NEW.psid IS DISTINCT FROM OLD.psid
             OR NEW.session_key IS DISTINCT FROM OLD.session_key
             OR NEW.snapshot IS DISTINCT FROM OLD.snapshot
             OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'the snapshot of a delivered session is immutable';
          END IF;
          IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
            RAISE EXCEPTION 'revoking a session cannot be undone';
          END IF;
          RETURN NEW;
        END; $$;

CREATE TABLE redline.agent_sessions (
    psid text NOT NULL,
    provider text NOT NULL,
    session_id text NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    prompt_digest text,
    CONSTRAINT ck_agent_sessions_identity CHECK (((psid <> ''::text) AND (session_id <> ''::text))),
    CONSTRAINT ck_agent_sessions_provider CHECK ((provider = ANY (ARRAY['codex'::text, 'claude'::text])))
);

CREATE TABLE redline.athlete_activity_states (
    athlete_id text NOT NULL,
    last_activity_at timestamp with time zone NOT NULL
);

CREATE TABLE redline.athletes (
    id text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_athletes_id CHECK ((id ~ '^[a-z][a-z0-9-]{1,63}$'::text))
);

CREATE TABLE redline.audit_events (
    id uuid NOT NULL,
    actor text NOT NULL,
    action text NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE TABLE redline.capability_evaluations (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    capability_number smallint NOT NULL,
    slug text NOT NULL,
    need text NOT NULL,
    score smallint NOT NULL,
    scale smallint NOT NULL,
    status text NOT NULL,
    evaluated_on date NOT NULL,
    next_test text,
    lock_reason text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_capability_evaluations_number CHECK (((capability_number >= 1) AND (capability_number <= 9))),
    CONSTRAINT ck_capability_evaluations_scale CHECK (((scale >= 1) AND (scale <= 9))),
    CONSTRAINT ck_capability_evaluations_score CHECK (((score >= 0) AND (score <= scale))),
    CONSTRAINT ck_capability_evaluations_status CHECK ((status = ANY (ARRAY['red'::text, 'amber'::text, 'green'::text])))
);

CREATE TABLE redline.coach_predictions (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    prediction_key text NOT NULL,
    domain text NOT NULL,
    statement text NOT NULL,
    probability numeric(4,3) NOT NULL,
    due_on date NOT NULL,
    direction text DEFAULT 'neutral'::text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    outcome boolean,
    brier_score numeric(7,6),
    challenge text,
    challenged_at timestamp with time zone,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    resolved_at timestamp with time zone,
    resolves_kind text,
    resolves_on date,
    success_op text,
    success_value numeric(4,1),
    resolves_zone text,
    outcome_reason text,
    CONSTRAINT ck_coach_predictions_direction CHECK ((direction = ANY (ARRAY['increase'::text, 'decrease'::text, 'neutral'::text]))),
    CONSTRAINT ck_coach_predictions_outcome_reason CHECK (((outcome_reason IS NULL) OR ((status = 'resolved'::text) AND (outcome = false) AND (outcome_reason = ANY (ARRAY['optimistic'::text, 'cautious'::text]))) OR ((status = 'void'::text) AND (outcome_reason = ANY (ARRAY['instrument'::text, 'exposure'::text, 'scope'::text, 'superseded'::text]))))),
    CONSTRAINT ck_coach_predictions_probability CHECK (((probability >= (0)::numeric) AND (probability <= (1)::numeric))),
    CONSTRAINT ck_coach_predictions_resolution CHECK ((((status = 'resolved'::text) AND (outcome IS NOT NULL) AND (brier_score IS NOT NULL) AND (resolved_at IS NOT NULL)) OR ((status = 'open'::text) AND (outcome IS NULL) AND (brier_score IS NULL) AND (resolved_at IS NULL)) OR ((status = 'void'::text) AND (outcome IS NULL) AND (brier_score IS NULL) AND (resolved_at IS NOT NULL)))),
    CONSTRAINT ck_coach_predictions_resolves CHECK ((((resolves_kind IS NULL) AND (resolves_on IS NULL) AND (resolves_zone IS NULL) AND (success_op IS NULL) AND (success_value IS NULL)) OR ((resolves_kind = 'wake-trace'::text) AND (resolves_on IS NOT NULL) AND (resolves_zone IS NOT NULL) AND (success_op = ANY (ARRAY['<='::text, '<'::text, '>='::text, '>'::text, '='::text])) AND (success_value IS NOT NULL) AND (resolves_on <= due_on)))),
    CONSTRAINT ck_coach_predictions_status CHECK ((status = ANY (ARRAY['open'::text, 'resolved'::text, 'void'::text])))
);

CREATE TABLE redline.conversation_messages (
    id uuid NOT NULL,
    psid text NOT NULL,
    role text NOT NULL,
    body text NOT NULL,
    source_key text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_conversation_messages_body CHECK ((body <> ''::text)),
    CONSTRAINT ck_conversation_messages_role CHECK ((role = ANY (ARRAY['athlete'::text, 'coach'::text, 'proactive'::text])))
);

CREATE TABLE redline.conversation_questions (
    id uuid NOT NULL,
    psid text NOT NULL,
    fingerprint text NOT NULL,
    question text NOT NULL,
    source_message_id uuid NOT NULL,
    source_platform_message_id text,
    status text DEFAULT 'open'::text NOT NULL,
    candidate_answer_message_id uuid,
    asked_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    resolved_at timestamp with time zone,
    review_note text DEFAULT ''::text NOT NULL,
    follow_up_on date,
    CONSTRAINT ck_conversation_questions_follow_up CHECK (((status = 'deferred'::text) OR (follow_up_on IS NULL))),
    CONSTRAINT ck_conversation_questions_note CHECK ((length(review_note) <= 500)),
    CONSTRAINT ck_conversation_questions_resolution CHECK (((status = ANY (ARRAY['open'::text, 'deferred'::text])) = (resolved_at IS NULL))),
    CONSTRAINT ck_conversation_questions_status CHECK ((status = ANY (ARRAY['open'::text, 'deferred'::text, 'answered'::text, 'expired'::text])))
);

CREATE TABLE redline.conversation_threads (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    thread_key text NOT NULL,
    opened_on date NOT NULL,
    kind text NOT NULL,
    body text NOT NULL,
    follow_up_on date,
    status text NOT NULL,
    closed_at timestamp with time zone,
    CONSTRAINT ck_conversation_threads_closed_at CHECK (((status = 'closed'::text) = (closed_at IS NOT NULL))),
    CONSTRAINT ck_conversation_threads_kind CHECK ((kind = ANY (ARRAY['promise'::text, 'feeling'::text, 'open_loop'::text]))),
    CONSTRAINT ck_conversation_threads_status CHECK ((status = ANY (ARRAY['open'::text, 'closed'::text])))
);

CREATE TABLE redline.correction_deliveries (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    correction_key text NOT NULL,
    summary text NOT NULL,
    registered_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    delivered_at timestamp with time zone,
    CONSTRAINT ck_correction_deliveries_identity CHECK (((correction_key <> ''::text) AND (summary <> ''::text)))
);

CREATE TABLE redline.daily_checkins (
    athlete_id text NOT NULL,
    checked_on date NOT NULL,
    sleep text,
    life_load text,
    desire text,
    note text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_daily_checkins_desire CHECK (((desire IS NULL) OR (desire = ANY (ARRAY['high'::text, 'normal'::text, 'low'::text])))),
    CONSTRAINT ck_daily_checkins_life_load CHECK (((life_load IS NULL) OR (life_load = ANY (ARRAY['light'::text, 'normal'::text, 'high'::text])))),
    CONSTRAINT ck_daily_checkins_sleep CHECK (((sleep IS NULL) OR (sleep = ANY (ARRAY['good'::text, 'fair'::text, 'poor'::text]))))
);

CREATE TABLE redline.deadlines (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    deadline_key text NOT NULL,
    due_on date NOT NULL,
    item text NOT NULL,
    fact_owner text NOT NULL,
    blocks text,
    status text NOT NULL,
    completed_on date,
    kind text DEFAULT 'general'::text NOT NULL,
    starts_on date,
    ends_on date,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    athlete_label text,
    CONSTRAINT ck_deadlines_athlete_label CHECK (((athlete_label IS NULL) OR ((char_length(athlete_label) >= 3) AND (char_length(athlete_label) <= 80)))),
    CONSTRAINT ck_deadlines_completed_on CHECK (((completed_on IS NULL) OR (status = 'done'::text))),
    CONSTRAINT ck_deadlines_kind CHECK ((kind = ANY (ARRAY['general'::text, 'medical'::text, 'availability'::text, 'race'::text]))),
    CONSTRAINT ck_deadlines_range CHECK (((starts_on IS NULL) OR (ends_on IS NULL) OR (starts_on <= ends_on))),
    CONSTRAINT ck_deadlines_status CHECK ((status = ANY (ARRAY['todo'::text, 'scheduled'::text, 'done'::text])))
);

CREATE TABLE redline.delivery_debts (
    psid text NOT NULL,
    iso_week text NOT NULL,
    revision integer NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_delivery_debts_revision CHECK ((revision >= 1)),
    CONSTRAINT ck_delivery_debts_week CHECK ((iso_week ~ '^20[0-9]{2}-W(0[1-9]|[1-4][0-9]|5[0-3])$'::text))
);

CREATE TABLE redline.gear_items (
    gear_id text NOT NULL,
    athlete_id text NOT NULL,
    name text NOT NULL,
    role text NOT NULL,
    retire_at_km numeric(7,1) NOT NULL,
    strava_distance_km numeric(9,1),
    distance_synced_at timestamp with time zone,
    "primary" boolean DEFAULT false NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_gear_items_identity CHECK (((gear_id <> ''::text) AND (name <> ''::text))),
    CONSTRAINT ck_gear_items_retire CHECK ((retire_at_km > (0)::numeric)),
    CONSTRAINT ck_gear_items_status CHECK ((status = ANY (ARRAY['active'::text, 'retired'::text]))),
    CONSTRAINT ck_gear_items_strava_distance CHECK (((strava_distance_km IS NULL) OR (strava_distance_km >= (0)::numeric)))
);

CREATE TABLE redline.handoffs (
    psid text NOT NULL,
    body text NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE redline.inbound_activity_states (
    psid text NOT NULL,
    last_inbound_at timestamp with time zone NOT NULL
);

CREATE TABLE redline.inbound_messages (
    message_id text NOT NULL,
    psid text NOT NULL,
    kind text NOT NULL,
    body text NOT NULL,
    reply_to_message_id text,
    status text DEFAULT 'pending'::text NOT NULL,
    received_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT ck_inbound_messages_identity CHECK (((message_id <> ''::text) AND (psid <> ''::text))),
    CONSTRAINT ck_inbound_messages_kind CHECK ((kind = ANY (ARRAY['text'::text, 'postback'::text, 'sticker'::text, 'attachment'::text]))),
    CONSTRAINT ck_inbound_messages_reply CHECK (((reply_to_message_id IS NULL) OR (reply_to_message_id <> ''::text))),
    CONSTRAINT ck_inbound_messages_status CHECK ((status = ANY (ARRAY['pending'::text, 'done'::text])))
);

CREATE TABLE redline.messenger_message_refs (
    message_id text NOT NULL,
    conversation_message_id uuid NOT NULL,
    body text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_messenger_message_refs_identity CHECK (((message_id <> ''::text) AND (body <> ''::text)))
);

CREATE TABLE redline.notebook_credentials (
    token_hash text NOT NULL,
    psid text NOT NULL,
    purpose text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_notebook_credentials_hash CHECK ((token_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_notebook_credentials_psid CHECK ((psid <> ''::text)),
    CONSTRAINT ck_notebook_credentials_purpose CHECK ((purpose = ANY (ARRAY['link'::text, 'device'::text])))
);

CREATE TABLE redline.notebook_entries (
    id uuid NOT NULL,
    psid text NOT NULL,
    source_key text NOT NULL,
    slot text NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    period text DEFAULT ''::text NOT NULL,
    occurred_on date NOT NULL,
    body text DEFAULT ''::text NOT NULL,
    image bytea,
    image_type text DEFAULT 'image/png'::text NOT NULL,
    image_hash text DEFAULT ''::text NOT NULL,
    snapshot jsonb,
    origin text DEFAULT 'delivery'::text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_notebook_entries_identity CHECK (((psid <> ''::text) AND (source_key <> ''::text) AND (slot <> ''::text))),
    CONSTRAINT ck_notebook_entries_image CHECK (((image IS NULL) OR (octet_length(image) <= 12582912))),
    CONSTRAINT ck_notebook_entries_kind CHECK ((kind = ANY (ARRAY['weekly'::text, 'monthly'::text, 'strength'::text, 'other'::text]))),
    CONSTRAINT ck_notebook_entries_origin CHECK ((origin = ANY (ARRAY['delivery'::text, 'recovered'::text])))
);

CREATE TABLE redline.observation_metrics (
    observation_id uuid NOT NULL,
    recorded_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    distance_km numeric(7,3),
    distance_km_state text NOT NULL,
    duration_s integer,
    duration_s_state text NOT NULL,
    pace_s_per_km integer,
    pace_s_per_km_state text NOT NULL,
    hr_avg_bpm smallint,
    hr_avg_bpm_state text NOT NULL,
    hr_max_bpm smallint,
    hr_max_bpm_state text NOT NULL,
    cadence_spm smallint,
    cadence_spm_state text NOT NULL,
    elevation_gain_m integer,
    elevation_gain_m_state text NOT NULL,
    elevation_loss_m integer,
    elevation_loss_m_state text NOT NULL,
    wbgt_c numeric(4,1),
    wbgt_c_state text NOT NULL,
    rpe numeric(3,1),
    rpe_state text NOT NULL,
    enjoyment numeric(3,1),
    enjoyment_state text NOT NULL,
    carbs_g_h numeric(5,1),
    carbs_g_h_state text NOT NULL,
    gut_symptoms numeric(3,1),
    gut_symptoms_state text NOT NULL,
    reps_in_reserve smallint,
    reps_in_reserve_state text NOT NULL,
    hr_doubt text,
    CONSTRAINT ck_observation_metrics_cadence_spm_state CHECK ((cadence_spm_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_cadence_spm_value CHECK ((((cadence_spm_state = 'known'::text) AND (cadence_spm IS NOT NULL) AND ((cadence_spm >= 0) AND (cadence_spm <= 300))) OR ((cadence_spm_state <> 'known'::text) AND (cadence_spm IS NULL)))),
    CONSTRAINT ck_observation_metrics_carbs_g_h_state CHECK ((carbs_g_h_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_carbs_g_h_value CHECK ((((carbs_g_h_state = 'known'::text) AND (carbs_g_h IS NOT NULL) AND ((carbs_g_h >= (0)::numeric) AND (carbs_g_h <= (200)::numeric))) OR ((carbs_g_h_state <> 'known'::text) AND (carbs_g_h IS NULL)))),
    CONSTRAINT ck_observation_metrics_distance_km_state CHECK ((distance_km_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_distance_km_value CHECK ((((distance_km_state = 'known'::text) AND (distance_km IS NOT NULL) AND ((distance_km >= (0)::numeric) AND (distance_km <= (1000)::numeric))) OR ((distance_km_state <> 'known'::text) AND (distance_km IS NULL)))),
    CONSTRAINT ck_observation_metrics_duration_s_state CHECK ((duration_s_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_duration_s_value CHECK ((((duration_s_state = 'known'::text) AND (duration_s IS NOT NULL) AND ((duration_s >= 0) AND (duration_s <= 604800))) OR ((duration_s_state <> 'known'::text) AND (duration_s IS NULL)))),
    CONSTRAINT ck_observation_metrics_elevation_gain_m_state CHECK ((elevation_gain_m_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_elevation_gain_m_value CHECK ((((elevation_gain_m_state = 'known'::text) AND (elevation_gain_m IS NOT NULL) AND ((elevation_gain_m >= 0) AND (elevation_gain_m <= 20000))) OR ((elevation_gain_m_state <> 'known'::text) AND (elevation_gain_m IS NULL)))),
    CONSTRAINT ck_observation_metrics_elevation_loss_m_state CHECK ((elevation_loss_m_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_elevation_loss_m_value CHECK ((((elevation_loss_m_state = 'known'::text) AND (elevation_loss_m IS NOT NULL) AND ((elevation_loss_m >= 0) AND (elevation_loss_m <= 20000))) OR ((elevation_loss_m_state <> 'known'::text) AND (elevation_loss_m IS NULL)))),
    CONSTRAINT ck_observation_metrics_enjoyment_state CHECK ((enjoyment_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_enjoyment_value CHECK ((((enjoyment_state = 'known'::text) AND (enjoyment IS NOT NULL) AND ((enjoyment >= (0)::numeric) AND (enjoyment <= (10)::numeric))) OR ((enjoyment_state <> 'known'::text) AND (enjoyment IS NULL)))),
    CONSTRAINT ck_observation_metrics_gut_symptoms_state CHECK ((gut_symptoms_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_gut_symptoms_value CHECK ((((gut_symptoms_state = 'known'::text) AND (gut_symptoms IS NOT NULL) AND ((gut_symptoms >= (0)::numeric) AND (gut_symptoms <= (10)::numeric))) OR ((gut_symptoms_state <> 'known'::text) AND (gut_symptoms IS NULL)))),
    CONSTRAINT ck_observation_metrics_hr_avg_bpm_state CHECK ((hr_avg_bpm_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_hr_avg_bpm_value CHECK ((((hr_avg_bpm_state = 'known'::text) AND (hr_avg_bpm IS NOT NULL) AND ((hr_avg_bpm >= 20) AND (hr_avg_bpm <= 250))) OR ((hr_avg_bpm_state <> 'known'::text) AND (hr_avg_bpm IS NULL)))),
    CONSTRAINT ck_observation_metrics_hr_doubt CHECK (((hr_doubt IS NULL) OR ((hr_doubt = ANY (ARRAY['no_sensor'::text, 'context'::text])) AND (hr_avg_bpm_state = 'known'::text)))),
    CONSTRAINT ck_observation_metrics_hr_max_bpm_state CHECK ((hr_max_bpm_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_hr_max_bpm_value CHECK ((((hr_max_bpm_state = 'known'::text) AND (hr_max_bpm IS NOT NULL) AND ((hr_max_bpm >= 20) AND (hr_max_bpm <= 250))) OR ((hr_max_bpm_state <> 'known'::text) AND (hr_max_bpm IS NULL)))),
    CONSTRAINT ck_observation_metrics_hr_order CHECK (((hr_avg_bpm_state <> 'known'::text) OR (hr_max_bpm_state <> 'known'::text) OR (hr_avg_bpm <= hr_max_bpm))),
    CONSTRAINT ck_observation_metrics_pace_s_per_km_state CHECK ((pace_s_per_km_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_pace_s_per_km_value CHECK ((((pace_s_per_km_state = 'known'::text) AND (pace_s_per_km IS NOT NULL) AND ((pace_s_per_km >= 60) AND (pace_s_per_km <= 3600))) OR ((pace_s_per_km_state <> 'known'::text) AND (pace_s_per_km IS NULL)))),
    CONSTRAINT ck_observation_metrics_reps_in_reserve_state CHECK ((reps_in_reserve_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_reps_in_reserve_value CHECK ((((reps_in_reserve_state = 'known'::text) AND (reps_in_reserve IS NOT NULL) AND ((reps_in_reserve >= 0) AND (reps_in_reserve <= 5))) OR ((reps_in_reserve_state <> 'known'::text) AND (reps_in_reserve IS NULL)))),
    CONSTRAINT ck_observation_metrics_rpe_state CHECK ((rpe_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_rpe_value CHECK ((((rpe_state = 'known'::text) AND (rpe IS NOT NULL) AND ((rpe >= (0)::numeric) AND (rpe <= (10)::numeric))) OR ((rpe_state <> 'known'::text) AND (rpe IS NULL)))),
    CONSTRAINT ck_observation_metrics_wbgt_c_state CHECK ((wbgt_c_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_observation_metrics_wbgt_c_value CHECK ((((wbgt_c_state = 'known'::text) AND (wbgt_c IS NOT NULL) AND ((wbgt_c >= ('-50'::integer)::numeric) AND (wbgt_c <= (60)::numeric))) OR ((wbgt_c_state <> 'known'::text) AND (wbgt_c IS NULL))))
);

CREATE TABLE redline.observations (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    occurred_on date NOT NULL,
    started_at timestamp with time zone,
    kind text NOT NULL,
    idempotency_key text NOT NULL,
    strava_activity_id bigint,
    gear_id text,
    note text DEFAULT ''::text NOT NULL,
    source text DEFAULT 'application'::text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    supersedes_id uuid,
    routine text,
    CONSTRAINT ck_observations_idempotency_key CHECK ((idempotency_key <> ''::text)),
    CONSTRAINT ck_observations_kind CHECK ((kind = ANY (ARRAY['easy'::text, 'long'::text, 'threshold'::text, 'hills'::text, 'strides'::text, 'cadence'::text, 'race'::text, 'hike'::text, 'bike'::text, 'strength'::text, 'wake-trace'::text, 'rest-reading'::text, 'instruments'::text, 'palpation'::text]))),
    CONSTRAINT ck_observations_not_self_superseding CHECK (((supersedes_id IS NULL) OR (supersedes_id <> id))),
    CONSTRAINT ck_observations_routine CHECK (((routine IS NULL) OR ((kind = 'strength'::text) AND (routine ~ '^[a-z0-9][a-z0-9-]{0,39}$'::text)))),
    CONSTRAINT ck_observations_strava_positive CHECK (((strava_activity_id IS NULL) OR (strava_activity_id > 0)))
);

CREATE TABLE redline.outbound_deliveries (
    source_key text NOT NULL,
    psid text NOT NULL,
    payload_digest text NOT NULL,
    payload jsonb,
    status text NOT NULL,
    attempts integer DEFAULT 1 NOT NULL,
    last_error text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_outbound_deliveries_attempts CHECK ((attempts >= 1)),
    CONSTRAINT ck_outbound_deliveries_digest CHECK ((payload_digest ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_outbound_deliveries_identity CHECK (((source_key <> ''::text) AND (psid <> ''::text))),
    CONSTRAINT ck_outbound_deliveries_payload CHECK (((payload IS NULL) OR (jsonb_typeof(payload) = 'object'::text))),
    CONSTRAINT ck_outbound_deliveries_status CHECK ((status = ANY (ARRAY['prepared'::text, 'sending'::text, 'delivered'::text, 'failed'::text])))
);

CREATE TABLE redline.pain_readings (
    observation_id uuid NOT NULL,
    zone text NOT NULL,
    athlete_id text NOT NULL,
    state text NOT NULL,
    value numeric(3,1),
    recorded_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_pain_readings_state CHECK ((state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'not_applicable'::text]))),
    CONSTRAINT ck_pain_readings_value CHECK ((((state = 'known'::text) AND (value IS NOT NULL) AND ((value >= (0)::numeric) AND (value <= (10)::numeric))) OR ((state <> 'known'::text) AND (value IS NULL))))
);

CREATE TABLE redline.pain_zones (
    athlete_id text NOT NULL,
    slug text NOT NULL,
    label text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_pain_zones_label CHECK (((label <> ''::text) AND (char_length(label) <= 40))),
    CONSTRAINT ck_pain_zones_slug CHECK ((slug ~ '^[a-z0-9][a-z0-9-]{0,39}$'::text))
);

CREATE TABLE redline.patience_notifications (
    singleton boolean DEFAULT true NOT NULL,
    last_sent_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_patience_notifications_singleton CHECK (singleton)
);

CREATE TABLE redline.permit_choices (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    domain text DEFAULT 'easy'::text NOT NULL,
    iso_week text NOT NULL,
    session_key text NOT NULL,
    scheduled_on date NOT NULL,
    coach_minutes smallint NOT NULL,
    prediction_key text NOT NULL,
    sealed_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_permit_choices_domain CHECK ((domain = 'easy'::text)),
    CONSTRAINT ck_permit_choices_key CHECK ((session_key ~ '^[a-z0-9][a-z0-9-]*$'::text)),
    CONSTRAINT ck_permit_choices_minutes CHECK (((coach_minutes >= 10) AND (coach_minutes <= 180))),
    CONSTRAINT ck_permit_choices_prediction CHECK ((prediction_key <> ''::text)),
    CONSTRAINT ck_permit_choices_week CHECK ((iso_week ~ '^20[0-9]{2}-W[0-9]{2}$'::text))
);

CREATE TABLE redline.plan_gates (
    id uuid NOT NULL,
    revision_id uuid NOT NULL,
    logical_key text NOT NULL,
    due_on date NOT NULL,
    question text NOT NULL,
    on_fail text NOT NULL,
    applies_when redline.gate_when NOT NULL,
    holds jsonb,
    CONSTRAINT ck_plan_gates_holds CHECK ((((applies_when = 'before'::redline.gate_when) AND (jsonb_typeof(holds) = 'array'::text) AND (jsonb_array_length(holds) > 0)) OR ((applies_when = 'after'::redline.gate_when) AND (holds IS NULL)))),
    CONSTRAINT ck_plan_gates_logical_key CHECK ((logical_key ~ '^[a-z0-9][a-z0-9-]*$'::text)),
    CONSTRAINT ck_plan_gates_on_fail CHECK ((length(on_fail) >= 3)),
    CONSTRAINT ck_plan_gates_question CHECK ((length(question) >= 10))
);

CREATE TABLE redline.plan_revisions (
    id uuid NOT NULL,
    plan_id uuid NOT NULL,
    revision integer NOT NULL,
    computed_at timestamp with time zone NOT NULL,
    persisted_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    reason text NOT NULL,
    evidence jsonb DEFAULT '[]'::jsonb NOT NULL,
    constraints jsonb DEFAULT '[]'::jsonb NOT NULL,
    changes jsonb DEFAULT '[]'::jsonb NOT NULL,
    calibration_target smallint,
    volume_target_km numeric(5,1),
    CONSTRAINT ck_plan_revisions_calibration_target CHECK (((calibration_target IS NULL) OR (calibration_target = ANY (ARRAY[0, 2, 3])))),
    CONSTRAINT ck_plan_revisions_number CHECK ((revision >= 1)),
    CONSTRAINT ck_plan_revisions_volume_target CHECK (((volume_target_km IS NULL) OR ((volume_target_km >= (1)::numeric) AND (volume_target_km <= (200)::numeric))))
);

CREATE TABLE redline.planned_session_sources (
    session_id uuid NOT NULL,
    "position" smallint NOT NULL,
    source text NOT NULL,
    CONSTRAINT ck_planned_session_sources_position CHECK (("position" >= 1)),
    CONSTRAINT ck_planned_session_sources_source CHECK ((source <> ''::text))
);

CREATE TABLE redline.planned_sessions (
    id uuid NOT NULL,
    revision_id uuid NOT NULL,
    logical_key text NOT NULL,
    title text NOT NULL,
    scheduled_on date NOT NULL,
    start_time time without time zone,
    kind text NOT NULL,
    workout text NOT NULL,
    status text NOT NULL,
    distance_target_km numeric(7,2),
    distance_cap_km numeric(7,2),
    duration_cap_min integer,
    rpe_min smallint,
    rpe_max smallint,
    hr_zone text,
    hr_cap_bpm smallint,
    cadence_target smallint,
    demanding boolean NOT NULL,
    transition text,
    calibration boolean DEFAULT false NOT NULL,
    location_label text,
    carbs_target_g_h smallint,
    strength_spec jsonb,
    note text,
    routine text,
    CONSTRAINT ck_planned_sessions_cadence_target CHECK (((cadence_target IS NULL) OR ((cadence_target >= 120) AND (cadence_target <= 220)))),
    CONSTRAINT ck_planned_sessions_carbs_target CHECK (((carbs_target_g_h IS NULL) OR ((carbs_target_g_h >= 0) AND (carbs_target_g_h <= 150)))),
    CONSTRAINT ck_planned_sessions_distance_cap CHECK (((distance_cap_km IS NULL) OR (distance_cap_km >= (0)::numeric))),
    CONSTRAINT ck_planned_sessions_distance_order CHECK (((distance_target_km IS NULL) OR (distance_cap_km IS NULL) OR (distance_target_km <= distance_cap_km))),
    CONSTRAINT ck_planned_sessions_distance_target CHECK (((distance_target_km IS NULL) OR (distance_target_km >= (0)::numeric))),
    CONSTRAINT ck_planned_sessions_duration_cap CHECK (((duration_cap_min IS NULL) OR (duration_cap_min >= 1))),
    CONSTRAINT ck_planned_sessions_hr_cap CHECK (((hr_cap_bpm IS NULL) OR ((hr_cap_bpm >= 80) AND (hr_cap_bpm <= 220)))),
    CONSTRAINT ck_planned_sessions_kind CHECK ((kind = ANY (ARRAY['run'::text, 'hike'::text, 'bike'::text, 'strength'::text, 'test'::text, 'rest'::text]))),
    CONSTRAINT ck_planned_sessions_logical_key CHECK ((logical_key ~ '^[a-z0-9][a-z0-9-]*$'::text)),
    CONSTRAINT ck_planned_sessions_routine CHECK (((workout = 'strength'::text) = ((routine IS NOT NULL) AND (routine ~ '^[a-z0-9][a-z0-9-]{0,39}$'::text)))),
    CONSTRAINT ck_planned_sessions_rpe_max CHECK (((rpe_max IS NULL) OR ((rpe_max >= 0) AND (rpe_max <= 10)))),
    CONSTRAINT ck_planned_sessions_rpe_min CHECK (((rpe_min IS NULL) OR ((rpe_min >= 0) AND (rpe_min <= 10)))),
    CONSTRAINT ck_planned_sessions_rpe_order CHECK (((rpe_min IS NULL) OR (rpe_max IS NULL) OR (rpe_min <= rpe_max))),
    CONSTRAINT ck_planned_sessions_status CHECK ((status = ANY (ARRAY['planned'::text, 'cancelled'::text]))),
    CONSTRAINT ck_planned_sessions_strength_owner CHECK (((workout = 'strength'::text) = (strength_spec IS NOT NULL))),
    CONSTRAINT ck_planned_sessions_strength_spec CHECK (((strength_spec IS NULL) OR (jsonb_typeof(strength_spec) = 'object'::text))),
    CONSTRAINT ck_planned_sessions_transition CHECK (((transition IS NULL) OR (transition ~ '^[a-z0-9][a-z0-9-]{0,39}$'::text))),
    CONSTRAINT ck_planned_sessions_workout CHECK ((workout = ANY (ARRAY['easy'::text, 'long'::text, 'threshold'::text, 'hills'::text, 'strides'::text, 'cadence'::text, 'race'::text, 'hike'::text, 'bike'::text, 'strength'::text, 'test'::text, 'rest'::text])))
);

CREATE TABLE redline.proactive_delivery_budgets (
    budget_on date NOT NULL,
    sent_count integer NOT NULL,
    last_sent_at timestamp with time zone,
    CONSTRAINT ck_proactive_delivery_budgets_count CHECK ((sent_count >= 0))
);

CREATE TABLE redline.proactive_jobs (
    job_key text NOT NULL,
    kind text NOT NULL,
    detail text DEFAULT ''::text NOT NULL,
    due_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone,
    priority boolean DEFAULT false NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    completed_at timestamp with time zone,
    last_error text DEFAULT ''::text NOT NULL,
    CONSTRAINT ck_proactive_jobs_attempts CHECK ((attempts >= 0)),
    CONSTRAINT ck_proactive_jobs_expiry CHECK (((expires_at IS NULL) OR (expires_at >= due_at))),
    CONSTRAINT ck_proactive_jobs_kind CHECK ((kind = ANY (ARRAY['strava_debrief'::text, 'silence'::text, 'morning'::text, 'weekly'::text, 'weekly_repair'::text, 'monthly'::text, 'session_followup'::text, 'missed_session'::text, 'daily_signal'::text, 'premortem'::text, 'correction'::text, 'decision_challenge'::text, 'gut_review'::text, 'gear_alert'::text, 'race_countdown'::text, 'strength_card'::text, 'calibration_eve'::text, 'operator_message'::text, 'strava_debrief_late'::text, 'ledger_incomplete'::text, 'wake_trace'::text]))),
    CONSTRAINT ck_proactive_jobs_last_error CHECK ((length(last_error) <= 500)),
    CONSTRAINT ck_proactive_jobs_status CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'done'::text, 'failed'::text])))
);

CREATE TABLE redline.proactive_markers (
    marker_key text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_proactive_markers_key CHECK ((marker_key <> ''::text))
);

CREATE TABLE redline.proactive_messages (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    source_key text NOT NULL,
    sent_at timestamp with time zone NOT NULL,
    kind text NOT NULL,
    angle text,
    summary text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_proactive_messages_kind CHECK ((kind = ANY (ARRAY['decision'::text, 'morning'::text, 'debrief'::text, 'weekly'::text, 'monthly'::text, 'correction'::text, 'other'::text]))),
    CONSTRAINT ck_proactive_messages_source_key CHECK ((source_key <> ''::text)),
    CONSTRAINT ck_proactive_messages_summary CHECK ((summary <> ''::text))
);

CREATE TABLE redline.projection_states (
    singleton boolean DEFAULT true NOT NULL,
    dirty_since timestamp with time zone,
    last_projected_at timestamp with time zone,
    CONSTRAINT ck_projection_states_singleton CHECK (singleton)
);

CREATE TABLE redline.session_cycles (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    plan_id uuid NOT NULL,
    logical_key text NOT NULL,
    source_session_id uuid NOT NULL,
    observation_id uuid,
    state text NOT NULL,
    planned_for timestamp with time zone,
    plan_digest text NOT NULL,
    started_at timestamp with time zone,
    declared_started_at timestamp with time zone,
    observed_started_at timestamp with time zone,
    completed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_session_cycles_logical_key CHECK ((logical_key ~ '^[a-z0-9][a-z0-9-]*$'::text)),
    CONSTRAINT ck_session_cycles_plan_digest CHECK ((plan_digest ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_session_cycles_state CHECK ((state = ANY (ARRAY['planned'::text, 'started'::text, 'completed'::text, 'missed'::text, 'cancelled'::text, 'followup_due'::text, 'closed'::text])))
);

CREATE TABLE redline.session_exposures (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    observation_id uuid NOT NULL,
    exposure_key text NOT NULL,
    exposure_type text NOT NULL,
    origin text NOT NULL,
    status text NOT NULL,
    duration_min smallint,
    target_value numeric(8,2),
    target_unit text,
    tolerance text DEFAULT 'unknown'::text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_session_exposures_duration CHECK (((duration_min IS NULL) OR ((duration_min >= 1) AND (duration_min <= 1440)))),
    CONSTRAINT ck_session_exposures_key CHECK ((exposure_key <> ''::text)),
    CONSTRAINT ck_session_exposures_origin CHECK ((origin = ANY (ARRAY['prescribed'::text, 'voluntary'::text, 'spontaneous'::text]))),
    CONSTRAINT ck_session_exposures_status CHECK ((status = ANY (ARRAY['candidate'::text, 'confirmed'::text, 'dismissed'::text]))),
    CONSTRAINT ck_session_exposures_target_pair CHECK (((target_value IS NULL) = (target_unit IS NULL))),
    CONSTRAINT ck_session_exposures_target_value CHECK (((target_value IS NULL) OR (target_value >= (0)::numeric))),
    CONSTRAINT ck_session_exposures_tolerance CHECK ((tolerance = ANY (ARRAY['unknown'::text, 'easy'::text, 'strained'::text, 'protective'::text]))),
    CONSTRAINT ck_session_exposures_type CHECK ((exposure_type = ANY (ARRAY['cadence'::text, 'descent'::text, 'strides'::text, 'nutrition'::text, 'gear'::text, 'other'::text])))
);

CREATE TABLE redline.session_followups (
    id uuid NOT NULL,
    cycle_id uuid NOT NULL,
    observation_id uuid,
    outcome text DEFAULT 'pending'::text NOT NULL,
    recorded_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    resolved_at timestamp with time zone,
    CONSTRAINT ck_session_followups_outcome CHECK ((outcome = ANY (ARRAY['pending'::text, 'answered'::text, 'unanswered'::text]))),
    CONSTRAINT ck_session_followups_resolution CHECK ((((outcome = 'answered'::text) AND (observation_id IS NOT NULL) AND (resolved_at IS NOT NULL)) OR ((outcome = 'unanswered'::text) AND (observation_id IS NULL) AND (resolved_at IS NOT NULL)) OR ((outcome = 'pending'::text) AND (observation_id IS NULL) AND (resolved_at IS NULL))))
);

CREATE TABLE redline.session_predictions (
    id uuid NOT NULL,
    cycle_id uuid NOT NULL,
    predicted_at timestamp with time zone NOT NULL,
    predicted_pain numeric(3,1) NOT NULL,
    predicted_morning_trace text NOT NULL,
    predicted_hr smallint NOT NULL,
    validity text NOT NULL,
    source text DEFAULT 'messenger'::text NOT NULL,
    CONSTRAINT ck_session_predictions_hr CHECK (((predicted_hr >= 20) AND (predicted_hr <= 250))),
    CONSTRAINT ck_session_predictions_morning_trace CHECK ((predicted_morning_trace = ANY (ARRAY['none'::text, 'light'::text, 'clear'::text]))),
    CONSTRAINT ck_session_predictions_pain CHECK (((predicted_pain >= (0)::numeric) AND (predicted_pain <= (10)::numeric))),
    CONSTRAINT ck_session_predictions_validity CHECK ((validity = ANY (ARRAY['pending'::text, 'valid'::text, 'late'::text])))
);

CREATE TABLE redline.strava_activity_intakes (
    activity_id bigint NOT NULL,
    athlete_id text NOT NULL,
    started_at timestamp with time zone,
    sport text NOT NULL,
    distilled jsonb NOT NULL,
    status text NOT NULL,
    candidate_session_keys jsonb DEFAULT '[]'::jsonb NOT NULL,
    observation_id uuid,
    error text DEFAULT ''::text NOT NULL,
    elevation_loss_state text DEFAULT 'pending'::text NOT NULL,
    elevation_loss_value integer,
    elevation_loss_attempts integer DEFAULT 0 NOT NULL,
    elevation_loss_error text DEFAULT ''::text NOT NULL,
    wbgt_state text DEFAULT 'pending'::text NOT NULL,
    wbgt_value numeric(4,1),
    wbgt_attempts integer DEFAULT 0 NOT NULL,
    wbgt_error text DEFAULT ''::text NOT NULL,
    streams jsonb,
    weather jsonb,
    fetched_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_strava_activity_intakes_elevation_loss_attempts CHECK ((elevation_loss_attempts >= 0)),
    CONSTRAINT ck_strava_activity_intakes_elevation_loss_state CHECK ((elevation_loss_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'failed'::text]))),
    CONSTRAINT ck_strava_activity_intakes_elevation_loss_value CHECK ((((elevation_loss_state = 'known'::text) AND (elevation_loss_value IS NOT NULL)) OR ((elevation_loss_state <> 'known'::text) AND (elevation_loss_value IS NULL)))),
    CONSTRAINT ck_strava_activity_intakes_id CHECK ((activity_id > 0)),
    CONSTRAINT ck_strava_activity_intakes_sport CHECK ((sport <> ''::text)),
    CONSTRAINT ck_strava_activity_intakes_status CHECK ((status = ANY (ARRAY['captured'::text, 'matched'::text, 'ambiguous'::text, 'unmatched'::text, 'failed'::text]))),
    CONSTRAINT ck_strava_activity_intakes_wbgt_attempts CHECK ((wbgt_attempts >= 0)),
    CONSTRAINT ck_strava_activity_intakes_wbgt_state CHECK ((wbgt_state = ANY (ARRAY['pending'::text, 'known'::text, 'unavailable'::text, 'failed'::text]))),
    CONSTRAINT ck_strava_activity_intakes_wbgt_value CHECK ((((wbgt_state = 'known'::text) AND (wbgt_value IS NOT NULL)) OR ((wbgt_state <> 'known'::text) AND (wbgt_value IS NULL))))
);

CREATE SEQUENCE redline.strava_activity_intakes_activity_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE redline.strava_activity_intakes_activity_id_seq OWNED BY redline.strava_activity_intakes.activity_id;

CREATE TABLE redline.strength_pain_readings (
    strength_session_id uuid NOT NULL,
    zone text NOT NULL,
    moment text NOT NULL,
    athlete_id text NOT NULL,
    value smallint NOT NULL,
    CONSTRAINT ck_strength_pain_readings_moment CHECK ((moment = ANY (ARRAY['before'::text, 'after'::text]))),
    CONSTRAINT ck_strength_pain_readings_value CHECK (((value >= 0) AND (value <= 10)))
);

CREATE TABLE redline.strength_report_claims (
    session_key text NOT NULL,
    claimed_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE TABLE redline.strength_report_daily_quotas (
    quota_on date NOT NULL,
    accepted_count integer NOT NULL,
    CONSTRAINT ck_strength_report_daily_quotas_count CHECK ((accepted_count >= 0))
);

CREATE TABLE redline.strength_results (
    id uuid NOT NULL,
    session_id uuid NOT NULL,
    "position" smallint NOT NULL,
    exercise_slug text NOT NULL,
    difficulty smallint,
    skipped boolean DEFAULT false NOT NULL,
    status text,
    completed_slots smallint,
    note text DEFAULT ''::text NOT NULL,
    CONSTRAINT ck_strength_results_difficulty CHECK (((difficulty IS NULL) OR ((difficulty >= 0) AND (difficulty <= 10)))),
    CONSTRAINT ck_strength_results_note CHECK ((char_length(note) <= 300)),
    CONSTRAINT ck_strength_results_position CHECK (("position" >= 1)),
    CONSTRAINT ck_strength_results_skipped CHECK (((NOT skipped) OR (difficulty IS NULL))),
    CONSTRAINT ck_strength_results_slots CHECK (((completed_slots IS NULL) OR ((completed_slots >= 0) AND (completed_slots <= 24)))),
    CONSTRAINT ck_strength_results_status CHECK (((status IS NULL) OR (status = ANY (ARRAY['completed'::text, 'partial'::text, 'skipped'::text, 'gated'::text, 'unknown'::text]))))
);

CREATE TABLE redline.strength_session_links (
    token_hash text NOT NULL,
    psid text NOT NULL,
    session_key text NOT NULL,
    snapshot jsonb NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_strength_session_links_expiry CHECK ((expires_at > created_at)),
    CONSTRAINT ck_strength_session_links_psid CHECK ((psid <> ''::text)),
    CONSTRAINT ck_strength_session_links_revocation CHECK (((revoked_at IS NULL) OR (revoked_at >= created_at))),
    CONSTRAINT ck_strength_session_links_session_key CHECK (((session_key <> ''::text) AND (char_length(session_key) <= 64))),
    CONSTRAINT ck_strength_session_links_snapshot CHECK ((jsonb_typeof(snapshot) = 'object'::text)),
    CONSTRAINT ck_strength_session_links_token_hash CHECK ((token_hash ~ '^[0-9a-f]{64}$'::text))
);

CREATE TABLE redline.strength_sessions (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    session_key text NOT NULL,
    performed_on date NOT NULL,
    workout text,
    duration_minutes smallint,
    note text DEFAULT ''::text NOT NULL,
    prescription jsonb,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_strength_sessions_duration CHECK (((duration_minutes IS NULL) OR ((duration_minutes >= 0) AND (duration_minutes <= 300)))),
    CONSTRAINT ck_strength_sessions_key CHECK (((session_key <> ''::text) AND (char_length(session_key) <= 64))),
    CONSTRAINT ck_strength_sessions_note CHECK ((char_length(note) <= 300)),
    CONSTRAINT ck_strength_sessions_workout CHECK (((workout IS NULL) OR (workout ~ '^[a-z0-9][a-z0-9-]{0,39}$'::text)))
);

CREATE TABLE redline.strength_sets (
    result_id uuid NOT NULL,
    side text NOT NULL,
    set_index smallint NOT NULL,
    repetitions smallint NOT NULL,
    CONSTRAINT ck_strength_sets_index CHECK ((set_index >= 1)),
    CONSTRAINT ck_strength_sets_repetitions CHECK (((repetitions >= 0) AND (repetitions <= 999))),
    CONSTRAINT ck_strength_sets_side CHECK ((side = ANY (ARRAY['both'::text, 'right'::text, 'left'::text])))
);

CREATE TABLE redline.turn_commands (
    id uuid NOT NULL,
    turn_id uuid NOT NULL,
    command_key text NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    applied_at timestamp with time zone,
    CONSTRAINT ck_turn_commands_command_key CHECK ((command_key <> ''::text)),
    CONSTRAINT ck_turn_commands_kind CHECK (((kind = ANY (ARRAY['observation.add'::text, 'activity.link'::text, 'measurement.complete'::text, 'measurement.hr_doubt'::text, 'exposure.upsert'::text, 'plan.revise'::text, 'daily.upsert'::text, 'capability.evaluate'::text, 'thread.upsert'::text, 'question.review'::text, 'question.ask'::text, 'deadline.upsert'::text, 'session.start'::text, 'session.match'::text, 'proactive.angle'::text, 'gear.upsert'::text, 'prediction.upsert'::text, 'correction.register'::text, 'permit.seal'::text])) OR ((kind = 'proactive_message.add'::text) AND (applied_at IS NOT NULL))))
);

CREATE TABLE redline.turn_scopes (
    turn_id uuid NOT NULL,
    psid text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_turn_scopes_psid CHECK ((psid <> ''::text))
);

CREATE TABLE redline.weekly_instruments (
    athlete_id text NOT NULL,
    iso_week text NOT NULL,
    reviewed_on date NOT NULL,
    cmj_cm numeric(7,2),
    calf_right_straight smallint,
    calf_left_straight smallint,
    calf_right_bent smallint,
    calf_left_bent smallint,
    enjoyment numeric(4,1),
    canary_pain numeric(4,1),
    pain_calibration_gap numeric(5,2),
    hr_calibration_gap numeric(7,2),
    sleep text,
    life_load text,
    note text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT ck_weekly_instruments_calf_left_bent CHECK (((calf_left_bent IS NULL) OR ((calf_left_bent >= 0) AND (calf_left_bent <= 999)))),
    CONSTRAINT ck_weekly_instruments_calf_left_straight CHECK (((calf_left_straight IS NULL) OR ((calf_left_straight >= 0) AND (calf_left_straight <= 999)))),
    CONSTRAINT ck_weekly_instruments_calf_right_bent CHECK (((calf_right_bent IS NULL) OR ((calf_right_bent >= 0) AND (calf_right_bent <= 999)))),
    CONSTRAINT ck_weekly_instruments_calf_right_straight CHECK (((calf_right_straight IS NULL) OR ((calf_right_straight >= 0) AND (calf_right_straight <= 999)))),
    CONSTRAINT ck_weekly_instruments_canary_pain CHECK (((canary_pain IS NULL) OR ((canary_pain >= (0)::numeric) AND (canary_pain <= (10)::numeric)))),
    CONSTRAINT ck_weekly_instruments_cmj CHECK (((cmj_cm IS NULL) OR ((cmj_cm >= (0)::numeric) AND (cmj_cm <= (100)::numeric)))),
    CONSTRAINT ck_weekly_instruments_enjoyment CHECK (((enjoyment IS NULL) OR ((enjoyment >= (0)::numeric) AND (enjoyment <= (10)::numeric)))),
    CONSTRAINT ck_weekly_instruments_life_load CHECK (((life_load IS NULL) OR (life_load = ANY (ARRAY['normal'::text, 'high'::text])))),
    CONSTRAINT ck_weekly_instruments_sleep CHECK (((sleep IS NULL) OR (sleep = ANY (ARRAY['ok'::text, 'bad_nights'::text, 'bad_week'::text])))),
    CONSTRAINT ck_weekly_instruments_week CHECK ((iso_week ~ '^20[0-9]{2}-W(0[1-9]|[1-4][0-9]|5[0-3])$'::text))
);

CREATE TABLE redline.weekly_plans (
    id uuid NOT NULL,
    athlete_id text NOT NULL,
    iso_week text NOT NULL,
    CONSTRAINT ck_weekly_plans_week CHECK ((iso_week ~ '^20[0-9]{2}-W(0[1-9]|[1-4][0-9]|5[0-3])$'::text))
);

ALTER TABLE ONLY redline.strava_activity_intakes ALTER COLUMN activity_id SET DEFAULT nextval('redline.strava_activity_intakes_activity_id_seq'::regclass);

ALTER TABLE ONLY redline.agent_sessions
    ADD CONSTRAINT pk_agent_sessions PRIMARY KEY (psid);

ALTER TABLE ONLY redline.athlete_activity_states
    ADD CONSTRAINT pk_athlete_activity_states PRIMARY KEY (athlete_id);

ALTER TABLE ONLY redline.athletes
    ADD CONSTRAINT pk_athletes PRIMARY KEY (id);

ALTER TABLE ONLY redline.audit_events
    ADD CONSTRAINT pk_audit_events PRIMARY KEY (id);

ALTER TABLE ONLY redline.capability_evaluations
    ADD CONSTRAINT pk_capability_evaluations PRIMARY KEY (id);

ALTER TABLE ONLY redline.coach_predictions
    ADD CONSTRAINT pk_coach_predictions PRIMARY KEY (id);

ALTER TABLE ONLY redline.conversation_messages
    ADD CONSTRAINT pk_conversation_messages PRIMARY KEY (id);

ALTER TABLE ONLY redline.conversation_questions
    ADD CONSTRAINT pk_conversation_questions PRIMARY KEY (id);

ALTER TABLE ONLY redline.conversation_threads
    ADD CONSTRAINT pk_conversation_threads PRIMARY KEY (id);

ALTER TABLE ONLY redline.correction_deliveries
    ADD CONSTRAINT pk_correction_deliveries PRIMARY KEY (id);

ALTER TABLE ONLY redline.daily_checkins
    ADD CONSTRAINT pk_daily_checkins PRIMARY KEY (athlete_id, checked_on);

ALTER TABLE ONLY redline.deadlines
    ADD CONSTRAINT pk_deadlines PRIMARY KEY (id);

ALTER TABLE ONLY redline.delivery_debts
    ADD CONSTRAINT pk_delivery_debts PRIMARY KEY (psid);

ALTER TABLE ONLY redline.gear_items
    ADD CONSTRAINT pk_gear_items PRIMARY KEY (gear_id);

ALTER TABLE ONLY redline.handoffs
    ADD CONSTRAINT pk_handoffs PRIMARY KEY (psid);

ALTER TABLE ONLY redline.inbound_activity_states
    ADD CONSTRAINT pk_inbound_activity_states PRIMARY KEY (psid);

ALTER TABLE ONLY redline.inbound_messages
    ADD CONSTRAINT pk_inbound_messages PRIMARY KEY (message_id);

ALTER TABLE ONLY redline.messenger_message_refs
    ADD CONSTRAINT pk_messenger_message_refs PRIMARY KEY (message_id);

ALTER TABLE ONLY redline.notebook_credentials
    ADD CONSTRAINT pk_notebook_credentials PRIMARY KEY (token_hash);

ALTER TABLE ONLY redline.notebook_entries
    ADD CONSTRAINT pk_notebook_entries PRIMARY KEY (id);

ALTER TABLE ONLY redline.observation_metrics
    ADD CONSTRAINT pk_observation_metrics PRIMARY KEY (observation_id);

ALTER TABLE ONLY redline.observations
    ADD CONSTRAINT pk_observations PRIMARY KEY (id);

ALTER TABLE ONLY redline.outbound_deliveries
    ADD CONSTRAINT pk_outbound_deliveries PRIMARY KEY (source_key);

ALTER TABLE ONLY redline.pain_readings
    ADD CONSTRAINT pk_pain_readings PRIMARY KEY (observation_id, zone);

ALTER TABLE ONLY redline.pain_zones
    ADD CONSTRAINT pk_pain_zones PRIMARY KEY (athlete_id, slug);

ALTER TABLE ONLY redline.patience_notifications
    ADD CONSTRAINT pk_patience_notifications PRIMARY KEY (singleton);

ALTER TABLE ONLY redline.permit_choices
    ADD CONSTRAINT pk_permit_choices PRIMARY KEY (id);

ALTER TABLE ONLY redline.plan_gates
    ADD CONSTRAINT pk_plan_gates PRIMARY KEY (id);

ALTER TABLE ONLY redline.plan_revisions
    ADD CONSTRAINT pk_plan_revisions PRIMARY KEY (id);

ALTER TABLE ONLY redline.planned_session_sources
    ADD CONSTRAINT pk_planned_session_sources PRIMARY KEY (session_id, "position");

ALTER TABLE ONLY redline.planned_sessions
    ADD CONSTRAINT pk_planned_sessions PRIMARY KEY (id);

ALTER TABLE ONLY redline.proactive_delivery_budgets
    ADD CONSTRAINT pk_proactive_delivery_budgets PRIMARY KEY (budget_on);

ALTER TABLE ONLY redline.proactive_jobs
    ADD CONSTRAINT pk_proactive_jobs PRIMARY KEY (job_key);

ALTER TABLE ONLY redline.proactive_markers
    ADD CONSTRAINT pk_proactive_markers PRIMARY KEY (marker_key);

ALTER TABLE ONLY redline.proactive_messages
    ADD CONSTRAINT pk_proactive_messages PRIMARY KEY (id);

ALTER TABLE ONLY redline.projection_states
    ADD CONSTRAINT pk_projection_states PRIMARY KEY (singleton);

ALTER TABLE ONLY redline.session_cycles
    ADD CONSTRAINT pk_session_cycles PRIMARY KEY (id);

ALTER TABLE ONLY redline.session_exposures
    ADD CONSTRAINT pk_session_exposures PRIMARY KEY (id);

ALTER TABLE ONLY redline.session_followups
    ADD CONSTRAINT pk_session_followups PRIMARY KEY (id);

ALTER TABLE ONLY redline.session_predictions
    ADD CONSTRAINT pk_session_predictions PRIMARY KEY (id);

ALTER TABLE ONLY redline.strava_activity_intakes
    ADD CONSTRAINT pk_strava_activity_intakes PRIMARY KEY (activity_id);

ALTER TABLE ONLY redline.strength_pain_readings
    ADD CONSTRAINT pk_strength_pain_readings PRIMARY KEY (strength_session_id, zone, moment);

ALTER TABLE ONLY redline.strength_report_claims
    ADD CONSTRAINT pk_strength_report_claims PRIMARY KEY (session_key);

ALTER TABLE ONLY redline.strength_report_daily_quotas
    ADD CONSTRAINT pk_strength_report_daily_quotas PRIMARY KEY (quota_on);

ALTER TABLE ONLY redline.strength_results
    ADD CONSTRAINT pk_strength_results PRIMARY KEY (id);

ALTER TABLE ONLY redline.strength_session_links
    ADD CONSTRAINT pk_strength_session_links PRIMARY KEY (token_hash);

ALTER TABLE ONLY redline.strength_sessions
    ADD CONSTRAINT pk_strength_sessions PRIMARY KEY (id);

ALTER TABLE ONLY redline.strength_sets
    ADD CONSTRAINT pk_strength_sets PRIMARY KEY (result_id, side, set_index);

ALTER TABLE ONLY redline.turn_commands
    ADD CONSTRAINT pk_turn_commands PRIMARY KEY (id);

ALTER TABLE ONLY redline.turn_scopes
    ADD CONSTRAINT pk_turn_scopes PRIMARY KEY (turn_id);

ALTER TABLE ONLY redline.weekly_instruments
    ADD CONSTRAINT pk_weekly_instruments PRIMARY KEY (athlete_id, iso_week);

ALTER TABLE ONLY redline.weekly_plans
    ADD CONSTRAINT pk_weekly_plans PRIMARY KEY (id);

ALTER TABLE ONLY redline.capability_evaluations
    ADD CONSTRAINT uq_capability_evaluations_day UNIQUE (athlete_id, capability_number, evaluated_on);

ALTER TABLE ONLY redline.coach_predictions
    ADD CONSTRAINT uq_coach_predictions_key UNIQUE (athlete_id, prediction_key);

ALTER TABLE ONLY redline.conversation_messages
    ADD CONSTRAINT uq_conversation_messages_source UNIQUE (psid, source_key);

ALTER TABLE ONLY redline.conversation_threads
    ADD CONSTRAINT uq_conversation_threads_key UNIQUE (athlete_id, thread_key);

ALTER TABLE ONLY redline.correction_deliveries
    ADD CONSTRAINT uq_correction_deliveries_key UNIQUE (athlete_id, correction_key);

ALTER TABLE ONLY redline.deadlines
    ADD CONSTRAINT uq_deadlines_key UNIQUE (athlete_id, deadline_key);

ALTER TABLE ONLY redline.notebook_entries
    ADD CONSTRAINT uq_notebook_entries_source UNIQUE (psid, source_key, slot);

ALTER TABLE ONLY redline.observations
    ADD CONSTRAINT uq_observations_idempotency UNIQUE (athlete_id, idempotency_key);

ALTER TABLE ONLY redline.observations
    ADD CONSTRAINT uq_observations_strava UNIQUE (athlete_id, strava_activity_id);

ALTER TABLE ONLY redline.permit_choices
    ADD CONSTRAINT uq_permit_choices_session UNIQUE (athlete_id, iso_week, session_key);

ALTER TABLE ONLY redline.plan_gates
    ADD CONSTRAINT uq_plan_gates_key UNIQUE (revision_id, logical_key);

ALTER TABLE ONLY redline.plan_revisions
    ADD CONSTRAINT uq_plan_revisions_number UNIQUE (plan_id, revision);

ALTER TABLE ONLY redline.planned_sessions
    ADD CONSTRAINT uq_planned_sessions_key UNIQUE (revision_id, logical_key);

ALTER TABLE ONLY redline.proactive_messages
    ADD CONSTRAINT uq_proactive_messages_source_key UNIQUE (athlete_id, source_key);

ALTER TABLE ONLY redline.session_cycles
    ADD CONSTRAINT uq_session_cycles_observation_id UNIQUE (observation_id);

ALTER TABLE ONLY redline.session_cycles
    ADD CONSTRAINT uq_session_cycles_plan_key UNIQUE (plan_id, logical_key);

ALTER TABLE ONLY redline.session_exposures
    ADD CONSTRAINT uq_session_exposures_key UNIQUE (athlete_id, exposure_key);

ALTER TABLE ONLY redline.session_followups
    ADD CONSTRAINT uq_session_followups_cycle_id UNIQUE (cycle_id);

ALTER TABLE ONLY redline.session_followups
    ADD CONSTRAINT uq_session_followups_observation_id UNIQUE (observation_id);

ALTER TABLE ONLY redline.session_predictions
    ADD CONSTRAINT uq_session_predictions_cycle_id UNIQUE (cycle_id);

ALTER TABLE ONLY redline.strava_activity_intakes
    ADD CONSTRAINT uq_strava_activity_intakes_observation_id UNIQUE (observation_id);

ALTER TABLE ONLY redline.strength_results
    ADD CONSTRAINT uq_strength_results_exercise UNIQUE (session_id, exercise_slug);

ALTER TABLE ONLY redline.strength_results
    ADD CONSTRAINT uq_strength_results_position UNIQUE (session_id, "position");

ALTER TABLE ONLY redline.strength_sessions
    ADD CONSTRAINT uq_strength_sessions_key UNIQUE (athlete_id, session_key);

ALTER TABLE ONLY redline.turn_commands
    ADD CONSTRAINT uq_turn_commands_command_key UNIQUE (command_key);

ALTER TABLE ONLY redline.weekly_plans
    ADD CONSTRAINT uq_weekly_plans_week UNIQUE (athlete_id, iso_week);

CREATE INDEX ix_audit_events_created ON redline.audit_events USING btree (created_at);

CREATE INDEX ix_coach_predictions_due ON redline.coach_predictions USING btree (due_on) WHERE (status = 'open'::text);

CREATE INDEX ix_coach_predictions_resolves ON redline.coach_predictions USING btree (resolves_kind, resolves_on) WHERE ((status = 'open'::text) AND (resolves_kind IS NOT NULL));

CREATE INDEX ix_conversation_messages_fts ON redline.conversation_messages USING gin (to_tsvector('french'::regconfig, body));

CREATE INDEX ix_conversation_messages_recent ON redline.conversation_messages USING btree (psid, created_at DESC);

CREATE INDEX ix_conversation_questions_open ON redline.conversation_questions USING btree (psid) WHERE (status = 'open'::text);

CREATE UNIQUE INDEX ix_conversation_questions_open_fingerprint ON redline.conversation_questions USING btree (psid, fingerprint) WHERE (status = ANY (ARRAY['open'::text, 'deferred'::text]));

CREATE INDEX ix_inbound_messages_pending ON redline.inbound_messages USING btree (received_at) WHERE (status = 'pending'::text);

CREATE INDEX ix_inbound_messages_received ON redline.inbound_messages USING btree (received_at);

CREATE INDEX ix_messenger_message_refs_conversation ON redline.messenger_message_refs USING btree (conversation_message_id);

CREATE INDEX ix_notebook_entries_calendar ON redline.notebook_entries USING btree (psid, occurred_on);

CREATE INDEX ix_observations_day ON redline.observations USING btree (athlete_id, occurred_on DESC);

CREATE INDEX ix_observations_supersedes ON redline.observations USING btree (supersedes_id);

CREATE INDEX ix_outbound_deliveries_updated ON redline.outbound_deliveries USING btree (updated_at);

CREATE INDEX ix_proactive_jobs_claim ON redline.proactive_jobs USING btree (priority DESC, due_at) WHERE (status = 'pending'::text);

CREATE INDEX ix_proactive_messages_sent_at ON redline.proactive_messages USING btree (athlete_id, sent_at DESC);

CREATE INDEX ix_session_cycles_state_planned_for ON redline.session_cycles USING btree (state, planned_for);

CREATE INDEX ix_strength_session_links_expiry ON redline.strength_session_links USING btree (expires_at);

CREATE INDEX ix_turn_commands_pending ON redline.turn_commands USING btree (turn_id) WHERE (applied_at IS NULL);

CREATE INDEX ix_turn_scopes_created ON redline.turn_scopes USING btree (created_at);

CREATE TRIGGER trg_audit_event_immutable BEFORE DELETE OR UPDATE ON redline.audit_events FOR EACH ROW EXECUTE FUNCTION redline.forbid_audit_mutation();

CREATE TRIGGER trg_measurement_history BEFORE UPDATE ON redline.observation_metrics FOR EACH ROW EXECUTE FUNCTION redline.protect_measurement_history();

CREATE TRIGGER trg_pain_reading_history BEFORE UPDATE ON redline.pain_readings FOR EACH ROW EXECUTE FUNCTION redline.protect_pain_reading();

CREATE TRIGGER trg_plan_revision_contiguous BEFORE INSERT ON redline.plan_revisions FOR EACH ROW EXECUTE FUNCTION redline.protect_plan_revision();

CREATE TRIGGER trg_plan_revision_immutable BEFORE DELETE OR UPDATE ON redline.plan_revisions FOR EACH ROW EXECUTE FUNCTION redline.forbid_plan_revision_mutation();

CREATE TRIGGER trg_proactive_message_immutable BEFORE DELETE OR UPDATE ON redline.proactive_messages FOR EACH ROW EXECUTE FUNCTION redline.forbid_proactive_message_mutation();

CREATE TRIGGER trg_strength_session_link_immutable BEFORE UPDATE ON redline.strength_session_links FOR EACH ROW EXECUTE FUNCTION redline.protect_strength_session_link();

ALTER TABLE ONLY redline.athlete_activity_states
    ADD CONSTRAINT fk_athlete_activity_states_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.capability_evaluations
    ADD CONSTRAINT fk_capability_evaluations_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.coach_predictions
    ADD CONSTRAINT fk_coach_predictions_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.coach_predictions
    ADD CONSTRAINT fk_coach_predictions_zone FOREIGN KEY (athlete_id, resolves_zone) REFERENCES redline.pain_zones(athlete_id, slug);

ALTER TABLE ONLY redline.conversation_questions
    ADD CONSTRAINT fk_conversation_questions_candidate_answer FOREIGN KEY (candidate_answer_message_id) REFERENCES redline.conversation_messages(id) ON DELETE SET NULL;

ALTER TABLE ONLY redline.conversation_questions
    ADD CONSTRAINT fk_conversation_questions_source_message FOREIGN KEY (source_message_id) REFERENCES redline.conversation_messages(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.conversation_questions
    ADD CONSTRAINT fk_conversation_questions_source_platform FOREIGN KEY (source_platform_message_id) REFERENCES redline.messenger_message_refs(message_id) ON DELETE SET NULL;

ALTER TABLE ONLY redline.conversation_threads
    ADD CONSTRAINT fk_conversation_threads_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.correction_deliveries
    ADD CONSTRAINT fk_correction_deliveries_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.daily_checkins
    ADD CONSTRAINT fk_daily_checkins_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.deadlines
    ADD CONSTRAINT fk_deadlines_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.gear_items
    ADD CONSTRAINT fk_gear_items_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.messenger_message_refs
    ADD CONSTRAINT fk_messenger_message_refs_conversation_message FOREIGN KEY (conversation_message_id) REFERENCES redline.conversation_messages(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.observation_metrics
    ADD CONSTRAINT fk_observation_metrics_observation_id_observations FOREIGN KEY (observation_id) REFERENCES redline.observations(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.observations
    ADD CONSTRAINT fk_observations_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.observations
    ADD CONSTRAINT fk_observations_supersedes_id_observations FOREIGN KEY (supersedes_id) REFERENCES redline.observations(id);

ALTER TABLE ONLY redline.pain_readings
    ADD CONSTRAINT fk_pain_readings_observation_id_observations FOREIGN KEY (observation_id) REFERENCES redline.observations(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.pain_readings
    ADD CONSTRAINT fk_pain_readings_zone FOREIGN KEY (athlete_id, zone) REFERENCES redline.pain_zones(athlete_id, slug);

ALTER TABLE ONLY redline.pain_zones
    ADD CONSTRAINT fk_pain_zones_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.permit_choices
    ADD CONSTRAINT fk_permit_choices_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.plan_gates
    ADD CONSTRAINT fk_plan_gates_revision_id_plan_revisions FOREIGN KEY (revision_id) REFERENCES redline.plan_revisions(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.plan_revisions
    ADD CONSTRAINT fk_plan_revisions_plan_id_weekly_plans FOREIGN KEY (plan_id) REFERENCES redline.weekly_plans(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.planned_session_sources
    ADD CONSTRAINT fk_planned_session_sources_session_id_planned_sessions FOREIGN KEY (session_id) REFERENCES redline.planned_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.planned_sessions
    ADD CONSTRAINT fk_planned_sessions_revision_id_plan_revisions FOREIGN KEY (revision_id) REFERENCES redline.plan_revisions(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.proactive_messages
    ADD CONSTRAINT fk_proactive_messages_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.session_cycles
    ADD CONSTRAINT fk_session_cycles_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.session_cycles
    ADD CONSTRAINT fk_session_cycles_observation_id_observations FOREIGN KEY (observation_id) REFERENCES redline.observations(id);

ALTER TABLE ONLY redline.session_cycles
    ADD CONSTRAINT fk_session_cycles_plan_id_weekly_plans FOREIGN KEY (plan_id) REFERENCES redline.weekly_plans(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.session_cycles
    ADD CONSTRAINT fk_session_cycles_source_session_id_planned_sessions FOREIGN KEY (source_session_id) REFERENCES redline.planned_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.session_exposures
    ADD CONSTRAINT fk_session_exposures_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.session_exposures
    ADD CONSTRAINT fk_session_exposures_observation_id_observations FOREIGN KEY (observation_id) REFERENCES redline.observations(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.session_followups
    ADD CONSTRAINT fk_session_followups_cycle_id_session_cycles FOREIGN KEY (cycle_id) REFERENCES redline.session_cycles(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.session_followups
    ADD CONSTRAINT fk_session_followups_observation_id_observations FOREIGN KEY (observation_id) REFERENCES redline.observations(id);

ALTER TABLE ONLY redline.session_predictions
    ADD CONSTRAINT fk_session_predictions_cycle_id_session_cycles FOREIGN KEY (cycle_id) REFERENCES redline.session_cycles(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.strava_activity_intakes
    ADD CONSTRAINT fk_strava_activity_intakes_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.strava_activity_intakes
    ADD CONSTRAINT fk_strava_activity_intakes_observation_id_observations FOREIGN KEY (observation_id) REFERENCES redline.observations(id);

ALTER TABLE ONLY redline.strength_pain_readings
    ADD CONSTRAINT fk_strength_pain_readings_strength_session_id_strength_sessions FOREIGN KEY (strength_session_id) REFERENCES redline.strength_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.strength_pain_readings
    ADD CONSTRAINT fk_strength_pain_readings_zone FOREIGN KEY (athlete_id, zone) REFERENCES redline.pain_zones(athlete_id, slug);

ALTER TABLE ONLY redline.strength_results
    ADD CONSTRAINT fk_strength_results_session_id_strength_sessions FOREIGN KEY (session_id) REFERENCES redline.strength_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.strength_sessions
    ADD CONSTRAINT fk_strength_sessions_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.strength_sets
    ADD CONSTRAINT fk_strength_sets_result_id_strength_results FOREIGN KEY (result_id) REFERENCES redline.strength_results(id) ON DELETE CASCADE;

ALTER TABLE ONLY redline.weekly_instruments
    ADD CONSTRAINT fk_weekly_instruments_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);

ALTER TABLE ONLY redline.weekly_plans
    ADD CONSTRAINT fk_weekly_plans_athlete_id_athletes FOREIGN KEY (athlete_id) REFERENCES redline.athletes(id);
