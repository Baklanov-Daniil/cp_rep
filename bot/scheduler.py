import asyncio

from aiogram import Bot

from bot.repositories import columns_from_settings, get_overdue_tasks, list_chat_settings, mark_tasks_notified
from kanban.kanban_client import YouGileClient


async def notify_overdue_tasks(bot: Bot, settings, kanban: YouGileClient, columns: dict[str, str | None]) -> None:
    overdue_tasks = await get_overdue_tasks(settings.chat_id)
    if not overdue_tasks:
        return

    done_ids = {
        task.get("id")
        for task in await asyncio.to_thread(kanban.get_column_tasks, columns["done"])
    }

    notified_ids = []
    for task in overdue_tasks:
        if task.id in done_ids:
            notified_ids.append(task.id)
            continue

        assignee = f" {task.assigned_to}" if task.assigned_to else ""
        await bot.send_message(settings.chat_id, f"⚠️{assignee} Задача «{task.title}» просрочена.")
        notified_ids.append(task.id)

    await mark_tasks_notified(notified_ids)


async def monitor_deadlines(bot: Bot, interval_seconds: int) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            settings_list = await list_chat_settings()
            for settings in settings_list:
                columns = columns_from_settings(settings)
                if not settings.yougile_api_key or not all(columns.values()):
                    continue

                kanban = YouGileClient(settings.yougile_api_key)
                moved_count = await asyncio.to_thread(kanban.migrate_tasks_by_deadline, columns)
                if moved_count:
                    print(f"[Миграция] chat_id={settings.chat_id}, перемещено задач: {moved_count}")

                await notify_overdue_tasks(bot, settings, kanban, columns)
        except Exception as exc:
            print(f"[Миграция] Ошибка: {exc}")

        await asyncio.sleep(interval_seconds)
