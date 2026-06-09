from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from db.database import AsyncSessionMaker
from db.models import ChatMember, ChatSettings, PendingMessage, TaskCache


EMPTY_COLUMNS: dict[str, str | None] = {
    "participants": None,
    "no_deadline": None,
    "has_deadline": None,
    "urgent_deadline": None,
    "done": None,
}


def columns_from_settings(settings: ChatSettings | None) -> dict[str, str | None]:
    if settings is None:
        return EMPTY_COLUMNS.copy()
    return {
        "participants": settings.col_participants,
        "no_deadline": settings.col_no_deadline,
        "has_deadline": settings.col_has_deadline,
        "urgent_deadline": settings.col_urgent,
        "done": settings.col_done,
    }


async def get_chat_settings(chat_id: int) -> ChatSettings | None:
    async with AsyncSessionMaker() as session:
        return await session.get(ChatSettings, chat_id)


def is_chat_connected(settings: ChatSettings | None) -> bool:
    if settings is None or not settings.yougile_api_key:
        return False
    return all(columns_from_settings(settings).values())


async def list_chat_settings() -> list[ChatSettings]:
    async with AsyncSessionMaker() as session:
        result = await session.execute(select(ChatSettings))
        return list(result.scalars().all())


async def save_board_settings(chat_id: int, api_key: str, project_id: str, board_result: dict) -> None:
    columns = board_result["columns"]
    async with AsyncSessionMaker() as session:
        settings = await session.get(ChatSettings, chat_id)
        if settings is None:
            settings = ChatSettings(chat_id=chat_id)
            session.add(settings)

        settings.yougile_api_key = api_key
        settings.project_id = project_id
        settings.board_id = board_result["board_id"]
        settings.col_participants = columns["participants"]
        settings.col_no_deadline = columns["no_deadline"]
        settings.col_has_deadline = columns["has_deadline"]
        settings.col_urgent = columns["urgent_deadline"]
        settings.col_done = columns["done"]
        await session.commit()


async def save_pending_message(chat_id: int, username: str, text: str) -> None:
    async with AsyncSessionMaker() as session:
        session.add(PendingMessage(chat_id=chat_id, username=username, text=text))
        await session.commit()


async def upsert_chat_member(chat_id: int, user_id: int, username: str | None, role: str = "user") -> None:
    async with AsyncSessionMaker() as session:
        member = await session.get(ChatMember, (chat_id, user_id))
        if member is None:
            member = ChatMember(chat_id=chat_id, user_id=user_id, role=role, is_authorized=True)
            session.add(member)
        member.username = username
        if role == "admin":
            member.role = "admin"
            member.is_authorized = True
        await session.commit()


async def load_pending_messages(chat_id: int) -> list[dict]:
    async with AsyncSessionMaker() as session:
        result = await session.execute(
            select(PendingMessage)
            .where(PendingMessage.chat_id == chat_id)
            .order_by(PendingMessage.timestamp, PendingMessage.id)
        )
        return [{"username": row.username, "text": row.text} for row in result.scalars().all()]


async def clear_pending_messages(chat_id: int) -> None:
    async with AsyncSessionMaker() as session:
        await session.execute(delete(PendingMessage).where(PendingMessage.chat_id == chat_id))
        await session.commit()


async def pending_count(chat_id: int) -> int:
    async with AsyncSessionMaker() as session:
        result = await session.execute(
            select(func.count()).select_from(PendingMessage).where(PendingMessage.chat_id == chat_id)
        )
        return int(result.scalar_one() or 0)


async def cache_created_task(
    task_id: str,
    chat_id: int,
    title: str,
    assigned_to: str | None,
    deadline: datetime | None,
) -> None:
    async with AsyncSessionMaker() as session:
        await session.merge(TaskCache(
            id=task_id,
            chat_id=chat_id,
            title=title,
            assigned_to=assigned_to,
            deadline=deadline.replace(tzinfo=None) if deadline else None,
        ))
        await session.commit()


async def get_cached_tasks_for_digest(chat_id: int) -> list[TaskCache]:
    async with AsyncSessionMaker() as session:
        result = await session.execute(
            select(TaskCache)
            .where(TaskCache.chat_id == chat_id)
            .order_by(TaskCache.assigned_to, TaskCache.deadline)
        )
        return list(result.scalars().all())


async def get_user_id_by_username(chat_id: int, username: str | None) -> int | None:
    if not username:
        return None
    normalized = username if username.startswith("@") else f"@{username}"
    raw = username.lstrip("@")
    async with AsyncSessionMaker() as session:
        result = await session.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat_id,
                ChatMember.username.in_([normalized, raw]),
            )
        )
        member = result.scalar_one_or_none()
        return member.user_id if member else None


async def get_overdue_tasks(chat_id: int) -> list[TaskCache]:
    now_msk = datetime.now(timezone(timedelta(hours=3))).replace(tzinfo=None)
    async with AsyncSessionMaker() as session:
        result = await session.execute(
            select(TaskCache).where(
                TaskCache.chat_id == chat_id,
                TaskCache.deadline.is_not(None),
                TaskCache.deadline <= now_msk,
                TaskCache.is_notified.is_(False),
            )
        )
        return list(result.scalars().all())


async def mark_tasks_notified(task_ids: list[str]) -> None:
    if not task_ids:
        return
    async with AsyncSessionMaker() as session:
        result = await session.execute(select(TaskCache).where(TaskCache.id.in_(task_ids)))
        for task in result.scalars().all():
            task.is_notified = True
        await session.commit()
