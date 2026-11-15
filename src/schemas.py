import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, AliasChoices, BaseModel, ConfigDict, Field, field_validator


BranchName = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$"),
]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


class RoleEnum(str, Enum):
    USER = "user"
    MODEL = "model"
    SYSTEM_MERGE = "system_merge"


class BranchStatusEnum(str, Enum):
    ACTIVE = "active"
    MERGED = "merged"
    ARCHIVED = "archived"


class ChatCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class ChatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: UtcDatetime
    updated_at: UtcDatetime


class BranchCreateRequest(BaseModel):
    name: BranchName
    target_message_id: str


class BranchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chat_id: str
    name: str
    head_message_id: str | None
    forked_from_id: str | None
    last_merged_message_id: str | None
    status: BranchStatusEnum
    created_at: UtcDatetime


class BranchMergeRequest(BaseModel):
    target_branch: Literal["main"] = "main"


class BranchMergeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: Literal["success"] = "success"
    merge_message_id: str
    source_branch: str
    target_branch: Literal["main"]
    compressed_messages_count: int
    summary: str


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, content: str) -> str:
        if not content.strip():
            raise ValueError("Сообщение должно содержать текст.")
        return content


class RunTurnRequest(BaseModel):
    expected_head_id: str | None = None


class MessageUpdateContentRequest(SendMessageRequest):
    force: bool = False


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chat_id: str
    parent_id: str | None
    role: RoleEnum
    content: str
    is_edited: bool
    is_active: bool
    metadata: dict[str, Any] | None = Field(
        default=None, validation_alias=AliasChoices("metadata_json", "metadata"),
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime | None

    @field_validator("metadata", mode="before")
    @classmethod
    def decode_metadata(cls, value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value)
        return value


class RegenerateReplyRequest(BaseModel):
    expected_head_id: UUID
    new_branch_name: BranchName


class RegenerateReplyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    branch: BranchResponse
    message: MessageResponse


class ToggleActiveResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: str
    is_active: bool


class AncestorMutationWarningResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    detail: Literal["ANCESTOR_MUTATION_WARNING"] = "ANCESTOR_MUTATION_WARNING"
    affected_branch_count: int
    message: str


class ErrorResponse(BaseModel):
    detail: str


class RegenerationFailureResponse(ErrorResponse):
    branch_id: str
    head_message_id: str
