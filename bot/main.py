import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import MessageDatabase
from db.task_db import TaskDatabase
from utils.llm import parse_tasks_from_text
from utils.date_parser import parse_deadline, format_deadline_for_display

load_dotenv()

MESSAGE_LIMIT = 20
bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))
dp = Dispatcher()
msg_db = MessageDatabase()
task_db = TaskDatabase()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Приветственное сообщение"""
    await message.answer(
        "Привет! Я AI Project Manager.\n\n"
        "Я слушаю этот чат и автоматически создаю задачи.\n"
        "Команды:\n"
        "/parse - проанализировать очередь сообщений\n"
        "/tasks - показать все задачи\n"
        "/stats - статистика очереди"
    )


@dp.message(Command("parse"))
async def cmd_parse(message: Message):
    """Ручной запуск анализа очереди"""
    await message.answer("🔍 Анализирую очередь сообщений...")
    
    messages = await asyncio.to_thread(msg_db.get_unprocessed_messages, MESSAGE_LIMIT)

    if not messages:
        await message.answer("📭 Очередь пуста. Напишите что-нибудь в чат!")
        return

    conversation_text = "\n".join([
        f"[{msg[4]}]: {msg[5]}"
        for msg in messages
    ])

    print(f"Отправляю в LLM {len(messages)} сообщений:")
    print(conversation_text[:200] + "...")

    await message.answer("🤖 Отправляю в AI для анализа...")
    tasks = await asyncio.to_thread(parse_tasks_from_text, conversation_text)

    if not tasks:
        await message.answer("❌ Задач не найдено в сообщениях.")
        message_ids = [msg[0] for msg in messages]
        await asyncio.to_thread(msg_db.mark_as_processed, message_ids)
        return

    # Сохраняем задачи в БД
    saved_tasks = []
    for task in tasks:
        # Обрабатываем дедлайн
        deadline = None
        if task.get('deadline'):
            deadline = parse_deadline(task.get('deadline'))
        
        # Сохраняем в БД
        task_id = await asyncio.to_thread(
            task_db.add_task,
            title=task.get('title', 'Без названия'),
            description='',
            assignee_name=task.get('assignee'),
            deadline=deadline,
            priority=task.get('priority', 'medium'),
            source_message_id=messages[0][1] if messages else None,
            chat_id=message.chat.id
        )
        saved_tasks.append((task_id, task, deadline))

    # Помечаем сообщения как обработанные
    message_ids = [msg[0] for msg in messages]
    await asyncio.to_thread(msg_db.mark_as_processed, message_ids)

    # Формируем ответ
    response_text = f"✅ **Найдено задач: {len(saved_tasks)}**\n\n"
    
    for task_id, task, deadline in saved_tasks:
        response_text += f" **Задача #{task_id}:** {task.get('title')}\n"
        if task.get('assignee'):
            response_text += f"   👤 {task.get('assignee')}\n"
        if deadline:
            formatted_date = format_deadline_for_display(deadline)
            response_text += f"   📅 {formatted_date}\n"
        else:
            response_text += f"   📅 Дедлайн не указан\n"
        
        priority = task.get('priority', 'medium')
        priority_emoji = {"high": "🔥", "medium": "⚡", "low": "💤"}.get(priority, "⚪")
        response_text += f"   {priority_emoji} {priority}\n\n"

    await message.answer(response_text, parse_mode="Markdown")


@dp.message(Command("tasks"))
async def cmd_tasks(message: Message):
    """Показать все задачи"""
    tasks = await asyncio.to_thread(task_db.get_all_tasks)

    if not tasks:
        await message.answer("📭 Задач пока нет.")
        return

    response_text = f"📋 **Всего задач: {len(tasks)}**\n\n"

    for task in tasks:
        status_emoji = {"new": "🆕", "done": "✅", "cancelled": "❌"}.get(task['status'], "📌")
        
        response_text += f"{status_emoji} **#{task['id']}:** {task['title']}\n"
        if task.get('assignee_name'):
            response_text += f"   👤 {task['assignee_name']}\n"
        if task.get('deadline'):
            formatted_date = format_deadline_for_display(task['deadline'])
            response_text += f"   📅 {formatted_date}\n"
        
        priority = task.get('priority', 'medium')
        priority_emoji = {"high": "🔥", "medium": "⚡", "low": "💤"}.get(priority, "⚪")
        response_text += f"   {priority_emoji} {priority}\n\n"

    await message.answer(response_text, parse_mode="Markdown")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Показывает статистику очереди"""
    msg_stats = await asyncio.to_thread(msg_db.get_stats)
    task_stats = await asyncio.to_thread(task_db.get_stats)
    
    await message.answer(
        f"📊 **Статистика:**\n\n"
        f"💬 **Сообщения:**\n"
        f"  • В очереди: {msg_stats['unprocessed']}\n"
        f"  • Обработано: {msg_stats['processed']}\n\n"
        f"📋 **Задачи:**\n"
        f"  • Новые: {task_stats['new']}\n"
        f"  • Всего: {task_stats['total']}"
    )


@dp.message()
async def handle_all_messages(message: Message):
    """Сохраняет ВСЕ текстовые сообщения в очередь"""
    if message.text and not message.text.startswith('/'):
        username = message.from_user.username or message.from_user.first_name
        await asyncio.to_thread(
            msg_db.add_message,
            message.message_id,
            message.chat.id,
            message.from_user.id,
            username,
            message.text
        )


async def main():
    print("Запуск бота...")
    print(f"База данных сообщений: {msg_db.db_path}")
    print(f"База данных задач: {task_db.db_path}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())