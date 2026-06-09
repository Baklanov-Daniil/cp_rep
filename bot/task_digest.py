import asyncio
import html
from collections import defaultdict
from datetime import datetime

from aiogram import Bot

from bot.repositories import columns_from_settings, get_cached_tasks_for_digest, get_chat_settings, get_user_id_by_username
from kanban.kanban_client import YouGileClient


def format_deadline(value: datetime | None) -> str:
    if value is None:
        return "без дедлайна"
    return value.strftime("%d.%m %H:%M")


def normalize_assignee(value: str | None) -> str:
    if not value:
        return "Без ответственного"
    return value if value.startswith("@") else f"@{value}"


def format_personal_digest(assignee: str, tasks: list) -> str:
    safe_assignee = html.escape(assignee)
    lines = [
        "📌 <b>Ваши задачи в AI PM</b>",
        f"Ответственный: <b>{safe_assignee}</b>",
        "",
    ]
    for index, task in enumerate(tasks, 1):
        lines.append(f"{index}. <b>{html.escape(task.title)}</b>")
        lines.append(f"   Срок: {format_deadline(task.deadline)}")
    return "\n".join(lines)


def format_group_fallback(assignee: str, tasks: list) -> str:
    header = f"📨 <b>{html.escape(assignee)}</b>, личное сообщение недоступно. Ваши задачи:"
    body = "\n".join(
        f"{index}. <b>{html.escape(task.title)}</b> — {format_deadline(task.deadline)}"
        for index, task in enumerate(tasks, 1)
    )
    return f"{header}\n{body}"


async def get_done_task_ids(settings) -> set[str]:
    columns = columns_from_settings(settings)
    if not settings or not settings.yougile_api_key or not columns["done"]:
        return set()
    kanban = YouGileClient(settings.yougile_api_key)
    done_tasks = await asyncio.to_thread(kanban.get_column_tasks, columns["done"])
    return {task.get("id") for task in done_tasks if task.get("id")}


async def send_task_digest(bot: Bot, chat_id: int) -> dict:
    settings = await get_chat_settings(chat_id)
    if not settings or not settings.yougile_api_key:
        return {"sent": 0, "fallback": 0, "tasks": 0, "error": "not_connected"}

    done_ids = await get_done_task_ids(settings)
    tasks = [
        task
        for task in await get_cached_tasks_for_digest(chat_id)
        if task.id not in done_ids
    ]
    grouped = defaultdict(list)
    for task in tasks:
        grouped[normalize_assignee(task.assigned_to)].append(task)

    sent = 0
    fallback = 0
    for assignee, assignee_tasks in grouped.items():
        if assignee == "Без ответственного":
            await bot.send_message(chat_id, format_group_fallback(assignee, assignee_tasks), parse_mode="HTML")
            fallback += 1
            continue

        user_id = await get_user_id_by_username(chat_id, assignee)
        try:
            if user_id is None:
                raise RuntimeError("unknown Telegram user id")
            await bot.send_message(user_id, format_personal_digest(assignee, assignee_tasks), parse_mode="HTML")
            sent += 1
        except Exception:
            await bot.send_message(chat_id, format_group_fallback(assignee, assignee_tasks), parse_mode="HTML")
            fallback += 1

    return {"sent": sent, "fallback": fallback, "tasks": len(tasks), "error": None}
