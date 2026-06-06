import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanban.kanban_client import YouGileClient
from db.database import MessageDatabase
from utils.llm import parse_tasks_from_text

load_dotenv()

MESSAGE_LIMIT = 20
# Лучше вынести ID колонки YouGile в .env, чтобы не хардкодить в коде
YOUGILE_COLUMN_ID = os.getenv('YOUGILE_COLUMN_ID', 'id_твоей_колонки_по_умолчанию')

bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))
dp = Dispatcher()
db = MessageDatabase()
kanban = YouGileClient()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Приветственное сообщение"""
    await message.answer(
        "Привет! Я AI Project Manager.\n\n"
        "Я слушаю этот чат и автоматически создаю задачи.\n"
        "Команды:\n"
        "/parse - проанализировать очередь сообщений\n"
        "/stats - статистика очереди"
    )

@dp.message(Command("parse"))
async def cmd_parse(message: Message):
    """Ручной запуск анализа очереди"""
    await message.answer("Анализирую очередь сообщений...")
    
    messages = await asyncio.to_thread(db.get_unprocessed_messages, MESSAGE_LIMIT)
    
    if not messages:
        await message.answer("📭 Очередь пуста. Напишите что-нибудь в чат!")
        return
    
    conversation_text = "\n".join([
        f"[{msg[4]}]: {msg[5]}"
        f"[{msg[4]}]: {msg[5]}"
        for msg in messages
    ])
    
    print(f"Отправляю в LLM {len(messages)} сообщений:")
    print(conversation_text[:200] + "...")
    
    await message.answer("Отправляю в AI для анализа...")
    tasks = await asyncio.to_thread(parse_tasks_from_text, conversation_text)
    
    if not tasks:
        await message.answer("Задач не найдено в сообщениях.")
        message_ids = [msg[0] for msg in messages]
        await asyncio.to_thread(db.mark_as_processed, message_ids)
        return
    
    response_text = f"📝 **Найдено задач: {len(tasks)}**\n\n"
    
    # --- ИНТЕГРАЦИЯ С KANBAN ---
    created_tasks_count = 0
    
    for i, task in enumerate(tasks, 1):
        title = task.get('title', 'Новая задача')
        
        # Формируем описание для карточки в YouGile из метаданных LLM
        description_parts = []
        if task.get('assignee'):
            description_parts.append(f"Исполнитель: {task.get('assignee')}")
        if task.get('deadline'):
            description_parts.append(f"Дедлайн: {task.get('deadline')}")
        if task.get('priority'):
            description_parts.append(f"Приоритет: {task.get('priority')}")
        
        description = "\n".join(description_parts) if description_parts else "Создано автоматически AI PM."

        try:
            # Отправляем в YouGile внутри отдельного потока
            await asyncio.to_thread(kanban.create_task, YOUGILE_COLUMN_ID, title, description)
            created_tasks_count += 1
        except Exception as e:
            print(f"Ошибка при создании задачи '{title}' в YouGile: {e}")
            
        # Красиво оформляем ответ в Telegram
        response_text += f"📌 **Задача {i}:** {title}\n"
        if task.get('assignee'):
            response_text += f"   👤 {task.get('assignee')}\n"
        if task.get('deadline'):
            response_text += f"   📅 {task.get('deadline')}\n"
        if task.get('priority'):
            priority_emoji = {"high": "🔥", "medium": "⚡", "low": "💤"}.get(task.get('priority'), "⚪")
            response_text += f"   {priority_emoji} {task.get('priority')}\n"
        response_text += "\n"
    
    response_text += f"🚀 Экспортировано в YouGile: {created_tasks_count} из {len(tasks)}"
    # ---------------------------
    
    message_ids = [msg[0] for msg in messages]
    await asyncio.to_thread(db.mark_as_processed, message_ids)
    
    await message.answer(response_text, parse_mode="Markdown")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Показывает статистику очереди"""
    stats = await asyncio.to_thread(db.get_stats)
    await message.answer(
        f"Статистика очереди:\n"
        f"• Не обработано: {stats['unprocessed']}\n"
        f"• Обработано: {stats['processed']}\n"
        f"• Всего: {stats['total']}"
    )

@dp.message()
async def handle_all_messages(message: Message):
    """Сохраняет ВСЕ текстовые сообщения в очередь"""
    if message.text and not message.text.startswith('/'):
        username = message.from_user.username or message.from_user.first_name
        
        await asyncio.to_thread(
            db.add_message,
            message.message_id,
            message.chat.id,
            message.from_user.id,
            username,
            message.text
        )

async def main():
    print("Запуск бота...")
    print(f"База данных: {db.db_path}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
