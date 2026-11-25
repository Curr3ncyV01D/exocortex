"""Record the actual source HEAD for new merge events without guessing legacy data."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("merge_events", recreate="always") as batch:
        batch.add_column(sa.Column("source_head_message_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_merge_source_head", "messages", ["chat_id", "source_head_message_id"], ["chat_id", "id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("merge_events", recreate="always") as batch:
        batch.drop_constraint("fk_merge_source_head", type_="foreignkey")
        batch.drop_column("source_head_message_id")
