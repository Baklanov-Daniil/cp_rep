import asyncio
import os
import sys
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import MessageDatabase
from utils.llm import parse_tasks_from_text
from utils.meeting_recorder import MeetingRecorder

load_dotenv()

MESSAGE_LIMIT = 20

bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))
dp = Dispatcher()
db = MessageDatabase()
recorder = MeetingRecorder(output_dir="recordings")


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Приветственное сообщение"""
    await message.answer(
        "🤖 **Привет! Я AI Project Manager.**\n\n"
        "Я анализирую диалог в группе и автоматически создаю задачи.\n\n"
        "**Команды:**\n"
        "/parse - проанализировать очередь сообщений\n"
        "/stats - статистика очереди\n"
        "/join <ссылка> [мин] - подключиться к встрече и записать\n"
        "/test - проверка работы бота"
    )


@dp.message(Command("test"))
async def cmd_test(message: Message):
    """Тестовая команда"""
    print("🔔 КОМАНДА /test ПОЛУЧЕНА!")
    await message.answer("✅ Бот работает! Команда получена.")


@dp.message(Command("parse"))
async def cmd_parse(message: Message):
    """Ручной запуск анализа очереди"""
    await message.answer("🔍 **Анализирую очередь сообщений...**")
    
    messages = await asyncio.to_thread(db.get_unprocessed_messages, MESSAGE_LIMIT)

    if not messages:
        await message.answer("📭 **Очередь пуста.** Напишите что-нибудь в чат!")
        return

    conversation_text = "\n".join([
        f"[{msg[4]}]: {msg[5]}"
        for msg in messages
    ])

    print(f"Отправляю в LLM {len(messages)} сообщений:")
    print(conversation_text[:200] + "...")

    await message.answer("🤖 **Отправляю в AI для анализа...**")
    tasks = await asyncio.to_thread(parse_tasks_from_text, conversation_text)

    if not tasks:
        await message.answer("❌ **Задач не найдено в сообщениях.**")
        message_ids = [msg[0] for msg in messages]
        await asyncio.to_thread(db.mark_as_processed, message_ids)
        return

    response_text = f"📝 **Найдено задач: {len(tasks)}**\n\n"
    for i, task in enumerate(tasks, 1):
        response_text += f"📌 **Задача #{i}:** {task.get('title')}\n"
        if task.get('assignee'):
            response_text += f"   👤 {task.get('assignee')}\n"
        if task.get('deadline'):
            response_text += f"   📅 {task.get('deadline')}\n"
        if task.get('priority'):
            priority_emoji = {"high": "🔥", "medium": "⚡", "low": "💤"}.get(task.get('priority'), "⚪")
            response_text += f"   {priority_emoji} {task.get('priority')}\n"
        response_text += "\n"

    message_ids = [msg[0] for msg in messages]
    await asyncio.to_thread(db.mark_as_processed, message_ids)

    await message.answer(response_text, parse_mode="Markdown")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Показывает статистику очереди"""
    stats = await asyncio.to_thread(db.get_stats)
    await message.answer(
        f"📋 **Статистика очереди:**\n"
        f"• В текущем обсуждении (не обработано): {stats['unprocessed']}\n"
        f"• Всего создано задач/сообщений: {stats['processed']}\n"
        f"• Всего записей в логах: {stats['total']}"
    )


@dp.message(Command("join"))
async def cmd_join_meeting(message: Message):
    """Подключиться к встрече и записать"""
    print(f"🔔 Получена команда /join от {message.from_user.username}")
    print(f"Текст: {message.text}")
    
    args = message.text.split()
    
    if len(args) < 2:
        await message.answer(
            "❌ **Неверный формат!**\n\n"
            "**Использование:**\n"
            "/join <ссылка> [минуты]\n\n"
            "**Пример:**\n"
            "/join https://telemost.yandex.ru/... 15"
        )
        return
    
    meeting_url = args[1]
    duration = int(args[2]) if len(args) > 2 else 10
    
    status_msg = await message.answer(
        f"🔗 **Подключаюсь к встрече...**\n"
        f"📅 Длительность: {duration} мин\n"
        f"🎙️ Запись будет отправлена после завершения\n\n"
        f"⏳ Это займёт некоторое время..."
    )
    
    try:
        print(f"🔍 DEBUG: URL={meeting_url}, duration={duration}")
        filepath = await recorder.connect_and_record(meeting_url, duration)
        print(f"✅ DEBUG: filepath={filepath}")
        
        if filepath and isinstance(filepath, str) and os.path.exists(filepath):
            await status_msg.edit_text("✅ **Запись завершена! Отправляю файл...**")
            
            await message.answer_document(
                document=FSInputFile(filepath),
                caption=f"🎙️ **Запись встречи завершена!**\n"
                       f"📁 {Path(filepath).name}\n\n"
                       f"Длительность: {duration} мин"
            )
        else:
            await status_msg.edit_text("❌ **Ошибка:** файл не создан")
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        await status_msg.edit_text(f"❌ **Ошибка:** {str(e)[:200]}")


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
    print("[SERVER] AI Project Manager бот успешно запущен и слушает события...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())