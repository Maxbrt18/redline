"""Les natures de transition viennent du règlement de l'instance, plus de la base.

`ck_planned_sessions_transition` énumérait les six natures que le moteur connaissait.
Elles appartiennent désormais à `rulebook.toml` et se vérifient à l'écriture du
programme : la contrainte ne garde que la forme d'un slug.

Le retour en arrière remet la liste d'origine ; il échoue si une séance porte une nature
qu'elle ne contient pas, plutôt que d'effacer la nature de cette séance.

Revision ID: 20261027_0003
Revises: 20261026_0002
Create Date: 2026-10-27
"""

from __future__ import annotations

from alembic import op

revision = "20261027_0003"
down_revision = "20261026_0002"
branch_labels = None
depends_on = None

NAME = "ck_planned_sessions_transition"
SLUG = "transition IS NULL OR transition ~ '^[a-z0-9][a-z0-9-]{0,39}$'"
LISTED = ("transition IS NULL OR transition IN "
          "('qualite','descente','nocturne','terrain','reconnaissance','injury')")


def upgrade() -> None:
    op.drop_constraint(NAME, "planned_sessions", type_="check", schema="redline")
    op.create_check_constraint(NAME, "planned_sessions", SLUG, schema="redline")


def downgrade() -> None:
    op.drop_constraint(NAME, "planned_sessions", type_="check", schema="redline")
    op.create_check_constraint(NAME, "planned_sessions", LISTED, schema="redline")
