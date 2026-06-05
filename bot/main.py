import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import MessageDatabase

load_dotenv()

MESSAGE_LIMIT = 20

bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))
dp = Dispatcher()
db = MessageDatabase()


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
    """Ручной запуск анализа очереди (для тестирования)"""
    await message.answer("Анализирую очередь сообщений...")
    
    messages = db.get_unprocessed_messages(limit=MESSAGE_LIMIT)
    
    if not messages:
        await message.answer("Очередь пуста. Напишите что-нибудь в чат!")
        return
    
    conversation_text = "\n".join([
        f"[{msg[4]}]: {msg[5]}"
        for msg in messages
    ])
    
    preview = conversation_text[:500] + "..." if len(conversation_text) > 500 else conversation_text
    await message.answer(
        f"Найдено {len(messages)} сообщений:\n\n"
        f"```\n{preview}\n```"
    )
    
    # TODO: Здесь будет вызов LLM и создание задач в Kanban
    # Пока просто помечаем как обработанные
    message_ids = [msg[0] for msg in messages]
    db.mark_as_processed(message_ids)
    
    await message.answer("Сообщения обработаны (заглушка - LLM ещё не подключен)")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Показывает статистику очереди"""
    stats = db.get_stats()
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
        
        db.add_message(
            message_id=message.message_id,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            username=username,
            text=message.text
        )

async def main():
    print("Запуск бота...")
    print(f"База данных: {db.db_path}")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())