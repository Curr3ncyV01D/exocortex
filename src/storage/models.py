import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.storage.database import Base


class MessageRole(str, enum.Enum):
    USER = "user"
    MODEL = "model"
    SYSTEM_MERGE = "system_merge"


class BranchStatus(str, enum.Enum):
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
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
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
    head_message_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("messages.id"),
        nullable=False,
    )
    last_merged_message_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("messages.id"),
        nullable=True,
    )
    forked_from_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("messages.id"),
        nullable=True,
    )
    status: Mapped[BranchStatus] = mapped_column(
        Enum(BranchStatus),
        default=BranchStatus.ACTIVE,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    chat: Mapped["Chat"] = relationship("Chat", back_populates="branches")


class Message(Base):
    """Базовый полиморфный узел направленного ациклического графа (DAG)."""

    __tablename__ = "messages"
    __table_args__ = (
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
        ForeignKey("messages.id"),
        nullable=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole),
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
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )

    chat: Mapped["Chat"] = relationship("Chat", back_populates="messages")
    parent: Mapped[Optional["Message"]] = relationship(
        "Message",
        remote_side=[id],
        backref="children",
    )

    __mapper_args__ = {
        "polymorphic_on": "role",
        "polymorphic_identity": MessageRole.USER,
    }


class ModelMessage(Message):
    """Специализация узла для генераций языковой модели."""

    __mapper_args__ = {
        "polymorphic_identity": MessageRole.MODEL,
    }


class MergeEvent(Message):
    """Полиморфное расширение узла для фиксации факта слияния веток."""

    __tablename__ = "merge_events"

    message_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_branch_name: Mapped[str] = mapped_column(String(64), nullable=False)
    forked_from_msg_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("messages.id"),
        nullable=False,
    )
    compressed_messages_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    __mapper_args__ = {
        "polymorphic_identity": MessageRole.SYSTEM_MERGE,
        "inherit_condition": (message_id == Message.id),
    }