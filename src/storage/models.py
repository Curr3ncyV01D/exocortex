import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.storage.database import Base


class MessageRole(str, Enum):
    USER = "user"
    MODEL = "model"
    SYSTEM_MERGE = "system_merge"


class BranchStatus(str, Enum):
    ACTIVE = "active"
    MERGED = "merged"
    ARCHIVED = "archived"


class Chat(Base):
    """Контейнер изолированного диалогового пространства."""

    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    branches: Mapped[list["Branch"]] = relationship(
        "Branch",
        back_populates="chat",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="chat",
        cascade="all, delete-orphan",
    )


class Branch(Base):
    """Именованный указатель на текущую вершину графа диалога."""

    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint("chat_id", "name", name="uq_chat_branch_name"),
        ForeignKeyConstraint(
            ["chat_id", "head_message_id"], ["messages.chat_id", "messages.id"],
        ),
        ForeignKeyConstraint(
            ["chat_id", "last_merged_message_id"], ["messages.chat_id", "messages.id"],
        ),
        ForeignKeyConstraint(
            ["chat_id", "forked_from_id"], ["messages.chat_id", "messages.id"],
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    chat_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    head_message_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True,
    )
    last_merged_message_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True,
    )
    forked_from_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True,
    )
    status: Mapped[BranchStatus] = mapped_column(
        SQLEnum(BranchStatus),
        default=BranchStatus.ACTIVE,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    chat: Mapped["Chat"] = relationship("Chat", back_populates="branches")
    head_message: Mapped[Optional["Message"]] = relationship(
        "Message",
        foreign_keys=[head_message_id],
    )


class Message(Base):
    """Базовый полиморфный узел направленного ациклического графа (DAG)."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "id", name="uq_chat_message_id"),
        ForeignKeyConstraint(
            ["chat_id", "parent_id"], ["messages.chat_id", "messages.id"],
        ),
        Index("idx_messages_parent_id", "parent_id"),
        Index("idx_messages_chat_id", "chat_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    chat_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        SQLEnum(MessageRole),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_edited: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )

    chat: Mapped["Chat"] = relationship("Chat", back_populates="messages")
    parent: Mapped[Optional["Message"]] = relationship(
        "Message",
        foreign_keys=[parent_id],
        remote_side=[id],
        backref="children",
    )

    __mapper_args__ = {
        "polymorphic_on": "role",
        "polymorphic_identity": MessageRole.USER,
        "with_polymorphic": "*",
    }


class ModelMessage(Message):
    """Специализация узла для генераций языковой модели."""

    __mapper_args__ = {
        "polymorphic_identity": MessageRole.MODEL,
    }


class MergeEvent(Message):
    """Полиморфное расширение узла для фиксации факта слияния веток."""

    __tablename__ = "merge_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chat_id", "message_id"], ["messages.chat_id", "messages.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["chat_id", "forked_from_msg_id"], ["messages.chat_id", "messages.id"],
        ),
    )

    message_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )
    merge_chat_id: Mapped[str] = mapped_column("chat_id", String(36), nullable=False)
    source_branch_name: Mapped[str] = mapped_column(String(64), nullable=False)
    forked_from_msg_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
    )
    compressed_messages_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    __mapper_args__ = {
        "polymorphic_identity": MessageRole.SYSTEM_MERGE,
        "inherit_condition": and_(
            message_id == Message.id, merge_chat_id == Message.chat_id,
        ),
    }
