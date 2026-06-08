import asyncio
import os
import sys
import re
import time
import json
from datetime import datetime, timedelta, timezone
import requests
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, WebAppInfo
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from dotenv import load_dotenv

# Настройка путей (если модули лежат в других папках)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanban.kanban_client import YouGileClient
from db.database import MessageDatabase
from utils.llm import parse_tasks_from_text

# Загружаем переменные из .env
load_dotenv()

# Ссылка на ваше веб-окно авторизации (HTML-форма)
WEB_APP_URL = "https://jolly-flan-a04ec.netlify.app/"  

bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))
dp = Dispatcher()
db = MessageDatabase()
kanban = YouGileClient()

# --- ТАЙМЕРЫ И НАСТРОЙКИ НАКОПЛЕНИЯ КОНТЕКСТА ---
REALTIME_DELAY = 15  # Пауза тишины в секундах (Debounce)
MAX_CONVERSATION_TIME = 900  # Принудительный парсинг каждые 15 минут штурма

chat_timers = {}       
chat_start_times = {}  

# Глобальный динамический кэш ID колонок (после генерации структуры)
COLUMNS_CACHE = {
    "participants": None,
    "no_deadline": None,
    "has_deadline": None,
    "urgent_deadline": None,
    "done": None
}

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def calculate_msk_deadline(day_marker: str, week_marker: str, time_marker: str) -> datetime:
    """Вычисляет дедлайн и возвращает объект datetime с таймзоной МСК"""
    if not day_marker:
        return None

    tz_msk = timezone(timedelta(hours=3))
    now_msk = datetime.now(tz_msk)
    
    if re.match(r'^\d{4}-\d{2}-\d{2}$', day_marker):
        try:
            dt = datetime.strptime(f"{day_marker} {time_marker or '18:00'}", '%Y-%m-%d %H:%M')
            return dt.replace(tzinfo=tz_msk)
        except:
            return None

    target_date = now_msk

    if day_marker == "today":
        pass
    elif day_marker == "tomorrow":
        target_date = now_msk + timedelta(days=1)
    else:
        days_of_week = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
        target_day_num = days_of_week.get(day_marker.lower())
        if target_day_num is not None:
            current_day_num = now_msk.weekday()
            days_ahead = target_day_num - current_day_num
            if days_ahead <= 0 and week_marker != "next":
                days_ahead += 7
            target_date = now_msk + timedelta(days=days_ahead)

    if week_marker == "next" and day_marker not in ["today", "tomorrow"]:
        days_to_next_monday = 7 - now_msk.weekday()
        next_monday = now_msk + timedelta(days=days_to_next_monday)
        days_of_week = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
        target_day_num = days_of_week.get(day_marker.lower(), 4)
        target_date = next_monday + timedelta(days=target_day_num)

    time_str = time_marker or "18:00"
    try:
        hour, minute = map(int, time_str.split(':'))
        return target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except:
        return target_date.replace(hour=18, minute=0, second=0, microsecond=0)


