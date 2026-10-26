"""Crée le stockage opérationnel au complet, en une seule révision.

L'édition open source part du schéma cible au lieu de copier la chronique du
déploiement : les tables et contraintes viennent de `scripts.storage.schema`, la
même déclaration que `alembic check` compare à la base. Les fonctions et triggers
portent les garanties qui ne s'expriment pas en déclaration : mesures connues
immuables, révisions de programme contiguës et append-only, audit et messages
proactifs non mutables, snapshot d'une séance livrée figé. Les privilèges ne
s'installent que si les rôles `redline_owner` / `redline_app` existent déjà.

Revision ID: 20261026_0001
Revises: None
Create Date: 2026-10-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from scripts.storage.schema import MEASUREMENT_DEFINITIONS, athletes, metadata

revision = "20261026_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("CREATE SCHEMA IF NOT EXISTS redline"))
    metadata.create_all(bind=connection, checkfirst=False)
    connection.execute(athletes.insert().values(id="sam"))

    # Une mesure connue ne se réécrit jamais. Une valeur pending peut être complétée ;
    # unavailable peut être corrigée en known si la source redevient disponible.
    known_changes = " OR ".join(
        f"(OLD.{metric}_state = 'known' AND "
        f"(NEW.{metric}_state, NEW.{metric}) IS DISTINCT FROM "
        f"(OLD.{metric}_state, OLD.{metric}))"
        for metric in MEASUREMENT_DEFINITIONS
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

    # Les révisions sont append-only et strictement contiguës par semaine.
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

    # L'audit est append-only, y compris si un appel applicatif contourne le repository.
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

    # Les privilèges par défaut donnent le DML aux futures tables métier. Pour le
    # journal d'audit, seule table technique encore présente, on réduit ensuite
    # précisément la surface nécessaire.
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

    # La trace des messages proactifs est append-only, elle aussi.
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

    # Le snapshot d'une séance de renfo livrée est immuable ; une révocation
    # ne se relâche jamais.
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
