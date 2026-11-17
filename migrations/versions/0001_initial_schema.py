"""Initial SQLite schema, including the external-content FTS5 index."""

from alembic import op
from alembic.util import CommandError
from sqlalchemy import inspect

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not op.get_context().as_sql and inspect(op.get_bind()).has_table("chats"):
        raise CommandError("Existing database without a revision. Run python -m scripts.adopt_legacy_db first.")

    op.execute("""
        CREATE TABLE chats (
            id VARCHAR(36) NOT NULL,
            title VARCHAR(255) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (id)
        )
    """)
    op.execute("""
        CREATE TABLE messages (
            id VARCHAR(36) NOT NULL,
            chat_id VARCHAR(36) NOT NULL,
            parent_id VARCHAR(36),
            role VARCHAR(12) NOT NULL,
            content TEXT NOT NULL,
            is_edited BOOLEAN NOT NULL,
            is_active BOOLEAN NOT NULL,
            metadata_json TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME,
            PRIMARY KEY (id),
            CONSTRAINT uq_chat_message_id UNIQUE (chat_id, id),
            FOREIGN KEY(chat_id, parent_id) REFERENCES messages (chat_id, id),
            FOREIGN KEY(chat_id) REFERENCES chats (id) ON DELETE CASCADE
        )
    """)
    op.create_index("idx_messages_chat_id", "messages", ["chat_id"])
    op.create_index("idx_messages_parent_id", "messages", ["parent_id"])
    op.execute("""
        CREATE TABLE branches (
            id VARCHAR(36) NOT NULL,
            chat_id VARCHAR(36) NOT NULL,
            name VARCHAR(64) NOT NULL,
            head_message_id VARCHAR(36),
            last_merged_message_id VARCHAR(36),
            forked_from_id VARCHAR(36),
            status VARCHAR(8) NOT NULL,
            created_at DATETIME NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_chat_branch_name UNIQUE (chat_id, name),
            FOREIGN KEY(chat_id, head_message_id) REFERENCES messages (chat_id, id),
            FOREIGN KEY(chat_id, last_merged_message_id) REFERENCES messages (chat_id, id),
            FOREIGN KEY(chat_id, forked_from_id) REFERENCES messages (chat_id, id),
            FOREIGN KEY(chat_id) REFERENCES chats (id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE TABLE merge_events (
            message_id VARCHAR(36) NOT NULL,
            chat_id VARCHAR(36) NOT NULL,
            source_branch_name VARCHAR(64) NOT NULL,
            forked_from_msg_id VARCHAR(36) NOT NULL,
            compressed_messages_count INTEGER NOT NULL,
            PRIMARY KEY (message_id),
            FOREIGN KEY(chat_id, message_id) REFERENCES messages (chat_id, id) ON DELETE CASCADE,
            FOREIGN KEY(chat_id, forked_from_msg_id) REFERENCES messages (chat_id, id)
        )
    """)
    op.execute("""
        CREATE TABLE message_submissions (
            branch_id VARCHAR(36) NOT NULL,
            "key" VARCHAR(36) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            message_id VARCHAR(36) NOT NULL,
            PRIMARY KEY (branch_id, "key"),
            FOREIGN KEY(branch_id) REFERENCES branches (id) ON DELETE CASCADE,
            FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            content,
            content='messages',
            content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2'
        )
    """)
    op.execute("""
        CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END
    """)
    op.execute("""
        CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
        END
    """)
    op.execute("""
        CREATE TRIGGER messages_au AFTER UPDATE OF content ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
            INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END
    """)


def downgrade() -> None:
    for trigger in ("messages_au", "messages_ad", "messages_ai"):
        op.execute(f"DROP TRIGGER {trigger}")
    for table in ("messages_fts", "message_submissions", "merge_events", "branches", "messages", "chats"):
        op.drop_table(table)