def create_yougile_structure(api_key: str, company_id: str) -> bool:
    """Создает в YouGile новый проект, новую доску и 5 колонок по ТЗ"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 1. Создаем именно НОВЫЙ изолированный проект
    project_payload = {"title": "AI Управление Проектом (Авто)"}
    p_resp = requests.post("https://ru.yougile.com/api-v2/projects", json=project_payload, headers=headers)
    
    if p_resp.status_code not in [200, 201]:
        print(f"[API Ошибка] Проект: {p_resp.text}")
        return False
    project_id = p_resp.json().get('id')

    # 2. Создаем Доску внутри проекта
    board_payload = {"title": "Канбан AI PM", "projectId": project_id}
    b_resp = requests.post("https://ru.yougile.com/api-v2/boards", json=board_payload, headers=headers)
    
    if b_resp.status_code not in [200, 201]:
        print(f"[API Ошибка] Доска: {b_resp.text}")
        return False
    board_id = b_resp.json().get('id')

    # 3. Создаем 5 необходимых колонок подряд
    columns_to_create = [
        "1. Участники проекта",
        "2. Без дедлайна",
        "3. Дедлайн есть",
        "4. Дедлайн менее 2 дней",
        "5. Выполнение задачи"
    ]
    
    created_columns = []
    for col_title in columns_to_create:
        c_payload = {"title": col_title, "boardId": board_id, "deleted": False}
        c_resp = requests.post("https://ru.yougile.com/api-v2/columns", json=c_payload, headers=headers)
        if c_resp.status_code in [200, 201]:
            created_columns.append(c_resp.json().get('id'))
            
    if len(created_columns) == 4: # Если 5 колонок успешно созданы
        COLUMNS_CACHE["participants"] = created_columns[0]
        COLUMNS_CACHE["no_deadline"] = created_columns[1]
        COLUMNS_CACHE["has_deadline"] = created_columns[2]
        COLUMNS_CACHE["urgent_deadline"] = created_columns[3]
        COLUMNS_CACHE["done"] = created_columns[4]
        return True
    return False


def get_telegram_username_from_kanban(assignee_name: str, api_key: str) -> str:
    """Парсит 1-ю колонку, ищет ФИО сотрудника и вытаскивает регуляркой его @username_tg"""
    if not assignee_name or not COLUMNS_CACHE["participants"]:
        return None
        
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"https://ru.yougile.com/api-v2/tasks?columnId={COLUMNS_CACHE['participants']}"
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            tasks = response.json().get('content', [])
            for t in tasks:
                title = t.get('title', '')
                if assignee_name.lower() in title.lower():
                    match = re.search(r'@\w+', title)
                    if match:
                        return match.group(0)
    except Exception as e:
        print(f"[Кэш Юзеров] Ошибка поиска: {e}")
    return None

# --- ХЭНДЛЕРЫ КОМАНД И СТРУКТУРЫ ---

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Приветствие"""
    await message.answer(
        "🤖 **Привет! Я AI Project Manager.**\n\n"
        "Я анализирую диалог в группе и автоматически создаю задачи по дедлайнам.\n\n"
        "**Команды:**\n"
        "/get_key — Открыть окно настройки и авторизации YouGile\n"
        "/stats — Посмотреть состояние очереди сообщений"
    )

@dp.message(Command("get_key"))
async def cmd_get_key(message: Message):
    """Отправляет специальную WebApp кнопку, открывающую внешнее окно"""
    try: await message.delete()
    except: pass

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Вход в YouGile (Приватное окно)", web_app=WebAppInfo(url=WEB_APP_URL))]
    ])

    await message.answer(
        "Выдаю модальное окно авторизации.\n"
        "Нажмите кнопку ниже. Ваш ввод данных **будет скрыт от истории чата**.",
        reply_markup=keyboard
    )

@dp.message(F.web_app_data)
async def process_web_app_data(message: Message, state: FSMContext):
    """Принимает логин и пароль из закрывшегося Web App окна"""
    try:
        raw_data = message.web_app_data.data
        data = json.loads(raw_data)
        
        login = data.get("login")
        password = data.get("password")
        
        status_msg = await message.answer("⏳ Безопасное подключение установлено. Авторизуюсь в YouGile...")
        
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        companies_url = "https://ru.yougile.com/api-v2/auth/companies"
        
        # Первая попытка авторизации
        payload = {"login": login, "password": password}
        response = await asyncio.to_thread(requests.post, companies_url, json=payload, headers=headers)
        if response.status_code == 401:
            payload = {"email": login, "password": password}
            response = await asyncio.to_thread(requests.post, companies_url, json=payload, headers=headers)

        if response.status_code not in [200, 201]:
            await status_msg.edit_text("🔴 Ошибка входа! Неверный логин или пароль в окне. Повторите /get_key")
            return

        companies_data = response.json()
        companies_list = companies_data.get("content", companies_data.get("data", [companies_data])) if isinstance(companies_data, dict) else companies_data

        # Записываем данные в состояние FSM для следующего шага
        await state.update_data(login=login, password=password)
        
        # Генерируем кнопки выбора компаний прямо в чате
        keyboard_builder = [[InlineKeyboardButton(text=c.get("name", "Без названия"), callback_data=f"setup_{c.get('id')}")] for c in companies_list]
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard_builder)
        
        await status_msg.delete()
        await message.answer(
            f"🍏 **Успешный вход!**\nАккаунт: `{login}`\n\n"
            f"Выберите компанию. Бот автоматически развернет структуру из 5 необходимых колонок:", 
            reply_markup=markup
        )
        
    except Exception as e:
        await message.answer(f"🔴 Ошибка разбора данных из Web App: {e}")

