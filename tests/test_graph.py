import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.agent.client import map_messages_to_gemini
from src.storage.crud.chats import append_message
from src.storage.crud.graph import (
    BranchAlreadyExistsError,
    BranchNotFoundError,
    GraphIntegrityError,
    InvalidMergeError,
    MessageNotFoundError,
    NoActiveMessagesToMergeError,
    branch_from_here,
    reconstruct_context,
    squash_merge_branch,
    toggle_message_active,
    update_message_content,
)
from src.storage.models import Branch, Chat, MergeEvent, Message, MessageRole
from tests.conftest import create_initial_chat


@pytest.mark.asyncio
async def test_reconstruct_context_linear_order(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка хронологического восстановления цепочки сообщений (Алгоритм 3.1)."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    msg2_id = str(uuid.uuid4())
    msg2 = Message(
        id=msg2_id,
        chat_id=chat_id,
        parent_id=root_id,
        role=MessageRole.MODEL,
        content="Response 1",
    )
    session.add(msg2)

    msg3_id = str(uuid.uuid4())
    msg3 = Message(
        id=msg3_id,
        chat_id=chat_id,
        parent_id=msg2_id,
        role=MessageRole.USER,
        content="Prompt 2",
    )
    session.add(msg3)

    stmt = select(Branch).where(
        Branch.chat_id == chat_id,
        Branch.name == "main",
    )
    branch = (await session.execute(stmt)).scalar_one()
    branch.head_message_id = msg3_id
    await session.commit()

    timeline = await reconstruct_context(chat_id, "main", session)

    assert len(timeline) == 3
    assert [m.id for m in timeline] == [root_id, msg2_id, msg3_id]


@pytest.mark.asyncio
async def test_reconstruct_context_preserves_nodes_and_stored_text(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    msg2_id = str(uuid.uuid4())
    msg2 = Message(
        id=msg2_id,
        chat_id=chat_id,
        parent_id=root_id,
        role=MessageRole.MODEL,
        content="Part 1",
        is_active=True,
    )
    session.add(msg2)

    msg3_id = str(uuid.uuid4())
    msg3 = Message(
        id=msg3_id,
        chat_id=chat_id,
        parent_id=msg2_id,
        role=MessageRole.USER,
        content="Middle prompt",
        is_active=False,
    )
    session.add(msg3)

    msg4_id = str(uuid.uuid4())
    msg4 = Message(
        id=msg4_id,
        chat_id=chat_id,
        parent_id=msg3_id,
        role=MessageRole.MODEL,
        content="Part 2",
        is_active=True,
    )
    session.add(msg4)

    stmt = select(Branch).where(
        Branch.chat_id == chat_id,
        Branch.name == "main",
    )
    branch = (await session.execute(stmt)).scalar_one()
    branch.head_message_id = msg4_id
    await session.commit()

    timeline = await reconstruct_context(chat_id, "main", session)
    contents = map_messages_to_gemini(timeline)
    await session.commit()

    stored_texts = (await session.scalars(
        select(Message.content)
        .where(Message.id.in_([msg2_id, msg4_id]))
        .order_by(Message.content)
    )).all()
    assert stored_texts == ["Part 1", "Part 2"]
    assert [message.id for message in timeline] == [root_id, msg2_id, msg4_id]
    assert [content.role for content in contents] == ["user", "model"]
    assert [part.text for part in contents[1].parts] == ["Part 1\n\nPart 2"]


@pytest.mark.asyncio
async def test_append_leaves_transaction_to_caller(
    file_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with file_sessions() as session:
        setup = await create_initial_chat(session)
        chat_id = setup["chat_id"]
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        chat.title = "Pending title"
        message = await append_message(chat_id, "main", MessageRole.USER, "Pending text", session)
        message_id = message.id
        await session.rollback()

    async with file_sessions() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None and chat.title == "Test Chat"
        assert await session.get(Message, message_id) is None


@pytest.mark.asyncio
async def test_branch_from_here_success(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка корректного создания указателя ветки (Алгоритм 3.2)."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    branch = await branch_from_here(
        chat_id=chat_id,
        target_message_id=root_id,
        new_branch_name="feature/test",
        session=session,
    )

    assert branch.name == "feature/test"
    assert branch.head_message_id == root_id
    assert branch.forked_from_id == root_id


@pytest.mark.asyncio
async def test_branch_from_here_duplicate_name_error(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка запрета на создание дублирующего имени ветки в рамках одного чата."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    with pytest.raises(BranchAlreadyExistsError):
        await branch_from_here(
            chat_id=chat_id,
            target_message_id=root_id,
            new_branch_name="main",
            session=session,
        )


@pytest.mark.asyncio
async def test_update_message_content_ancestor_warning(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]
    await branch_from_here(chat_id, root_id, "side", session)
    await append_message(chat_id, "main", MessageRole.USER, "Only child", session)
    await session.commit()

    result = await update_message_content(
        message_id=root_id,
        new_content="Mutated root",
        force=False,
        session=session,
    )

    assert result["status"] == "warning_required"
    assert result["affected_branch_count"] == 2

    root_msg = await session.get(Message, root_id)
    assert root_msg.content == "Root prompt"
    assert root_msg.is_edited is False


@pytest.mark.asyncio
async def test_update_message_content_force_success(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка принудительной мутации узла при передаче флага force=True."""
    root_id = initial_chat_setup["root_message_id"]
    await branch_from_here(initial_chat_setup["chat_id"], root_id, "side", session)

    result = await update_message_content(
        message_id=root_id,
        new_content="Force edited content",
        force=True,
        session=session,
    )

    assert result["status"] == "success"

    root_msg = await session.get(Message, root_id)
    assert root_msg.content == "Force edited content"
    assert root_msg.is_edited is True
    assert root_msg.updated_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("source,target", [("main", "main"), ("side", "side"), ("side", "other")])
async def test_merge_rejects_invalid_branches(
    session: AsyncSession, initial_chat_setup: dict[str, str], source: str, target: str,
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]
    for name in ("side", "other"):
        await branch_from_here(chat_id, root_id, name, session)
    await append_message(chat_id, source, MessageRole.USER, "Delta", session)
    summarizer = AsyncMock(return_value="Summary")

    with pytest.raises(InvalidMergeError):
        await squash_merge_branch(chat_id, source, target, summarizer, session)

    summarizer.assert_not_awaited()
    assert await session.scalar(select(MergeEvent.id)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("summary", ["", "  \n", None])
async def test_invalid_summary_preserves_delta_for_retry(
    session: AsyncSession, initial_chat_setup: dict[str, str], summary: str | None,
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]
    side = await branch_from_here(chat_id, root_id, "side", session)
    delta = await append_message(chat_id, "side", MessageRole.USER, "Delta", session)

    with pytest.raises(InvalidMergeError):
        await squash_merge_branch(
            chat_id, "side", summarizer_func=AsyncMock(return_value=summary), session=session,
        )

    assert side.last_merged_message_id is None
    assert await session.scalar(select(MergeEvent.id)) is None
    assert [msg.id for msg in await reconstruct_context(chat_id, "main", session)] == [root_id]
    summarizer = AsyncMock(return_value="Valid summary")
    await squash_merge_branch(chat_id, "side", summarizer_func=summarizer, session=session)
    summarizer.assert_awaited_once_with([delta])


@pytest.mark.asyncio
async def test_unmasked_message_before_merge_checkpoint_stays_out_of_delta(
    session: AsyncSession, initial_chat_setup: dict[str, str],
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    await branch_from_here(chat_id, initial_chat_setup["root_message_id"], "side", session)
    old = await append_message(chat_id, "side", MessageRole.USER, "Old", session)
    await toggle_message_active(old.id, session)
    await append_message(chat_id, "side", MessageRole.USER, "First visible", session)
    summary = AsyncMock(return_value="Summary")
    await squash_merge_branch(chat_id, "side", summarizer_func=summary, session=session)

    await toggle_message_active(old.id, session)
    assert old.id in {msg.id for msg in await reconstruct_context(chat_id, "side", session)}
    new = await append_message(chat_id, "side", MessageRole.USER, "New", session)
    summary.reset_mock()
    await squash_merge_branch(chat_id, "side", summarizer_func=summary, session=session)
    summary.assert_awaited_once_with([new])


@pytest.mark.asyncio
@pytest.mark.parametrize("link", ["head_message_id", "parent_id"])
async def test_context_rejects_existing_cross_chat_link(
    session: AsyncSession, initial_chat_setup: dict[str, str], link: str,
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    foreign_root = (await create_initial_chat(session))["root_message_id"]
    # Воспроизводим некорректную запись, оставленную схемой без составных ключей.
    await session.execute(text("PRAGMA foreign_keys = OFF"))
    if link == "head_message_id":
        await session.execute(
            text("UPDATE branches SET head_message_id = :foreign_root WHERE chat_id = :chat_id"),
            {"foreign_root": foreign_root, "chat_id": chat_id},
        )
    else:
        await session.execute(
            text("UPDATE messages SET parent_id = :foreign_root WHERE chat_id = :chat_id"),
            {"foreign_root": foreign_root, "chat_id": chat_id},
        )
    await session.commit()
    await session.execute(text("PRAGMA foreign_keys = ON"))

    with pytest.raises(GraphIntegrityError):
        await reconstruct_context(chat_id, "main", session)


@pytest.mark.asyncio
async def test_append_rejects_system_merge_role(
    session: AsyncSession, initial_chat_setup: dict[str, str],
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    with pytest.raises(ValueError):
        await append_message(chat_id, "main", MessageRole.SYSTEM_MERGE, "Incomplete merge", session)
    assert [msg.id for msg in await reconstruct_context(chat_id, "main", session)] == [
        initial_chat_setup["root_message_id"]
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("link", ["parent_id", "head_message_id", "forked_from_id",
                                  "last_merged_message_id", "forked_from_msg_id"])
async def test_db_rejects_cross_chat_graph_links(
    session: AsyncSession, initial_chat_setup: dict[str, str], link: str,
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]
    foreign_root = (await create_initial_chat(session))["root_message_id"]
    if link == "parent_id":
        row = Message(chat_id=chat_id, parent_id=foreign_root, role=MessageRole.USER, content="Text")
    elif link == "forked_from_msg_id":
        row = MergeEvent(
            chat_id=chat_id, parent_id=root_id, role=MessageRole.SYSTEM_MERGE,
            content="Summary", source_branch_name="side", forked_from_msg_id=foreign_root,
            compressed_messages_count=1,
        )
    else:
        pointers = {"head_message_id": root_id, link: foreign_root}
        row = Branch(chat_id=chat_id, name="side", **pointers)
    session.add(row)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


@pytest.mark.asyncio
async def test_toggle_message_active_invariants(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка тумблера видимости и инварианта неизменности updated_at (Алгоритм 3.5)."""
    root_id = initial_chat_setup["root_message_id"]

    new_state = await toggle_message_active(root_id, session)
    assert new_state is False

    msg = await session.get(Message, root_id)
    assert msg.is_active is False
    assert msg.updated_at is None

    new_state = await toggle_message_active(root_id, session)
    assert new_state is True


@pytest.mark.asyncio
async def test_squash_merge_branch_incremental(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка инкрементального сжатия ветки и создания MergeEvent (Алгоритм 3.4)."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    branch = await branch_from_here(
        chat_id=chat_id,
        target_message_id=root_id,
        new_branch_name="feature/sandbox",
        session=session,
    )

    msg_b1_id = str(uuid.uuid4())
    msg_b1 = Message(
        id=msg_b1_id,
        chat_id=chat_id,
        parent_id=root_id,
        role=MessageRole.USER,
        content="Sub-idea 1",
    )
    session.add(msg_b1)

    msg_b2_id = str(uuid.uuid4())
    msg_b2 = Message(
        id=msg_b2_id,
        chat_id=chat_id,
        parent_id=msg_b1_id,
        role=MessageRole.MODEL,
        content="Sub-idea 2",
    )
    session.add(msg_b2)

    branch.head_message_id = msg_b2_id
    await session.commit()

    mock_summarizer = AsyncMock(return_value="Synthesized conclusion of sandbox")

    merge_result = await squash_merge_branch(
        chat_id=chat_id,
        source_branch_name="feature/sandbox",
        target_branch_name="main",
        summarizer_func=mock_summarizer,
        session=session,
    )

    assert merge_result["status"] == "success"

    stmt_main = select(Branch).where(
        Branch.chat_id == chat_id,
        Branch.name == "main",
    )
    main_branch = (await session.execute(stmt_main)).scalar_one()
    merge_node_id = main_branch.head_message_id

    merge_msg = await session.get(Message, merge_node_id)
    assert merge_msg.role == MessageRole.SYSTEM_MERGE
    assert merge_msg.content == "Synthesized conclusion of sandbox"
    assert merge_msg.parent_id == root_id

    merge_event = await session.get(MergeEvent, merge_node_id)
    assert merge_event is not None
    assert merge_event.source_branch_name == "feature/sandbox"
    assert merge_event.compressed_messages_count == 2

    stmt_source = select(Branch).where(
        Branch.chat_id == chat_id,
        Branch.name == "feature/sandbox",
    )
    source_branch = (await session.execute(stmt_source)).scalar_one()
    assert source_branch.last_merged_message_id == msg_b2_id


@pytest.mark.asyncio
async def test_squash_merge_empty_delta_fails(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка запрета на слияние ветки без новых сообщений."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    await branch_from_here(
        chat_id=chat_id,
        target_message_id=root_id,
        new_branch_name="feature/empty",
        session=session,
    )

    mock_summarizer = AsyncMock()

    with pytest.raises(NoActiveMessagesToMergeError):
        await squash_merge_branch(
            chat_id=chat_id,
            source_branch_name="feature/empty",
            target_branch_name="main",
            summarizer_func=mock_summarizer,
            session=session,
        )


@pytest.mark.asyncio
async def test_squash_merge_second_incremental_merge(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка второго инкрементального слияния: сжатию подлежит строго новая дельта."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    branch = await branch_from_here(
        chat_id=chat_id,
        target_message_id=root_id,
        new_branch_name="feature/long-lived",
        session=session,
    )

    msg_b1_id = str(uuid.uuid4())
    msg_b2_id = str(uuid.uuid4())
    session.add_all([
        Message(id=msg_b1_id, chat_id=chat_id, parent_id=root_id, role=MessageRole.USER, content="Step 1"),
        Message(id=msg_b2_id, chat_id=chat_id, parent_id=msg_b1_id, role=MessageRole.MODEL, content="Step 2"),
    ])
    branch.head_message_id = msg_b2_id
    await session.commit()

    mock_summarizer_1 = AsyncMock(return_value="Merge Summary 1")
    res1 = await squash_merge_branch(
        chat_id=chat_id,
        source_branch_name="feature/long-lived",
        target_branch_name="main",
        summarizer_func=mock_summarizer_1,
        session=session,
    )
    merge1_id = res1["merge_message_id"]

    msg_b3_id = str(uuid.uuid4())
    msg_b4_id = str(uuid.uuid4())
    session.add_all([
        Message(id=msg_b3_id, chat_id=chat_id, parent_id=msg_b2_id, role=MessageRole.USER, content="Step 3"),
        Message(id=msg_b4_id, chat_id=chat_id, parent_id=msg_b3_id, role=MessageRole.MODEL, content="Step 4"),
    ])
    branch.head_message_id = msg_b4_id
    await session.commit()

    mock_summarizer_2 = AsyncMock(return_value="Merge Summary 2")
    res2 = await squash_merge_branch(
        chat_id=chat_id,
        source_branch_name="feature/long-lived",
        target_branch_name="main",
        summarizer_func=mock_summarizer_2,
        session=session,
    )

    passed_delta = mock_summarizer_2.call_args.args[0]
    assert [m.id for m in passed_delta] == [msg_b3_id, msg_b4_id]
    assert res2["compressed_messages_count"] == 2

    merge2_msg = await session.get(Message, res2["merge_message_id"])
    assert merge2_msg.parent_id == merge1_id

    await session.refresh(branch)
    assert branch.last_merged_message_id == msg_b4_id


@pytest.mark.asyncio
async def test_reconstruct_context_preserves_consecutive_user_messages(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка сохранения подряд идущих реплик пользователя без их слияния."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    u2_id = str(uuid.uuid4())
    u3_id = str(uuid.uuid4())
    m1_id = str(uuid.uuid4())

    session.add_all([
        Message(id=u2_id, chat_id=chat_id, parent_id=root_id, role=MessageRole.USER, content="Дополнение 1"),
        Message(id=u3_id, chat_id=chat_id, parent_id=u2_id, role=MessageRole.USER, content="Дополнение 2"),
        Message(id=m1_id, chat_id=chat_id, parent_id=u3_id, role=MessageRole.MODEL, content="Ответ модели"),
    ])

    stmt = select(Branch).where(Branch.chat_id == chat_id, Branch.name == "main")
    main_branch = (await session.execute(stmt)).scalar_one()
    main_branch.head_message_id = m1_id
    await session.commit()

    timeline = await reconstruct_context(chat_id, "main", session)

    assert len(timeline) == 4
    assert [m.role for m in timeline] == [
        MessageRole.USER,
        MessageRole.USER,
        MessageRole.USER,
        MessageRole.MODEL,
    ]


@pytest.mark.asyncio
async def test_branch_from_here_cross_chat_isolation(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка запрета на ветвление в одном чате от сообщения из другого чата."""
    root_id_chat_a = initial_chat_setup["root_message_id"]
    chat_b_id = str(uuid.uuid4())

    session.add(Chat(id=chat_b_id, title="Isolated Chat B"))
    await session.commit()

    with pytest.raises(MessageNotFoundError):
        await branch_from_here(
            chat_id=chat_b_id,
            target_message_id=root_id_chat_a,
            new_branch_name="hijack-branch",
            session=session,
        )


@pytest.mark.asyncio
async def test_reconstruct_context_includes_merge_event_polymorphism(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка корректной полиморфной загрузки MergeEvent внутри истории ветки."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    merge_id = str(uuid.uuid4())
    u2_id = str(uuid.uuid4())

    merge_node = MergeEvent(
        id=merge_id,
        chat_id=chat_id,
        parent_id=root_id,
        role=MessageRole.SYSTEM_MERGE,
        content="Сжатый итог ветки",
        source_branch_name="side-quest",
        forked_from_msg_id=root_id,
        compressed_messages_count=5,
    )
    session.add(merge_node)
    await session.flush()

    session.add(
        Message(id=u2_id, chat_id=chat_id, parent_id=merge_id, role=MessageRole.USER, content="Идем дальше")
    )

    stmt = select(Branch).where(Branch.chat_id == chat_id, Branch.name == "main")
    main_branch = (await session.execute(stmt)).scalar_one()
    main_branch.head_message_id = u2_id
    await session.commit()

    timeline = await reconstruct_context(chat_id, "main", session)

    assert len(timeline) == 3
    assert isinstance(timeline[1], MergeEvent)
    assert timeline[1].source_branch_name == "side-quest"
