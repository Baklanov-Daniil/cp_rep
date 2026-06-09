from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from bot.deadlines import calculate_msk_deadline, choose_task_column
from bot.repositories import (
    cache_created_task,
    clear_pending_messages,
    columns_from_settings,
    get_chat_settings,
    load_pending_messages,
)
from kanban.kanban_client import YouGileClient
from utils.llm import parse_tasks_from_text


@dataclass
class SessionStats:
    processed_messages_count: int = 0
    created_tasks_count: int = 0


def username_from_message_user(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.first_name or str(user.id)


def render_conversation(messages: list[dict]) -> str:
    return "\n".join(f"[{message['username']}]: {message['text']}" for message in messages)


def make_task_title(title: str, tg_username: str | None) -> str:
    return f"{tg_username} {title}" if tg_username else title


def make_task_description(task: dict, assignee_name: str | None) -> str:
    return (
        f"Исполнитель: {assignee_name or 'Не назначен'}\n"
        f"Приоритет: {task.get('priority', 'medium')}\n"
        "Создано AI PM автоматически."
    )


async def ensure_participants(kanban: YouGileClient, participants_column: str | None, participants: list[dict]) -> None:
    if not participants_column:
        return
    for person in participants:
        name = (person.get("full_name") or "").strip()
        if not name:
            continue
        await __to_thread(
            kanban.ensure_participant_card,
            participants_column,
            name,
            person.get("tg_username"),
        )


async def create_yougile_task(
    chat_id: int,
    kanban: YouGileClient,
    columns: dict[str, str | None],
    raw_task: dict,
) -> bool:
    parts_col = columns["participants"]
    title = raw_task.get("title") or "Новая задача"
    assignee_name = raw_task.get("assignee")
    tg_username = None

    if assignee_name and parts_col:
        tg_username = await __to_thread(kanban.get_tg_username_for_assignee, assignee_name, parts_col)

    deadline = calculate_msk_deadline(
        raw_task.get("deadline_day"),
        raw_task.get("deadline_week", "current"),
        raw_task.get("deadline_time", "18:00"),
    )
    column_id = choose_task_column(deadline, columns)
    if not column_id:
        return False

    final_title = make_task_title(title, tg_username)
    deadline_str = deadline.strftime("%Y-%m-%d %H:%M") if deadline else None
    created = await __to_thread(
        kanban.create_task,
        column_id,
        final_title,
        make_task_description(raw_task, assignee_name),
        deadline_str=deadline_str,
        assignee_name=assignee_name,
        parts_col=parts_col,
    )

    if created and created.get("id"):
        await cache_created_task(
            task_id=created["id"],
            chat_id=chat_id,
            title=final_title,
            assigned_to=tg_username or assignee_name,
            deadline=deadline,
        )

    return bool(created)


async def process_conversation(chat_id: int, bot: Bot, stats: SessionStats) -> None:
    settings = await get_chat_settings(chat_id)
    api_key = settings.yougile_api_key if settings else None
    if not api_key:
        print(f"[AI PM] chat_id={chat_id}: YouGile API key не задан.")
        return

    columns = columns_from_settings(settings)
    if not all(columns.values()):
        print(f"[AI PM] chat_id={chat_id}: колонки не инициализированы.")
        return

    messages = await load_pending_messages(chat_id)
    if not messages:
        return

    stats.processed_messages_count += len(messages)
    tasks, participants = await __to_thread(parse_tasks_from_text, render_conversation(messages))
    kanban = YouGileClient(api_key)

    await ensure_participants(kanban, columns["participants"], participants)
    if not tasks:
        await clear_pending_messages(chat_id)
        return

    created_count = 0
    for raw_task in tasks:
        try:
            if await create_yougile_task(chat_id, kanban, columns, raw_task):
                created_count += 1
        except Exception as exc:
            print(f"[AI PM] chat_id={chat_id}: ошибка создания задачи: {exc}")

    stats.created_tasks_count += created_count
    await clear_pending_messages(chat_id)

    if created_count:
        await bot.send_message(
            chat_id,
            f"🤖 <b>AI PM:</b> создал <b>{created_count}</b> задач в YouGile 🚀\n"
            "Автомиграция задач по дедлайну работает в фоне.",
            parse_mode="HTML",
        )


async def __to_thread(func, *args, **kwargs):
    import asyncio

    return await asyncio.to_thread(func, *args, **kwargs)