@dp.callback_query(F.data.startswith("setup_"))
async def process_setup(callback: CallbackQuery, state: FSMContext):
    """Генерация API ключа и автоматическое создание всей структуры доски"""
    company_id = callback.data.replace("setup_", "")
    user_data = await state.get_data()
    
    await callback.message.edit_text("⏳ Генерирую бессрочный токен и разворачиваю 5 колонок...")
    
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    keys_url = "https://ru.yougile.com/api-v2/auth/keys"
    payload = {"login": user_data['login'], "password": user_data['password'], "companyId": company_id}
    
    res = await asyncio.to_thread(requests.post, keys_url, json=payload, headers=headers)
    if res.status_code == 401:
        payload["email"] = payload.pop("login")
        res = await asyncio.to_thread(requests.post, keys_url, json=payload, headers=headers)

    if res.status_code in [200, 201]:
        api_key = res.json().get("key") or res.json().get("token") or res.json().get("apiKey")
        
        # Вызываем функцию создания проекта, доски и 5 колонок
        success = await asyncio.to_thread(create_yougile_structure, api_key, company_id)
        
        if success:
            # Пишем токены в переменные окружения
            os.environ["YOUGILE_API_KEY"] = api_key
            os.environ["YOUGILE_COMPANY_ID"] = company_id
            
            text = (
                f"🚀 **СТРУКТУРА ДЛЯ AI ГОТОВА!**\n\n"
                f"На вашем аккаунте создан проект 'AI Управление Проектом (Авто)'.\n"
                f"**Инструкция:** Зайдите в 1-й столбец ('Участники проекта') и создайте карточки людей в формате: `Иван Иванов (@username_tg)`.\n\n"
                f"**Конфигурация .env сохранена:**\n"
                f"```env\n"
                f"YOUGILE_API_KEY={api_key}\n"
                f"YOUGILE_COMPANY_ID={company_id}\n"
                f"```"
            )
            await callback.message.edit_text(text, parse_mode="Markdown")
        else:
            await callback.message.edit_text("🔴 Не удалось инициализировать проект и колонки. Проверьте права вашего аккаунта.")
    else:
        await callback.message.edit_text("🔴 Ошибка генерации API токена YouGile.")
    
    await state.clear()
    await callback.answer()

# --- СИСТЕМА УМНОГО АНАЛИЗА ДИАЛОГА И РАСПРЕДЕЛЕНИЯ ПО КОЛОНКАМ ---

