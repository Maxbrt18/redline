"""Creates the whole operational storage in a single revision.

The open source edition starts from the target schema instead of copying the
deployment history. This revision is a FROZEN SNAPSHOT, generated once from
`scripts.storage.schema` by Alembic autogeneration: it no longer reads the current
declaration, otherwise every schema change would rewrite history and no later
revision could apply. The functions and triggers carry the guarantees a declaration
cannot express: immutable known measurements, contiguous append-only plan revisions,
immutable audit and proactive messages, a frozen snapshot of a delivered session.
Privileges are installed only if the `redline_owner` / `redline_app` roles already
exist.

Revision ID: 20261026_0001
Revises: None
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# The `observation_metrics` measurements at this revision, frozen with it.
MEASUREMENTS = (
    "distance_km",
    "duration_s",
    "pace_s_per_km",
    "hr_avg_bpm",
    "hr_max_bpm",
    "cadence_spm",
    "elevation_gain_m",
    "elevation_loss_m",
    "wbgt_c",
    "rpe",
    "pain",
    "enjoyment",
    "carbs_g_h",
    "gut_symptoms",
    "reps_in_reserve",
)

revision = "20261026_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("CREATE SCHEMA IF NOT EXISTS redline"))
    op.create_table('agent_sessions',
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('provider', sa.Text(), nullable=False),
    sa.Column('session_id', sa.Text(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('prompt_digest', sa.Text(), nullable=True),
    sa.CheckConstraint("provider IN ('codex','claude')", name='ck_agent_sessions_provider'),
    sa.CheckConstraint("psid <> '' AND session_id <> ''", name='ck_agent_sessions_identity'),
    sa.PrimaryKeyConstraint('psid', name=op.f('pk_agent_sessions')),
    schema='redline'
    )
    op.create_table('athletes',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("id ~ '^[a-z][a-z0-9-]{1,63}$'", name='ck_athletes_id'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_athletes')),
    schema='redline'
    )
    op.create_table('audit_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('actor', sa.Text(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('aggregate_type', sa.Text(), nullable=False),
    sa.Column('aggregate_id', sa.UUID(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events')),
    schema='redline'
    )
    op.create_index('ix_audit_events_created', 'audit_events', ['created_at'], unique=False, schema='redline')
    op.create_table('conversation_messages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('source_key', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("body <> ''", name='ck_conversation_messages_body'),
    sa.CheckConstraint("role IN ('athlete','coach','proactive')", name='ck_conversation_messages_role'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversation_messages')),
    sa.UniqueConstraint('psid', 'source_key', name='uq_conversation_messages_source'),
    schema='redline'
    )
    op.create_index('ix_conversation_messages_fts', 'conversation_messages', [sa.literal_column("to_tsvector('french'::regconfig, body)")], unique=False, schema='redline', postgresql_using='gin')
    op.create_index('ix_conversation_messages_recent', 'conversation_messages', ['psid', sa.literal_column('created_at DESC')], unique=False, schema='redline')
    op.create_table('delivery_debts',
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('iso_week', sa.Text(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("iso_week ~ '^20[0-9]{2}-W(0[1-9]|[1-4][0-9]|5[0-3])$'", name='ck_delivery_debts_week'),
    sa.CheckConstraint('revision >= 1', name='ck_delivery_debts_revision'),
    sa.PrimaryKeyConstraint('psid', name=op.f('pk_delivery_debts')),
    schema='redline'
    )
    op.create_table('handoffs',
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('psid', name=op.f('pk_handoffs')),
    schema='redline'
    )
    op.create_table('inbound_activity_states',
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('last_inbound_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('psid', name=op.f('pk_inbound_activity_states')),
    schema='redline'
    )
    op.create_table('inbound_messages',
    sa.Column('message_id', sa.Text(), nullable=False),
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('reply_to_message_id', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default='pending', nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('text','postback','sticker','attachment')", name='ck_inbound_messages_kind'),
    sa.CheckConstraint("message_id <> '' AND psid <> ''", name='ck_inbound_messages_identity'),
    sa.CheckConstraint("reply_to_message_id IS NULL OR reply_to_message_id <> ''", name='ck_inbound_messages_reply'),
    sa.CheckConstraint("status IN ('pending','done')", name='ck_inbound_messages_status'),
    sa.PrimaryKeyConstraint('message_id', name=op.f('pk_inbound_messages')),
    schema='redline'
    )
    op.create_index('ix_inbound_messages_pending', 'inbound_messages', ['received_at'], unique=False, schema='redline', postgresql_where=sa.text("status = 'pending'"))
    op.create_index('ix_inbound_messages_received', 'inbound_messages', ['received_at'], unique=False, schema='redline')
    op.create_table('notebook_credentials',
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('purpose', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("psid <> ''", name='ck_notebook_credentials_psid'),
    sa.CheckConstraint("purpose IN ('link','device')", name='ck_notebook_credentials_purpose'),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name='ck_notebook_credentials_hash'),
    sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_notebook_credentials')),
    schema='redline'
    )
    op.create_table('notebook_entries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('source_key', sa.Text(), nullable=False),
    sa.Column('slot', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('period', sa.Text(), server_default='', nullable=False),
    sa.Column('occurred_on', sa.Date(), nullable=False),
    sa.Column('body', sa.Text(), server_default='', nullable=False),
    sa.Column('image', sa.LargeBinary(), nullable=True),
    sa.Column('image_type', sa.Text(), server_default='image/png', nullable=False),
    sa.Column('image_hash', sa.Text(), server_default='', nullable=False),
    sa.Column('snapshot', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('origin', sa.Text(), server_default='delivery', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("kind IN ('weekly','monthly','strength','other')", name='ck_notebook_entries_kind'),
    sa.CheckConstraint("origin IN ('delivery','recovered')", name='ck_notebook_entries_origin'),
    sa.CheckConstraint("psid <> '' AND source_key <> '' AND slot <> ''", name='ck_notebook_entries_identity'),
    sa.CheckConstraint('image IS NULL OR octet_length(image) <= 12582912', name='ck_notebook_entries_image'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notebook_entries')),
    sa.UniqueConstraint('psid', 'source_key', 'slot', name='uq_notebook_entries_source'),
    schema='redline'
    )
    op.create_index('ix_notebook_entries_calendar', 'notebook_entries', ['psid', 'occurred_on'], unique=False, schema='redline')
    op.create_table('outbound_deliveries',
    sa.Column('source_key', sa.Text(), nullable=False),
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('payload_digest', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='1', nullable=False),
    sa.Column('last_error', sa.Text(), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("payload IS NULL OR jsonb_typeof(payload) = 'object'", name='ck_outbound_deliveries_payload'),
    sa.CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'", name='ck_outbound_deliveries_digest'),
    sa.CheckConstraint("source_key <> '' AND psid <> ''", name='ck_outbound_deliveries_identity'),
    sa.CheckConstraint("status IN ('prepared','sending','delivered','failed')", name='ck_outbound_deliveries_status'),
    sa.CheckConstraint('attempts >= 1', name='ck_outbound_deliveries_attempts'),
    sa.PrimaryKeyConstraint('source_key', name=op.f('pk_outbound_deliveries')),
    schema='redline'
    )
    op.create_index('ix_outbound_deliveries_updated', 'outbound_deliveries', ['updated_at'], unique=False, schema='redline')
    op.create_table('patience_notifications',
    sa.Column('singleton', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('singleton', name='ck_patience_notifications_singleton'),
    sa.PrimaryKeyConstraint('singleton', name=op.f('pk_patience_notifications')),
    schema='redline'
    )
    op.create_table('proactive_delivery_budgets',
    sa.Column('budget_on', sa.Date(), nullable=False),
    sa.Column('sent_count', sa.Integer(), nullable=False),
    sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('sent_count >= 0', name='ck_proactive_delivery_budgets_count'),
    sa.PrimaryKeyConstraint('budget_on', name=op.f('pk_proactive_delivery_budgets')),
    schema='redline'
    )
    op.create_table('proactive_jobs',
    sa.Column('job_key', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('detail', sa.Text(), server_default='', nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('priority', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('status', sa.Text(), server_default='pending', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), server_default='', nullable=False),
    sa.CheckConstraint("kind IN ('strava_debrief','strava_debrief_differe','silence','registre_incomplet','morning','weekly','weekly_repair','monthly','session_followup','missed_session','daily_signal','premortem','correction','decision_challenge','gut_review','gear_alert','race_countdown','strength_card','calibration_eve','operator_message','trace_reveil')", name='ck_proactive_jobs_kind'),
    sa.CheckConstraint("status IN ('pending','running','done','failed')", name='ck_proactive_jobs_status'),
    sa.CheckConstraint('attempts >= 0', name='ck_proactive_jobs_attempts'),
    sa.CheckConstraint('expires_at IS NULL OR expires_at >= due_at', name='ck_proactive_jobs_expiry'),
    sa.CheckConstraint('length(last_error) <= 500', name='ck_proactive_jobs_last_error'),
    sa.PrimaryKeyConstraint('job_key', name=op.f('pk_proactive_jobs')),
    schema='redline'
    )
    op.create_index('ix_proactive_jobs_claim', 'proactive_jobs', [sa.literal_column('priority DESC'), 'due_at'], unique=False, schema='redline', postgresql_where=sa.text("status = 'pending'"))
    op.create_table('proactive_markers',
    sa.Column('marker_key', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("marker_key <> ''", name='ck_proactive_markers_key'),
    sa.PrimaryKeyConstraint('marker_key', name=op.f('pk_proactive_markers')),
    schema='redline'
    )
    op.create_table('projection_states',
    sa.Column('singleton', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('dirty_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_projected_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('singleton', name='ck_projection_states_singleton'),
    sa.PrimaryKeyConstraint('singleton', name=op.f('pk_projection_states')),
    schema='redline'
    )
    op.create_table('strength_report_claims',
    sa.Column('session_key', sa.Text(), nullable=False),
    sa.Column('claimed_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.PrimaryKeyConstraint('session_key', name=op.f('pk_strength_report_claims')),
    schema='redline'
    )
    op.create_table('strength_report_daily_quotas',
    sa.Column('quota_on', sa.Date(), nullable=False),
    sa.Column('accepted_count', sa.Integer(), nullable=False),
    sa.CheckConstraint('accepted_count >= 0', name='ck_strength_report_daily_quotas_count'),
    sa.PrimaryKeyConstraint('quota_on', name=op.f('pk_strength_report_daily_quotas')),
    schema='redline'
    )
    op.create_table('strength_session_links',
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('session_key', sa.Text(), nullable=False),
    sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("jsonb_typeof(snapshot) = 'object'", name='ck_strength_session_links_snapshot'),
    sa.CheckConstraint("psid <> ''", name='ck_strength_session_links_psid'),
    sa.CheckConstraint("session_key <> '' AND char_length(session_key) <= 64", name='ck_strength_session_links_session_key'),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name='ck_strength_session_links_token_hash'),
    sa.CheckConstraint('expires_at > created_at', name='ck_strength_session_links_expiry'),
    sa.CheckConstraint('revoked_at IS NULL OR revoked_at >= created_at', name='ck_strength_session_links_revocation'),
    sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_strength_session_links')),
    schema='redline'
    )
    op.create_index('ix_strength_session_links_expiry', 'strength_session_links', ['expires_at'], unique=False, schema='redline')
    op.create_table('turn_commands',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('turn_id', sa.UUID(), nullable=False),
    sa.Column('command_key', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("command_key <> ''", name='ck_turn_commands_command_key'),
    sa.CheckConstraint("kind IN ('observation.add','activity.link','measurement.complete','measurement.hr_doubt','exposure.upsert','plan.revise','daily.upsert','capability.evaluate','thread.upsert','question.review','question.ask','deadline.upsert','session.start','session.match','proactive.angle','gear.upsert','prediction.upsert','correction.register','permit.seal') OR (kind = 'proactive_message.add' AND applied_at IS NOT NULL)", name='ck_turn_commands_kind'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_turn_commands')),
    sa.UniqueConstraint('command_key', name=op.f('uq_turn_commands_command_key')),
    schema='redline'
    )
    op.create_index('ix_turn_commands_pending', 'turn_commands', ['turn_id'], unique=False, schema='redline', postgresql_where=sa.text('applied_at IS NULL'))
    op.create_table('turn_scopes',
    sa.Column('turn_id', sa.UUID(), nullable=False),
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("psid <> ''", name='ck_turn_scopes_psid'),
    sa.PrimaryKeyConstraint('turn_id', name=op.f('pk_turn_scopes')),
    schema='redline'
    )
    op.create_index('ix_turn_scopes_created', 'turn_scopes', ['created_at'], unique=False, schema='redline')
    op.create_table('athlete_activity_states',
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_athlete_activity_states_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('athlete_id', name=op.f('pk_athlete_activity_states')),
    schema='redline'
    )
    op.create_table('capability_evaluations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('capability_number', sa.SmallInteger(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('need', sa.Text(), nullable=False),
    sa.Column('score', sa.SmallInteger(), nullable=False),
    sa.Column('scale', sa.SmallInteger(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('evaluated_on', sa.Date(), nullable=False),
    sa.Column('next_test', sa.Text(), nullable=True),
    sa.Column('lock_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("status IN ('rouge','orange','vert')", name='ck_capability_evaluations_status'),
    sa.CheckConstraint('capability_number BETWEEN 1 AND 9', name='ck_capability_evaluations_number'),
    sa.CheckConstraint('scale BETWEEN 1 AND 9', name='ck_capability_evaluations_scale'),
    sa.CheckConstraint('score BETWEEN 0 AND scale', name='ck_capability_evaluations_score'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_capability_evaluations_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_capability_evaluations')),
    sa.UniqueConstraint('athlete_id', 'capability_number', 'evaluated_on', name='uq_capability_evaluations_day'),
    schema='redline'
    )
    op.create_table('coach_predictions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('prediction_key', sa.Text(), nullable=False),
    sa.Column('domain', sa.Text(), nullable=False),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('probability', sa.Numeric(precision=4, scale=3), nullable=False),
    sa.Column('due_on', sa.Date(), nullable=False),
    sa.Column('direction', sa.Text(), server_default='neutral', nullable=False),
    sa.Column('status', sa.Text(), server_default='open', nullable=False),
    sa.Column('outcome', sa.Boolean(), nullable=True),
    sa.Column('brier_score', sa.Numeric(precision=7, scale=6), nullable=True),
    sa.Column('challenge', sa.Text(), nullable=True),
    sa.Column('challenged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolves_kind', sa.Text(), nullable=True),
    sa.Column('resolves_on', sa.Date(), nullable=True),
    sa.Column('success_op', sa.Text(), nullable=True),
    sa.Column('success_value', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.CheckConstraint("(resolves_kind IS NULL AND resolves_on IS NULL AND success_op IS NULL AND success_value IS NULL) OR (resolves_kind IN ('reveil') AND resolves_on IS NOT NULL AND success_op IN ('<=','<','>=','>','=') AND success_value IS NOT NULL AND resolves_on <= due_on)", name='ck_coach_predictions_resolves'),
    sa.CheckConstraint("(status = 'resolved' AND outcome IS NOT NULL AND brier_score IS NOT NULL AND resolved_at IS NOT NULL) OR (status = 'open' AND outcome IS NULL AND brier_score IS NULL AND resolved_at IS NULL) OR (status = 'void' AND outcome IS NULL AND brier_score IS NULL AND resolved_at IS NOT NULL)", name='ck_coach_predictions_resolution'),
    sa.CheckConstraint("direction IN ('increase','decrease','neutral')", name='ck_coach_predictions_direction'),
    sa.CheckConstraint("status IN ('open','resolved','void')", name='ck_coach_predictions_status'),
    sa.CheckConstraint('probability BETWEEN 0 AND 1', name='ck_coach_predictions_probability'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_coach_predictions_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_coach_predictions')),
    sa.UniqueConstraint('athlete_id', 'prediction_key', name='uq_coach_predictions_key'),
    schema='redline'
    )
    op.create_index('ix_coach_predictions_due', 'coach_predictions', ['due_on'], unique=False, schema='redline', postgresql_where=sa.text("status = 'open'"))
    op.create_index('ix_coach_predictions_resolves', 'coach_predictions', ['resolves_kind', 'resolves_on'], unique=False, schema='redline', postgresql_where=sa.text("status = 'open' AND resolves_kind IS NOT NULL"))
    op.create_table('conversation_threads',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('thread_key', sa.Text(), nullable=False),
    sa.Column('opened_on', sa.Date(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('follow_up_on', sa.Date(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'clos') = (closed_at IS NOT NULL)", name='ck_conversation_threads_closed_at'),
    sa.CheckConstraint("kind IN ('promesse','ressenti','suspens')", name='ck_conversation_threads_kind'),
    sa.CheckConstraint("status IN ('ouvert','clos')", name='ck_conversation_threads_status'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_conversation_threads_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversation_threads')),
    sa.UniqueConstraint('athlete_id', 'thread_key', name='uq_conversation_threads_key'),
    schema='redline'
    )
    op.create_table('correction_deliveries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('correction_key', sa.Text(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('registered_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("correction_key <> '' AND summary <> ''", name='ck_correction_deliveries_identity'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_correction_deliveries_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_correction_deliveries')),
    sa.UniqueConstraint('athlete_id', 'correction_key', name='uq_correction_deliveries_key'),
    schema='redline'
    )
    op.create_table('daily_checkins',
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('checked_on', sa.Date(), nullable=False),
    sa.Column('sleep', sa.Text(), nullable=True),
    sa.Column('life_load', sa.Text(), nullable=True),
    sa.Column('desire', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("desire IS NULL OR desire IN ('forte','normale','basse')", name='ck_daily_checkins_desire'),
    sa.CheckConstraint("life_load IS NULL OR life_load IN ('legere','normale','elevee')", name='ck_daily_checkins_life_load'),
    sa.CheckConstraint("sleep IS NULL OR sleep IN ('bonne','moyenne','mauvaise')", name='ck_daily_checkins_sleep'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_daily_checkins_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('athlete_id', 'checked_on', name=op.f('pk_daily_checkins')),
    schema='redline'
    )
    op.create_table('deadlines',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('deadline_key', sa.Text(), nullable=False),
    sa.Column('due_on', sa.Date(), nullable=False),
    sa.Column('item', sa.Text(), nullable=False),
    sa.Column('fact_owner', sa.Text(), nullable=False),
    sa.Column('blocks', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('completed_on', sa.Date(), nullable=True),
    sa.Column('kind', sa.Text(), server_default='general', nullable=False),
    sa.Column('starts_on', sa.Date(), nullable=True),
    sa.Column('ends_on', sa.Date(), nullable=True),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('athlete_label', sa.Text(), nullable=True),
    sa.CheckConstraint("completed_on IS NULL OR status = 'done'", name='ck_deadlines_completed_on'),
    sa.CheckConstraint("kind IN ('general','medical','availability','race')", name='ck_deadlines_kind'),
    sa.CheckConstraint("status IN ('todo','scheduled','done')", name='ck_deadlines_status'),
    sa.CheckConstraint('athlete_label IS NULL OR char_length(athlete_label) BETWEEN 3 AND 80', name='ck_deadlines_athlete_label'),
    sa.CheckConstraint('starts_on IS NULL OR ends_on IS NULL OR starts_on <= ends_on', name='ck_deadlines_range'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_deadlines_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_deadlines')),
    sa.UniqueConstraint('athlete_id', 'deadline_key', name='uq_deadlines_key'),
    schema='redline'
    )
    op.create_table('gear_items',
    sa.Column('gear_id', sa.Text(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('retire_at_km', sa.Numeric(precision=7, scale=1), nullable=False),
    sa.Column('strava_distance_km', sa.Numeric(precision=9, scale=1), nullable=True),
    sa.Column('distance_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('primary', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('status', sa.Text(), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("gear_id <> '' AND name <> ''", name='ck_gear_items_identity'),
    sa.CheckConstraint("status IN ('active','retired')", name='ck_gear_items_status'),
    sa.CheckConstraint('retire_at_km > 0', name='ck_gear_items_retire'),
    sa.CheckConstraint('strava_distance_km IS NULL OR strava_distance_km >= 0', name='ck_gear_items_strava_distance'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_gear_items_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('gear_id', name=op.f('pk_gear_items')),
    schema='redline'
    )
    op.create_table('messenger_message_refs',
    sa.Column('message_id', sa.Text(), nullable=False),
    sa.Column('conversation_message_id', sa.UUID(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("message_id <> '' AND body <> ''", name='ck_messenger_message_refs_identity'),
    sa.ForeignKeyConstraint(['conversation_message_id'], ['redline.conversation_messages.id'], name='fk_messenger_message_refs_conversation_message', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('message_id', name=op.f('pk_messenger_message_refs')),
    schema='redline'
    )
    op.create_index('ix_messenger_message_refs_conversation', 'messenger_message_refs', ['conversation_message_id'], unique=False, schema='redline')
    op.create_table('observations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('occurred_on', sa.Date(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('idempotency_key', sa.Text(), nullable=False),
    sa.Column('strava_activity_id', sa.BigInteger(), nullable=True),
    sa.Column('gear_id', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('source', sa.Text(), server_default='application', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('supersedes_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("idempotency_key <> ''", name='ck_observations_idempotency_key'),
    sa.CheckConstraint("kind IN ('EF','SL','seuil','côtes','lignes','cadence','course','rando','vélo','renfoA','renfoB','instruments','lecture','reveil','palpation')", name='ck_observations_kind'),
    sa.CheckConstraint('strava_activity_id IS NULL OR strava_activity_id > 0', name='ck_observations_strava_positive'),
    sa.CheckConstraint('supersedes_id IS NULL OR supersedes_id <> id', name='ck_observations_not_self_superseding'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_observations_athlete_id_athletes')),
    sa.ForeignKeyConstraint(['supersedes_id'], ['redline.observations.id'], name=op.f('fk_observations_supersedes_id_observations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_observations')),
    sa.UniqueConstraint('athlete_id', 'idempotency_key', name='uq_observations_idempotency'),
    sa.UniqueConstraint('athlete_id', 'strava_activity_id', name='uq_observations_strava'),
    schema='redline'
    )
    op.create_index('ix_observations_day', 'observations', ['athlete_id', sa.literal_column('occurred_on DESC')], unique=False, schema='redline')
    op.create_index('ix_observations_supersedes', 'observations', ['supersedes_id'], unique=False, schema='redline')
    op.create_table('permit_choices',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('domain', sa.Text(), server_default='ef', nullable=False),
    sa.Column('iso_week', sa.Text(), nullable=False),
    sa.Column('session_key', sa.Text(), nullable=False),
    sa.Column('scheduled_on', sa.Date(), nullable=False),
    sa.Column('coach_minutes', sa.SmallInteger(), nullable=False),
    sa.Column('prediction_key', sa.Text(), nullable=False),
    sa.Column('sealed_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("domain IN ('ef')", name='ck_permit_choices_domain'),
    sa.CheckConstraint("iso_week ~ '^20[0-9]{2}-W[0-9]{2}$'", name='ck_permit_choices_week'),
    sa.CheckConstraint("prediction_key <> ''", name='ck_permit_choices_prediction'),
    sa.CheckConstraint("session_key ~ '^[a-z0-9][a-z0-9-]*$'", name='ck_permit_choices_key'),
    sa.CheckConstraint('coach_minutes BETWEEN 10 AND 180', name='ck_permit_choices_minutes'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_permit_choices_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_permit_choices')),
    sa.UniqueConstraint('athlete_id', 'iso_week', 'session_key', name='uq_permit_choices_session'),
    schema='redline'
    )
    op.create_table('proactive_messages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('source_key', sa.Text(), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('angle', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("kind IN ('decision','morning','debrief','weekly','monthly','correction','other')", name='ck_proactive_messages_kind'),
    sa.CheckConstraint("source_key <> ''", name='ck_proactive_messages_source_key'),
    sa.CheckConstraint("summary <> ''", name='ck_proactive_messages_summary'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_proactive_messages_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_proactive_messages')),
    sa.UniqueConstraint('athlete_id', 'source_key', name='uq_proactive_messages_source_key'),
    schema='redline'
    )
    op.create_index('ix_proactive_messages_sent_at', 'proactive_messages', ['athlete_id', sa.literal_column('sent_at DESC')], unique=False, schema='redline')
    op.create_table('strength_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('session_key', sa.Text(), nullable=False),
    sa.Column('performed_on', sa.Date(), nullable=False),
    sa.Column('workout', sa.Text(), nullable=True),
    sa.Column('shin_before', sa.SmallInteger(), nullable=True),
    sa.Column('shin_after', sa.SmallInteger(), nullable=True),
    sa.Column('duration_minutes', sa.SmallInteger(), nullable=True),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('prescription', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("session_key <> '' AND char_length(session_key) <= 64", name='ck_strength_sessions_key'),
    sa.CheckConstraint("workout IS NULL OR workout IN ('a','b','assimilation')", name='ck_strength_sessions_workout'),
    sa.CheckConstraint('char_length(note) <= 300', name='ck_strength_sessions_note'),
    sa.CheckConstraint('duration_minutes IS NULL OR duration_minutes BETWEEN 0 AND 300', name='ck_strength_sessions_duration'),
    sa.CheckConstraint('shin_after IS NULL OR shin_after BETWEEN 0 AND 10', name='ck_strength_sessions_shin_after'),
    sa.CheckConstraint('shin_before IS NULL OR shin_before BETWEEN 0 AND 10', name='ck_strength_sessions_shin_before'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_strength_sessions_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_strength_sessions')),
    sa.UniqueConstraint('athlete_id', 'session_key', name='uq_strength_sessions_key'),
    schema='redline'
    )
    op.create_table('weekly_instruments',
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('iso_week', sa.Text(), nullable=False),
    sa.Column('reviewed_on', sa.Date(), nullable=False),
    sa.Column('cmj_cm', sa.Numeric(precision=7, scale=2), nullable=True),
    sa.Column('calf_right_straight', sa.SmallInteger(), nullable=True),
    sa.Column('calf_left_straight', sa.SmallInteger(), nullable=True),
    sa.Column('calf_right_bent', sa.SmallInteger(), nullable=True),
    sa.Column('calf_left_bent', sa.SmallInteger(), nullable=True),
    sa.Column('enjoyment', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.Column('canary_pain', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.Column('pain_calibration_gap', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('hr_calibration_gap', sa.Numeric(precision=7, scale=2), nullable=True),
    sa.Column('sleep', sa.Text(), nullable=True),
    sa.Column('life_load', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("iso_week ~ '^20[0-9]{2}-W(0[1-9]|[1-4][0-9]|5[0-3])$'", name='ck_weekly_instruments_week'),
    sa.CheckConstraint("life_load IS NULL OR life_load IN ('normale','elevee')", name='ck_weekly_instruments_life_load'),
    sa.CheckConstraint("sleep IS NULL OR sleep IN ('ok','2_3_mauvaises_nuits','degrade_semaine')", name='ck_weekly_instruments_sleep'),
    sa.CheckConstraint('calf_left_bent IS NULL OR calf_left_bent BETWEEN 0 AND 999', name='ck_weekly_instruments_calf_left_bent'),
    sa.CheckConstraint('calf_left_straight IS NULL OR calf_left_straight BETWEEN 0 AND 999', name='ck_weekly_instruments_calf_left_straight'),
    sa.CheckConstraint('calf_right_bent IS NULL OR calf_right_bent BETWEEN 0 AND 999', name='ck_weekly_instruments_calf_right_bent'),
    sa.CheckConstraint('calf_right_straight IS NULL OR calf_right_straight BETWEEN 0 AND 999', name='ck_weekly_instruments_calf_right_straight'),
    sa.CheckConstraint('canary_pain IS NULL OR canary_pain BETWEEN 0 AND 10', name='ck_weekly_instruments_canary_pain'),
    sa.CheckConstraint('cmj_cm IS NULL OR cmj_cm BETWEEN 0 AND 100', name='ck_weekly_instruments_cmj'),
    sa.CheckConstraint('enjoyment IS NULL OR enjoyment BETWEEN 0 AND 10', name='ck_weekly_instruments_enjoyment'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_weekly_instruments_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('athlete_id', 'iso_week', name=op.f('pk_weekly_instruments')),
    schema='redline'
    )
    op.create_table('weekly_plans',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('iso_week', sa.Text(), nullable=False),
    sa.CheckConstraint("iso_week ~ '^20[0-9]{2}-W(0[1-9]|[1-4][0-9]|5[0-3])$'", name='ck_weekly_plans_week'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_weekly_plans_athlete_id_athletes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_weekly_plans')),
    sa.UniqueConstraint('athlete_id', 'iso_week', name='uq_weekly_plans_week'),
    schema='redline'
    )
    op.create_table('conversation_questions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('psid', sa.Text(), nullable=False),
    sa.Column('fingerprint', sa.Text(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('source_message_id', sa.UUID(), nullable=False),
    sa.Column('source_platform_message_id', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default='open', nullable=False),
    sa.Column('candidate_answer_message_id', sa.UUID(), nullable=True),
    sa.Column('asked_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('review_note', sa.Text(), server_default='', nullable=False),
    sa.Column('follow_up_on', sa.Date(), nullable=True),
    sa.CheckConstraint("(status IN ('open','deferred')) = (resolved_at IS NULL)", name='ck_conversation_questions_resolution'),
    sa.CheckConstraint("status = 'deferred' OR follow_up_on IS NULL", name='ck_conversation_questions_follow_up'),
    sa.CheckConstraint("status IN ('open','deferred','answered','expired')", name='ck_conversation_questions_status'),
    sa.CheckConstraint('length(review_note) <= 500', name='ck_conversation_questions_note'),
    sa.ForeignKeyConstraint(['candidate_answer_message_id'], ['redline.conversation_messages.id'], name='fk_conversation_questions_candidate_answer', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_message_id'], ['redline.conversation_messages.id'], name='fk_conversation_questions_source_message', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_platform_message_id'], ['redline.messenger_message_refs.message_id'], name='fk_conversation_questions_source_platform', ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversation_questions')),
    schema='redline'
    )
    op.create_index('ix_conversation_questions_open', 'conversation_questions', ['psid'], unique=False, schema='redline', postgresql_where=sa.text("status = 'open'"))
    op.create_index('ix_conversation_questions_open_fingerprint', 'conversation_questions', ['psid', 'fingerprint'], unique=True, schema='redline', postgresql_where=sa.text("status IN ('open', 'deferred')"))
    op.create_table('observation_metrics',
    sa.Column('observation_id', sa.UUID(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('distance_km', sa.Numeric(precision=7, scale=3), nullable=True),
    sa.Column('distance_km_state', sa.Text(), nullable=False),
    sa.Column('duration_s', sa.Integer(), nullable=True),
    sa.Column('duration_s_state', sa.Text(), nullable=False),
    sa.Column('pace_s_per_km', sa.Integer(), nullable=True),
    sa.Column('pace_s_per_km_state', sa.Text(), nullable=False),
    sa.Column('hr_avg_bpm', sa.SmallInteger(), nullable=True),
    sa.Column('hr_avg_bpm_state', sa.Text(), nullable=False),
    sa.Column('hr_max_bpm', sa.SmallInteger(), nullable=True),
    sa.Column('hr_max_bpm_state', sa.Text(), nullable=False),
    sa.Column('cadence_spm', sa.SmallInteger(), nullable=True),
    sa.Column('cadence_spm_state', sa.Text(), nullable=False),
    sa.Column('elevation_gain_m', sa.Integer(), nullable=True),
    sa.Column('elevation_gain_m_state', sa.Text(), nullable=False),
    sa.Column('elevation_loss_m', sa.Integer(), nullable=True),
    sa.Column('elevation_loss_m_state', sa.Text(), nullable=False),
    sa.Column('wbgt_c', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.Column('wbgt_c_state', sa.Text(), nullable=False),
    sa.Column('rpe', sa.Numeric(precision=3, scale=1), nullable=True),
    sa.Column('rpe_state', sa.Text(), nullable=False),
    sa.Column('pain', sa.Numeric(precision=3, scale=1), nullable=True),
    sa.Column('pain_state', sa.Text(), nullable=False),
    sa.Column('enjoyment', sa.Numeric(precision=3, scale=1), nullable=True),
    sa.Column('enjoyment_state', sa.Text(), nullable=False),
    sa.Column('carbs_g_h', sa.Numeric(precision=5, scale=1), nullable=True),
    sa.Column('carbs_g_h_state', sa.Text(), nullable=False),
    sa.Column('gut_symptoms', sa.Numeric(precision=3, scale=1), nullable=True),
    sa.Column('gut_symptoms_state', sa.Text(), nullable=False),
    sa.Column('reps_in_reserve', sa.SmallInteger(), nullable=True),
    sa.Column('reps_in_reserve_state', sa.Text(), nullable=False),
    sa.Column('hr_doubt', sa.Text(), nullable=True),
    sa.CheckConstraint("(cadence_spm_state = 'known' AND cadence_spm IS NOT NULL AND (cadence_spm BETWEEN 0 AND 300)) OR (cadence_spm_state <> 'known' AND cadence_spm IS NULL)", name='ck_observation_metrics_cadence_spm_value'),
    sa.CheckConstraint("(carbs_g_h_state = 'known' AND carbs_g_h IS NOT NULL AND (carbs_g_h BETWEEN 0 AND 200)) OR (carbs_g_h_state <> 'known' AND carbs_g_h IS NULL)", name='ck_observation_metrics_carbs_g_h_value'),
    sa.CheckConstraint("(distance_km_state = 'known' AND distance_km IS NOT NULL AND (distance_km >= 0 AND distance_km <= 1000)) OR (distance_km_state <> 'known' AND distance_km IS NULL)", name='ck_observation_metrics_distance_km_value'),
    sa.CheckConstraint("(duration_s_state = 'known' AND duration_s IS NOT NULL AND (duration_s >= 0 AND duration_s <= 604800)) OR (duration_s_state <> 'known' AND duration_s IS NULL)", name='ck_observation_metrics_duration_s_value'),
    sa.CheckConstraint("(elevation_gain_m_state = 'known' AND elevation_gain_m IS NOT NULL AND (elevation_gain_m BETWEEN 0 AND 20000)) OR (elevation_gain_m_state <> 'known' AND elevation_gain_m IS NULL)", name='ck_observation_metrics_elevation_gain_m_value'),
    sa.CheckConstraint("(elevation_loss_m_state = 'known' AND elevation_loss_m IS NOT NULL AND (elevation_loss_m BETWEEN 0 AND 20000)) OR (elevation_loss_m_state <> 'known' AND elevation_loss_m IS NULL)", name='ck_observation_metrics_elevation_loss_m_value'),
    sa.CheckConstraint("(enjoyment_state = 'known' AND enjoyment IS NOT NULL AND (enjoyment BETWEEN 0 AND 10)) OR (enjoyment_state <> 'known' AND enjoyment IS NULL)", name='ck_observation_metrics_enjoyment_value'),
    sa.CheckConstraint("(gut_symptoms_state = 'known' AND gut_symptoms IS NOT NULL AND (gut_symptoms BETWEEN 0 AND 10)) OR (gut_symptoms_state <> 'known' AND gut_symptoms IS NULL)", name='ck_observation_metrics_gut_symptoms_value'),
    sa.CheckConstraint("(hr_avg_bpm_state = 'known' AND hr_avg_bpm IS NOT NULL AND (hr_avg_bpm BETWEEN 20 AND 250)) OR (hr_avg_bpm_state <> 'known' AND hr_avg_bpm IS NULL)", name='ck_observation_metrics_hr_avg_bpm_value'),
    sa.CheckConstraint("(hr_max_bpm_state = 'known' AND hr_max_bpm IS NOT NULL AND (hr_max_bpm BETWEEN 20 AND 250)) OR (hr_max_bpm_state <> 'known' AND hr_max_bpm IS NULL)", name='ck_observation_metrics_hr_max_bpm_value'),
    sa.CheckConstraint("(pace_s_per_km_state = 'known' AND pace_s_per_km IS NOT NULL AND (pace_s_per_km BETWEEN 60 AND 3600)) OR (pace_s_per_km_state <> 'known' AND pace_s_per_km IS NULL)", name='ck_observation_metrics_pace_s_per_km_value'),
    sa.CheckConstraint("(pain_state = 'known' AND pain IS NOT NULL AND (pain BETWEEN 0 AND 10)) OR (pain_state <> 'known' AND pain IS NULL)", name='ck_observation_metrics_pain_value'),
    sa.CheckConstraint("(reps_in_reserve_state = 'known' AND reps_in_reserve IS NOT NULL AND (reps_in_reserve BETWEEN 0 AND 5)) OR (reps_in_reserve_state <> 'known' AND reps_in_reserve IS NULL)", name='ck_observation_metrics_reps_in_reserve_value'),
    sa.CheckConstraint("(rpe_state = 'known' AND rpe IS NOT NULL AND (rpe BETWEEN 0 AND 10)) OR (rpe_state <> 'known' AND rpe IS NULL)", name='ck_observation_metrics_rpe_value'),
    sa.CheckConstraint("(wbgt_c_state = 'known' AND wbgt_c IS NOT NULL AND (wbgt_c BETWEEN -50 AND 60)) OR (wbgt_c_state <> 'known' AND wbgt_c IS NULL)", name='ck_observation_metrics_wbgt_c_value'),
    sa.CheckConstraint("cadence_spm_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_cadence_spm_state'),
    sa.CheckConstraint("carbs_g_h_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_carbs_g_h_state'),
    sa.CheckConstraint("distance_km_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_distance_km_state'),
    sa.CheckConstraint("duration_s_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_duration_s_state'),
    sa.CheckConstraint("elevation_gain_m_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_elevation_gain_m_state'),
    sa.CheckConstraint("elevation_loss_m_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_elevation_loss_m_state'),
    sa.CheckConstraint("enjoyment_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_enjoyment_state'),
    sa.CheckConstraint("gut_symptoms_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_gut_symptoms_state'),
    sa.CheckConstraint("hr_avg_bpm_state <> 'known' OR hr_max_bpm_state <> 'known' OR hr_avg_bpm <= hr_max_bpm", name='ck_observation_metrics_hr_order'),
    sa.CheckConstraint("hr_avg_bpm_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_hr_avg_bpm_state'),
    sa.CheckConstraint("hr_doubt IS NULL OR (hr_doubt IN ('sans_brassard','contexte') AND hr_avg_bpm_state = 'known')", name='ck_observation_metrics_hr_doubt'),
    sa.CheckConstraint("hr_max_bpm_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_hr_max_bpm_state'),
    sa.CheckConstraint("pace_s_per_km_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_pace_s_per_km_state'),
    sa.CheckConstraint("pain_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_pain_state'),
    sa.CheckConstraint("reps_in_reserve_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_reps_in_reserve_state'),
    sa.CheckConstraint("rpe_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_rpe_state'),
    sa.CheckConstraint("wbgt_c_state IN ('pending','known','unavailable','not_applicable')", name='ck_observation_metrics_wbgt_c_state'),
    sa.ForeignKeyConstraint(['observation_id'], ['redline.observations.id'], name=op.f('fk_observation_metrics_observation_id_observations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('observation_id', name=op.f('pk_observation_metrics')),
    schema='redline'
    )
    op.create_table('plan_revisions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('plan_id', sa.UUID(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('persisted_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('constraints', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('changes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('calibration_target', sa.SmallInteger(), nullable=True),
    sa.Column('volume_target_km', sa.Numeric(precision=5, scale=1), nullable=True),
    sa.CheckConstraint('calibration_target IS NULL OR calibration_target IN (0, 2, 3)', name='ck_plan_revisions_calibration_target'),
    sa.CheckConstraint('revision >= 1', name='ck_plan_revisions_number'),
    sa.CheckConstraint('volume_target_km IS NULL OR volume_target_km BETWEEN 1 AND 200', name='ck_plan_revisions_volume_target'),
    sa.ForeignKeyConstraint(['plan_id'], ['redline.weekly_plans.id'], name=op.f('fk_plan_revisions_plan_id_weekly_plans'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plan_revisions')),
    sa.UniqueConstraint('plan_id', 'revision', name='uq_plan_revisions_number'),
    schema='redline'
    )
    op.create_table('session_exposures',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('observation_id', sa.UUID(), nullable=False),
    sa.Column('exposure_key', sa.Text(), nullable=False),
    sa.Column('exposure_type', sa.Text(), nullable=False),
    sa.Column('origin', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('duration_min', sa.SmallInteger(), nullable=True),
    sa.Column('target_value', sa.Numeric(precision=8, scale=2), nullable=True),
    sa.Column('target_unit', sa.Text(), nullable=True),
    sa.Column('tolerance', sa.Text(), server_default='unknown', nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("exposure_key <> ''", name='ck_session_exposures_key'),
    sa.CheckConstraint("exposure_type IN ('cadence','descente','lignes','nutrition','equipement','autre')", name='ck_session_exposures_type'),
    sa.CheckConstraint("origin IN ('prescribed','voluntary','spontaneous')", name='ck_session_exposures_origin'),
    sa.CheckConstraint("status IN ('candidate','confirmed','dismissed')", name='ck_session_exposures_status'),
    sa.CheckConstraint("tolerance IN ('unknown','easy','strained','protective')", name='ck_session_exposures_tolerance'),
    sa.CheckConstraint('(target_value IS NULL) = (target_unit IS NULL)', name='ck_session_exposures_target_pair'),
    sa.CheckConstraint('duration_min IS NULL OR duration_min BETWEEN 1 AND 1440', name='ck_session_exposures_duration'),
    sa.CheckConstraint('target_value IS NULL OR target_value >= 0', name='ck_session_exposures_target_value'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_session_exposures_athlete_id_athletes')),
    sa.ForeignKeyConstraint(['observation_id'], ['redline.observations.id'], name=op.f('fk_session_exposures_observation_id_observations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_session_exposures')),
    sa.UniqueConstraint('athlete_id', 'exposure_key', name='uq_session_exposures_key'),
    schema='redline'
    )
    op.create_table('strava_activity_intakes',
    sa.Column('activity_id', sa.BigInteger(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sport', sa.Text(), nullable=False),
    sa.Column('distilled', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('candidate_session_keys', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('observation_id', sa.UUID(), nullable=True),
    sa.Column('error', sa.Text(), server_default='', nullable=False),
    sa.Column('elevation_loss_state', sa.Text(), server_default='pending', nullable=False),
    sa.Column('elevation_loss_value', sa.Integer(), nullable=True),
    sa.Column('elevation_loss_attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('elevation_loss_error', sa.Text(), server_default='', nullable=False),
    sa.Column('wbgt_state', sa.Text(), server_default='pending', nullable=False),
    sa.Column('wbgt_value', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.Column('wbgt_attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('wbgt_error', sa.Text(), server_default='', nullable=False),
    sa.Column('streams', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('weather', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("(elevation_loss_state = 'known' AND elevation_loss_value IS NOT NULL) OR (elevation_loss_state <> 'known' AND elevation_loss_value IS NULL)", name='ck_strava_activity_intakes_elevation_loss_value'),
    sa.CheckConstraint("(wbgt_state = 'known' AND wbgt_value IS NOT NULL) OR (wbgt_state <> 'known' AND wbgt_value IS NULL)", name='ck_strava_activity_intakes_wbgt_value'),
    sa.CheckConstraint("elevation_loss_state IN ('pending','known','unavailable','failed')", name='ck_strava_activity_intakes_elevation_loss_state'),
    sa.CheckConstraint("sport <> ''", name='ck_strava_activity_intakes_sport'),
    sa.CheckConstraint("status IN ('captured','matched','ambiguous','unmatched','failed')", name='ck_strava_activity_intakes_status'),
    sa.CheckConstraint("wbgt_state IN ('pending','known','unavailable','failed')", name='ck_strava_activity_intakes_wbgt_state'),
    sa.CheckConstraint('activity_id > 0', name='ck_strava_activity_intakes_id'),
    sa.CheckConstraint('elevation_loss_attempts >= 0', name='ck_strava_activity_intakes_elevation_loss_attempts'),
    sa.CheckConstraint('wbgt_attempts >= 0', name='ck_strava_activity_intakes_wbgt_attempts'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_strava_activity_intakes_athlete_id_athletes')),
    sa.ForeignKeyConstraint(['observation_id'], ['redline.observations.id'], name=op.f('fk_strava_activity_intakes_observation_id_observations')),
    sa.PrimaryKeyConstraint('activity_id', name=op.f('pk_strava_activity_intakes')),
    sa.UniqueConstraint('observation_id', name='uq_strava_activity_intakes_observation_id'),
    schema='redline'
    )
    op.create_table('strength_results',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.Column('exercise_slug', sa.Text(), nullable=False),
    sa.Column('difficulty', sa.SmallInteger(), nullable=True),
    sa.Column('skipped', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('completed_slots', sa.SmallInteger(), nullable=True),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.CheckConstraint("status IS NULL OR status IN ('completed','partial','skipped','gated','unknown')", name='ck_strength_results_status'),
    sa.CheckConstraint('NOT skipped OR difficulty IS NULL', name='ck_strength_results_skipped'),
    sa.CheckConstraint('char_length(note) <= 300', name='ck_strength_results_note'),
    sa.CheckConstraint('completed_slots IS NULL OR completed_slots BETWEEN 0 AND 24', name='ck_strength_results_slots'),
    sa.CheckConstraint('difficulty IS NULL OR difficulty BETWEEN 0 AND 10', name='ck_strength_results_difficulty'),
    sa.CheckConstraint('position >= 1', name='ck_strength_results_position'),
    sa.ForeignKeyConstraint(['session_id'], ['redline.strength_sessions.id'], name=op.f('fk_strength_results_session_id_strength_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_strength_results')),
    sa.UniqueConstraint('session_id', 'exercise_slug', name='uq_strength_results_exercise'),
    sa.UniqueConstraint('session_id', 'position', name='uq_strength_results_position'),
    schema='redline'
    )
    op.create_table('plan_gates',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('revision_id', sa.UUID(), nullable=False),
    sa.Column('logical_key', sa.Text(), nullable=False),
    sa.Column('due_on', sa.Date(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('on_fail', sa.Text(), nullable=False),
    sa.Column('applies_when', sa.Enum('avant', 'apres', name='gate_when', schema='redline'), nullable=False),
    sa.Column('holds', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint("(applies_when = 'avant' AND jsonb_typeof(holds) = 'array' AND jsonb_array_length(holds) > 0) OR (applies_when = 'apres' AND holds IS NULL)", name='ck_plan_gates_holds'),
    sa.CheckConstraint("logical_key ~ '^[a-z0-9][a-z0-9-]*$'", name='ck_plan_gates_logical_key'),
    sa.CheckConstraint('length(on_fail) >= 3', name='ck_plan_gates_on_fail'),
    sa.CheckConstraint('length(question) >= 10', name='ck_plan_gates_question'),
    sa.ForeignKeyConstraint(['revision_id'], ['redline.plan_revisions.id'], name=op.f('fk_plan_gates_revision_id_plan_revisions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plan_gates')),
    sa.UniqueConstraint('revision_id', 'logical_key', name='uq_plan_gates_key'),
    schema='redline'
    )
    op.create_table('planned_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('revision_id', sa.UUID(), nullable=False),
    sa.Column('logical_key', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('scheduled_on', sa.Date(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('workout', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('distance_target_km', sa.Numeric(precision=7, scale=2), nullable=True),
    sa.Column('distance_cap_km', sa.Numeric(precision=7, scale=2), nullable=True),
    sa.Column('duration_cap_min', sa.Integer(), nullable=True),
    sa.Column('rpe_min', sa.SmallInteger(), nullable=True),
    sa.Column('rpe_max', sa.SmallInteger(), nullable=True),
    sa.Column('hr_zone', sa.Text(), nullable=True),
    sa.Column('hr_cap_bpm', sa.SmallInteger(), nullable=True),
    sa.Column('cadence_target', sa.SmallInteger(), nullable=True),
    sa.Column('demanding', sa.Boolean(), nullable=False),
    sa.Column('transition', sa.Text(), nullable=True),
    sa.Column('calibration', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('location_label', sa.Text(), nullable=True),
    sa.Column('carbs_target_g_h', sa.SmallInteger(), nullable=True),
    sa.Column('strength_spec', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.CheckConstraint("(workout IN ('renfo-a','renfo-b')) = (strength_spec IS NOT NULL)", name='ck_planned_sessions_strength_owner'),
    sa.CheckConstraint("kind IN ('run','strength','test','rest','bike','hike')", name='ck_planned_sessions_kind'),
    sa.CheckConstraint("logical_key ~ '^[a-z0-9][a-z0-9-]*$'", name='ck_planned_sessions_logical_key'),
    sa.CheckConstraint("status IN ('planned','cancelled')", name='ck_planned_sessions_status'),
    sa.CheckConstraint("strength_spec IS NULL OR jsonb_typeof(strength_spec) = 'object'", name='ck_planned_sessions_strength_spec'),
    sa.CheckConstraint("transition IS NULL OR transition IN ('qualite','descente','nocturne','terrain','reconnaissance','injury')", name='ck_planned_sessions_transition'),
    sa.CheckConstraint("workout IN ('EF','SL','seuil','cotes','lignes','cadence','course','renfo-a','renfo-b','test-mollet','repos','velo','rando')", name='ck_planned_sessions_workout'),
    sa.CheckConstraint('cadence_target IS NULL OR cadence_target BETWEEN 120 AND 220', name='ck_planned_sessions_cadence_target'),
    sa.CheckConstraint('carbs_target_g_h IS NULL OR carbs_target_g_h BETWEEN 0 AND 150', name='ck_planned_sessions_carbs_target'),
    sa.CheckConstraint('distance_cap_km IS NULL OR distance_cap_km >= 0', name='ck_planned_sessions_distance_cap'),
    sa.CheckConstraint('distance_target_km IS NULL OR distance_cap_km IS NULL OR distance_target_km <= distance_cap_km', name='ck_planned_sessions_distance_order'),
    sa.CheckConstraint('distance_target_km IS NULL OR distance_target_km >= 0', name='ck_planned_sessions_distance_target'),
    sa.CheckConstraint('duration_cap_min IS NULL OR duration_cap_min >= 1', name='ck_planned_sessions_duration_cap'),
    sa.CheckConstraint('hr_cap_bpm IS NULL OR hr_cap_bpm BETWEEN 80 AND 220', name='ck_planned_sessions_hr_cap'),
    sa.CheckConstraint('rpe_max IS NULL OR rpe_max BETWEEN 0 AND 10', name='ck_planned_sessions_rpe_max'),
    sa.CheckConstraint('rpe_min IS NULL OR rpe_max IS NULL OR rpe_min <= rpe_max', name='ck_planned_sessions_rpe_order'),
    sa.CheckConstraint('rpe_min IS NULL OR rpe_min BETWEEN 0 AND 10', name='ck_planned_sessions_rpe_min'),
    sa.ForeignKeyConstraint(['revision_id'], ['redline.plan_revisions.id'], name=op.f('fk_planned_sessions_revision_id_plan_revisions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_planned_sessions')),
    sa.UniqueConstraint('revision_id', 'logical_key', name='uq_planned_sessions_key'),
    schema='redline'
    )
    op.create_table('strength_sets',
    sa.Column('result_id', sa.UUID(), nullable=False),
    sa.Column('side', sa.Text(), nullable=False),
    sa.Column('set_index', sa.SmallInteger(), nullable=False),
    sa.Column('repetitions', sa.SmallInteger(), nullable=False),
    sa.CheckConstraint("side IN ('both','right','left')", name='ck_strength_sets_side'),
    sa.CheckConstraint('repetitions BETWEEN 0 AND 999', name='ck_strength_sets_repetitions'),
    sa.CheckConstraint('set_index >= 1', name='ck_strength_sets_index'),
    sa.ForeignKeyConstraint(['result_id'], ['redline.strength_results.id'], name=op.f('fk_strength_sets_result_id_strength_results'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('result_id', 'side', 'set_index', name=op.f('pk_strength_sets')),
    schema='redline'
    )
    op.create_table('planned_session_sources',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.CheckConstraint("source <> ''", name='ck_planned_session_sources_source'),
    sa.CheckConstraint('position >= 1', name='ck_planned_session_sources_position'),
    sa.ForeignKeyConstraint(['session_id'], ['redline.planned_sessions.id'], name=op.f('fk_planned_session_sources_session_id_planned_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('session_id', 'position', name=op.f('pk_planned_session_sources')),
    schema='redline'
    )
    op.create_table('session_cycles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('athlete_id', sa.Text(), nullable=False),
    sa.Column('plan_id', sa.UUID(), nullable=False),
    sa.Column('logical_key', sa.Text(), nullable=False),
    sa.Column('source_session_id', sa.UUID(), nullable=False),
    sa.Column('observation_id', sa.UUID(), nullable=True),
    sa.Column('state', sa.Text(), nullable=False),
    sa.Column('planned_for', sa.DateTime(timezone=True), nullable=True),
    sa.Column('plan_digest', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('declared_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('observed_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.CheckConstraint("logical_key ~ '^[a-z0-9][a-z0-9-]*$'", name='ck_session_cycles_logical_key'),
    sa.CheckConstraint("plan_digest ~ '^[0-9a-f]{64}$'", name='ck_session_cycles_plan_digest'),
    sa.CheckConstraint("state IN ('planned','started','completed','missed','cancelled','followup_due','closed')", name='ck_session_cycles_state'),
    sa.ForeignKeyConstraint(['athlete_id'], ['redline.athletes.id'], name=op.f('fk_session_cycles_athlete_id_athletes')),
    sa.ForeignKeyConstraint(['observation_id'], ['redline.observations.id'], name=op.f('fk_session_cycles_observation_id_observations')),
    sa.ForeignKeyConstraint(['plan_id'], ['redline.weekly_plans.id'], name=op.f('fk_session_cycles_plan_id_weekly_plans'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_session_id'], ['redline.planned_sessions.id'], name=op.f('fk_session_cycles_source_session_id_planned_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_session_cycles')),
    sa.UniqueConstraint('observation_id', name=op.f('uq_session_cycles_observation_id')),
    sa.UniqueConstraint('plan_id', 'logical_key', name='uq_session_cycles_plan_key'),
    schema='redline'
    )
    op.create_index('ix_session_cycles_state_planned_for', 'session_cycles', ['state', 'planned_for'], unique=False, schema='redline')
    op.create_table('session_followups',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('cycle_id', sa.UUID(), nullable=False),
    sa.Column('observation_id', sa.UUID(), nullable=True),
    sa.Column('outcome', sa.Text(), server_default='pending', nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(outcome = 'answered' AND observation_id IS NOT NULL AND resolved_at IS NOT NULL) OR (outcome = 'unanswered' AND observation_id IS NULL AND resolved_at IS NOT NULL) OR (outcome = 'pending' AND observation_id IS NULL AND resolved_at IS NULL)", name='ck_session_followups_resolution'),
    sa.CheckConstraint("outcome IN ('pending','answered','unanswered')", name='ck_session_followups_outcome'),
    sa.ForeignKeyConstraint(['cycle_id'], ['redline.session_cycles.id'], name=op.f('fk_session_followups_cycle_id_session_cycles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['observation_id'], ['redline.observations.id'], name=op.f('fk_session_followups_observation_id_observations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_session_followups')),
    sa.UniqueConstraint('cycle_id', name=op.f('uq_session_followups_cycle_id')),
    sa.UniqueConstraint('observation_id', name=op.f('uq_session_followups_observation_id')),
    schema='redline'
    )
    op.create_table('session_predictions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('cycle_id', sa.UUID(), nullable=False),
    sa.Column('predicted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('predicted_pain', sa.Numeric(precision=3, scale=1), nullable=False),
    sa.Column('predicted_morning_trace', sa.Text(), nullable=False),
    sa.Column('predicted_hr', sa.SmallInteger(), nullable=False),
    sa.Column('validity', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), server_default='messenger', nullable=False),
    sa.CheckConstraint("predicted_morning_trace IN ('aucune','legere','nette')", name='ck_session_predictions_morning_trace'),
    sa.CheckConstraint("validity IN ('pending','valid','late')", name='ck_session_predictions_validity'),
    sa.CheckConstraint('predicted_hr BETWEEN 20 AND 250', name='ck_session_predictions_hr'),
    sa.CheckConstraint('predicted_pain BETWEEN 0 AND 10', name='ck_session_predictions_pain'),
    sa.ForeignKeyConstraint(['cycle_id'], ['redline.session_cycles.id'], name=op.f('fk_session_predictions_cycle_id_session_cycles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_session_predictions')),
    sa.UniqueConstraint('cycle_id', name=op.f('uq_session_predictions_cycle_id')),
    schema='redline'
    )
    connection.execute(sa.text("INSERT INTO redline.athletes (id) VALUES ('sam')"))

    # A known measurement is never rewritten. A pending value may be completed;
    # unavailable may be corrected to known if the source becomes available again.
    known_changes = " OR ".join(
        f"(OLD.{metric}_state = 'known' AND "
        f"(NEW.{metric}_state, NEW.{metric}) IS DISTINCT FROM "
        f"(OLD.{metric}_state, OLD.{metric}))"
        for metric in MEASUREMENTS
    )
    connection.execute(sa.text(f"""
        CREATE FUNCTION redline.protect_measurement_history() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF {known_changes} THEN
            RAISE EXCEPTION 'une mesure connue est immuable; créer une observation correctrice';
          END IF;
          RETURN NEW;
        END;
        $$
    """))
    connection.execute(sa.text("""
        CREATE TRIGGER trg_measurement_history
        BEFORE UPDATE ON redline.observation_metrics
        FOR EACH ROW EXECUTE FUNCTION redline.protect_measurement_history()
    """))

    # Revisions are append-only and strictly contiguous per week.
    connection.execute(sa.text("""
        CREATE FUNCTION redline.protect_plan_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE expected integer;
        BEGIN
          SELECT COALESCE(MAX(revision), 0) + 1 INTO expected
          FROM redline.plan_revisions WHERE plan_id = NEW.plan_id;
          IF NEW.revision <> expected THEN
            RAISE EXCEPTION 'révision attendue %, reçue %', expected, NEW.revision;
          END IF;
          RETURN NEW;
        END;
        $$
    """))
    connection.execute(sa.text("""
        CREATE TRIGGER trg_plan_revision_contiguous
        BEFORE INSERT ON redline.plan_revisions
        FOR EACH ROW EXECUTE FUNCTION redline.protect_plan_revision()
    """))
    connection.execute(sa.text("""
        CREATE FUNCTION redline.forbid_plan_revision_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'une révision de programme est append-only';
        END; $$
    """))
    connection.execute(sa.text("""
        CREATE TRIGGER trg_plan_revision_immutable
        BEFORE UPDATE OR DELETE ON redline.plan_revisions
        FOR EACH ROW EXECUTE FUNCTION redline.forbid_plan_revision_mutation()
    """))

    # The audit is append-only, even if an application call bypasses the repository.
    connection.execute(sa.text("""
        CREATE FUNCTION redline.forbid_audit_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'le journal d audit est append-only';
        END; $$
    """))
    connection.execute(sa.text("""
        CREATE TRIGGER trg_audit_event_immutable
        BEFORE UPDATE OR DELETE ON redline.audit_events
        FOR EACH ROW EXECUTE FUNCTION redline.forbid_audit_mutation()
    """))

    # Default privileges grant DML on future business tables. For the audit log, the
    # only technical table left, the surface is then reduced to exactly what is needed.
    connection.execute(sa.text("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'redline_app') THEN
            GRANT USAGE ON SCHEMA redline, public TO redline_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA redline
              TO redline_app;
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA redline TO redline_app;
            ALTER DEFAULT PRIVILEGES FOR ROLE redline_owner IN SCHEMA redline
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO redline_app;
            ALTER DEFAULT PRIVILEGES FOR ROLE redline_owner IN SCHEMA redline
              GRANT USAGE, SELECT ON SEQUENCES TO redline_app;
            REVOKE UPDATE, DELETE ON redline.audit_events FROM redline_app;
            REVOKE UPDATE, DELETE ON redline.notebook_entries FROM redline_app;
            REVOKE UPDATE, DELETE ON redline.permit_choices FROM redline_app;
            REVOKE DELETE ON redline.projection_states FROM redline_app;
          END IF;
        END $$
    """))

    # The proactive message trace is append-only too.
    op.execute("""
        CREATE FUNCTION redline.forbid_proactive_message_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'la trace des messages proactifs est append-only';
        END; $$
    """)
    op.execute("""
        CREATE TRIGGER trg_proactive_message_immutable
        BEFORE UPDATE OR DELETE ON redline.proactive_messages
        FOR EACH ROW EXECUTE FUNCTION redline.forbid_proactive_message_mutation()
    """)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'redline_app') THEN
            GRANT SELECT, INSERT ON redline.proactive_messages TO redline_app;
            REVOKE UPDATE, DELETE ON redline.proactive_messages FROM redline_app;
          END IF;
        END $$
    """)

    # The snapshot of a delivered strength session is immutable; a revocation is never
    # relaxed.
    op.execute(sa.text("""
        CREATE FUNCTION redline.protect_strength_session_link() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.token_hash IS DISTINCT FROM OLD.token_hash
             OR NEW.psid IS DISTINCT FROM OLD.psid
             OR NEW.session_key IS DISTINCT FROM OLD.session_key
             OR NEW.snapshot IS DISTINCT FROM OLD.snapshot
             OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'le snapshot d une séance livrée est immuable';
          END IF;
          IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
            RAISE EXCEPTION 'la révocation d une séance est irréversible';
          END IF;
          RETURN NEW;
        END; $$
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_strength_session_link_immutable
        BEFORE UPDATE ON redline.strength_session_links
        FOR EACH ROW EXECUTE FUNCTION redline.protect_strength_session_link()
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'redline_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON redline.strength_session_links TO redline_app;
          END IF;
        END $$
    """))


def downgrade() -> None:
    op.execute("DROP SCHEMA redline CASCADE")
