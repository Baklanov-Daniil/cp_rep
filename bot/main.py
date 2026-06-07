import asyncio
import os
import sys
import re
import time
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanban.kanban_client import YouGileClient
from db.database import MessageDatabase
from utils.llm import parse_tasks_from_text

load_dotenv()

YOUGILE_COLUMN_ID = os.getenv('YOUGILE_COLUMN_ID', 'id_твоей_колонки_по_умолчанию')

bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))
dp = Dispatcher()
db = MessageDatabase()
kanban = YouGileClient()

# --- ПЕРЕМЕННЫЕ ДЛЯ РЕАЛТАЙМ НАКОПЛЕНИЯ ---
REALTIME_DELAY = 15  # Пауза тишины в секундах
MAX_CONVERSATION_TIME = 900  # Жесткий лимит непрерывного обсуждения (15 минут)

chat_timers = {}       # {chat_id: asyncio.Task} для таймеров задержки (Debounce)
chat_start_times = {}  # {chat_id: timestamp} для фиксации старта штурма чата

def calculate_msk_deadline(day_marker: str, week_marker: str, time_marker: str) -> str:
    """
    Вычисляет точную дату и время по МСК на основе маркеров от ИИ
    """
    if not day_marker:
        return None

    # Создаем явную таймзону UTC+3 (Москва)
    tz_msk = timezone(timedelta(hours=3))
    now_msk = datetime.now(tz_msk)
    
    # Если ИИ вернул конкретную дату в формате YYYY-MM-DD
    if re.match(r'^\d{4}-\d{2}-\d{2}$', day_marker):
        return f"{day_marker} {time_marker or '18:00'}"

    target_date = now_msk

    if day_marker == "today":
        pass
    elif day_marker == "tomorrow":
        target_date = now_msk + timedelta(days=1)
    else:
        # Расчет по дням недели
        days_of_week = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }
        target_day_num = days_of_week.get(day_marker.lower())
        
        if target_day_num is not None:
            current_day_num = now_msk.weekday()  # 0 = Понедельник, 5 = Суббота...
            
            # Считаем сдвиг до нужного дня на этой неделе
            days_ahead = target_day_num - current_day_num
            
            # Если день уже прошел или он сегодня, переносим на следующую неделю автоматически
            if days_ahead <= 0 and week_marker != "next":
                days_ahead += 7
                
            target_date = now_msk + timedelta(days=days_ahead)

    # Если ИИ явно сказал, что неделя СЛЕДУЮЩАЯ
    if week_marker == "next" and day_marker not in ["today", "tomorrow"]:
        days_to_next_monday = 7 - now_msk.weekday()
        next_monday = now_msk + timedelta(days=days_to_next_monday)
        
        days_of_week = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
        target_day_num = days_of_week.get(day_marker.lower(), 4)
        target_date = next_monday + timedelta(days=target_day_num)

    time_str = time_marker or "18:00"
    return f"{target_date.strftime('%Y-%m-%d')} {time_str}"


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Приветственное сообщение"""
    await message.answer(
        "Привет! Я AI Project Manager.\n\n"
        "Я слушаю этот чат и **автоматически в реальном времени** создаю задачи в YouGile, "
        "как только вы заканчиваете обсуждать вопрос (пауза в диалоге 15 сек) "
        "или принудительно каждые 15 минут бурного штурма.\n\n"
        "Команды:\n"
        "/stats - общая статистика сохраненных сообщений"
    )

async def process_live_conversation(chat_id: int):
    """Фоновая функция: срабатывает, когда пора обработать накопленный контекст"""
    try:
        # Забираем из базы последние необработанные сообщения
        messages = await asyncio.to_thread(db.get_unprocessed_messages, limit=30)
        
        if not messages:
            return

        conversation_text = "\n".join([
            f"[{msg[4]}]: {msg[5]}"
            for msg in messages
        ])
        
        print(f"[LIVE] Анализирую накопленный диалог из {len(messages)} сообщений...")
        
        tasks = await asyncio.to_thread(parse_tasks_from_text, conversation_text)
        
        if tasks:
            print(f"[LIVE] Найдено задач: {len(tasks)}. Отправляю на канбан...")
            
            for task in tasks:
                title = task.get('title', 'Новая задача')
                
                day_m = task.get('deadline_day')
                week_m = task.get('deadline_week', 'current')
                time_m = task.get('deadline_time', '18:00')
                
                deadline = calculate_msk_deadline(day_m, week_m, time_m)
                assignee = task.get('assignee')

                description_parts = []
                if assignee: description_parts.append(f"Исполнитель: {assignee}")
                if deadline: description_parts.append(f"Дедлайн: {deadline}")
                if task.get('priority'): description_parts.append(f"Приоритет: {task.get('priority')}")
                description = "\n".join(description_parts) if description_parts else "Создано автоматически AI PM."
                
                try:
                    await asyncio.to_thread(
                        kanban.create_task, 
                        YOUGILE_COLUMN_ID, 
                        title, 
                        description, 
                        deadline_str=deadline, 
                        assignee_name=assignee
                    )
                except Exception as e:
                    print(f"Ошибка YouGile: {e}")
            
            await bot.send_message(chat_id, f"🤖 **AI PM:** На основе обсуждения автоматически создано задач в YouGile: {len(tasks)}🚀")

        # Помечаем сообщения как обработанные
        message_ids = [msg[0] for msg in messages]
        await asyncio.to_thread(db.mark_as_processed, message_ids)

    except Exception as e:
        print(f"Ошибка в реалтайм обработчике: {e}")
    finally:
        # Очищаем за собой структуры для текущего чата
        if chat_id in chat_timers:
            del chat_timers[chat_id]
        if chat_id in chat_start_times:
            del chat_start_times[chat_id]

async def live_debounce_timer(chat_id: int):
    """Таймер ожидания тишины в чате"""
    await asyncio.sleep(REALTIME_DELAY)
    await process_live_conversation(chat_id)

@dp.message()
async def handle_all_messages(message: Message):
    """Ловит ВСЕ сообщения, сохраняет в базу и управляет реалтайм-таймерами (Debounce + Throttling)"""
    if not message.text or message.text.startswith('/'):
        return

    username = message.from_user.username or message.from_user.first_name
    chat_id = message.chat.id
    current_timestamp = time.time()
    
    # Сохраняем сообщение в базу данных
    await asyncio.to_thread(
        db.add_message,
        message.message_id,
        chat_id,
        message.from_user.id,
        username,
        message.text
    )

    # Защита от долгого обсуждения: если это первое сообщение штурма, фиксируем время старта
    if chat_id not in chat_start_times:
        chat_start_times[chat_id] = current_timestamp

    # Считаем, сколько времени уже идет непрерывный диалог
    elapsed_time = current_timestamp - chat_start_times[chat_id]

    if elapsed_time >= MAX_CONVERSATION_TIME:
        # Лимит времени превышен! Принудительно забираем контекст на анализ
        print(f"[LIVE] Чат штурмуют без остановки уже {int(elapsed_time/60)} мин. Срабатывает принудительный парсинг.")
        
        if chat_id in chat_timers:
            chat_timers[chat_id].cancel()
            
        # Запускаем анализ асинхронно прямо сейчас
        asyncio.create_task(process_live_conversation(chat_id))
    else:
        # Базовый режим: сбрасываем старый таймер ожидания и заводим новый на 15 секунд
        if chat_id in chat_timers:
            chat_timers[chat_id].cancel()

        chat_timers[chat_id] = asyncio.create_task(live_debounce_timer(chat_id))


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Показывает статистику очереди"""
    stats = await asyncio.to_thread(db.get_stats)
    await message.answer(
        f"Статистика очереди:\n"
        f"• Не обработано в текущем диалоге: {stats['unprocessed']}\n"
        f"• Всего обработано сообщений: {stats['processed']}\n"
        f"• Всего в базе: {stats['total']}"
    )

async def main():
    print("Запуск реалтайм-BOTа...")
    print(f"База данных: {db.db_path}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())