async def process_live_conversation(chat_id: int):
    """Вытаскивает сообщения из БД, отправляет в ИИ и раскидывает задачи по нужным колонкам"""
    try:
        api_key = os.getenv("YOUGILE_API_KEY")
        if not api_key or not COLUMNS_CACHE["no_deadline"]:
            return

        messages = await asyncio.to_thread(db.get_unprocessed_messages, limit=30)
        if not messages: return

        # Собираем лог переписки для LLM
        conversation_text = "\n".join([f"[{msg[4]}]: {msg[5]}" for msg in messages])
        tasks = await asyncio.to_thread(parse_tasks_from_text, conversation_text)
        
        if tasks:
            for task in tasks:
                title = task.get('title', 'Новая задача')
                assignee_name = task.get('assignee')
                
                # Ищем тег @username_tg исполнителя в первой колонке YouGile
                tg_username = await asyncio.to_thread(get_telegram_username_from_kanban, assignee_name, api_key)
                
                # Форматируем название задачи: ставим @username_tg в самое начало по ТЗ
                prefix = f"{tg_username} " if tg_username else ""
                final_title = f"{prefix}{title}"
                
                # Рассчитываем дедлайн задачи
                day_m = task.get('deadline_day')
                week_m = task.get('deadline_week', 'current')
                time_m = task.get('deadline_time', '18:00')
                
                deadline_dt = calculate_msk_deadline(day_m, week_m, time_m)
                
                # ЛОГИКА СОРТИРОВКИ ПО СТОЛБЦАМ:
                target_column = COLUMNS_CACHE["no_deadline"]  # Столбец 2 (по умолчанию - нет дедлайна)
                deadline_str_arg = None
                
                if deadline_dt:
                    deadline_str_arg = deadline_dt.strftime('%Y-%m-%d %H:%M')
                    tz_msk = timezone(timedelta(hours=3))
                    now_msk = datetime.now(tz_msk)
                    time_diff = deadline_dt - now_msk
                    
                    if time_diff <= timedelta(days=2):
                        target_column = COLUMNS_CACHE["urgent_deadline"]  # Столбец 4 (менее 2 дней)
                    else:
                        target_column = COLUMNS_CACHE["has_deadline"]     # Столбец 3 (дедлайн есть)

                description = f"Исполнитель: {assignee_name or 'Не назначен'}\nПриоритет выставлен AI-ботом."
                
                # Создаем задачу в динамически определенном столбце YouGile
                try:
                    await asyncio.to_thread(
                        kanban.create_task, 
                        target_column, 
                        final_title, 
                        description, 
                        deadline_str=deadline_str_arg, 
                        assignee_name=assignee_name
                    )
                except Exception as e:
                    print(f"Ошибка создания задачи в YouGile: {e}")
            
            await bot.send_message(chat_id, f"🤖 **AI PM:** Распознал контекст диалога. Успешно распределено новых задач по столбцам: {len(tasks)} 🚀")

        # Помечаем сообщения в базе как отработанные
        await asyncio.to_thread(db.mark_as_processed, [msg[0] for msg in messages])

    except Exception as e:
        print(f"Ошибка в основном конвейере: {e}")
    finally:
        if chat_id in chat_timers: del chat_timers[chat_id]
        if chat_id in chat_start_times: del chat_start_times[chat_id]

async def live_debounce_timer(chat_id: int):
    """Ожидание паузы тишины в чате"""
    await asyncio.sleep(REALTIME_DELAY)
    await process_live_conversation(chat_id)

@dp.message()
async def handle_all_messages(message: Message):
    """Слушает все сообщения группы, логгирует в базу и запускает таймеры сборщика задач"""
    if not message.text or message.text.startswith('/'):
        return

    username = message.from_user.username or message.from_user.first_name
    chat_id = message.chat.id
    current_timestamp = time.time()
    
    # Сохраняем сообщение в локальную БД для накопления контекста
    await asyncio.to_thread(db.add_message, message.message_id, chat_id, message.from_user.id, username, message.text)

    if chat_id not in chat_start_times:
        chat_start_times[chat_id] = current_timestamp

    elapsed_time = current_timestamp - chat_start_times[chat_id]

    if elapsed_time >= MAX_CONVERSATION_TIME:
        # Если штурмуют чат без остановки больше 15 минут — парсим принудительно часть контекста
        if chat_id in chat_timers: chat_timers[chat_id].cancel()
        asyncio.create_task(process_live_conversation(chat_id))
    else:
        # Стандартный сброс таймаута тишины (Debounce 15 секунд)
        if chat_id in chat_timers: chat_timers[chat_id].cancel()
        chat_timers[chat_id] = asyncio.create_task(live_debounce_timer(chat_id))


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Вывод внутренней статистики сообщений бота"""
    stats = await asyncio.to_thread(db.get_stats)
    await message.answer(
        f"📋 **Статистика очереди:**\n"
        f"• В текущем обсуждении (не обработано): {stats['unprocessed']}\n"
        f"• Всего создано задач/сообщений: {stats['processed']}\n"
        f"• Всего записей в логах: {stats['total']}"
    )

# --- ТОЧКА ВХОДА ЗАПУСКА ---
async def main():
    print("[SERVER] AI Project Manager бот успешно запущен и слушает события...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())