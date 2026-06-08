"""
Telegram-бот AI PM для автоматизации YouGile.
Полная версия с исправленными ошибками.
"""

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
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    WebAppInfo,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    BotCommand,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanban.kanban_client import YouGileClient
from utils.llm import parse_tasks_from_text

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

load_dotenv()

WEB_APP_URL = "https://jolly-flan-a04ec.netlify.app/"

REALTIME_DELAY = 15       # секунд ожидания тишины перед обработкой
MAX_CONVERSATION_TIME = 900  # секунд максимальной длины одной сессии

# ---------------------------------------------------------------------------
# Глобальные объекты приложения
# ---------------------------------------------------------------------------

bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
dp = Dispatcher()

# Создаём клиент YouGile лениво — через фабрику, чтобы подхватывать свежий ключ
def _make_kanban() -> YouGileClient:
    return YouGileClient()

kanban: YouGileClient = _make_kanban()

# ---------------------------------------------------------------------------
# In-memory хранилища
# ---------------------------------------------------------------------------

# {chat_id: [{"username": str, "text": str}, ...]}
chat_buffers: dict[int, list[dict]] = {}

# {chat_id: asyncio.Task}  — дебаунс-таймеры
chat_timers: dict[int, asyncio.Task] = {}

# {chat_id: float}  — время начала текущей сессии
chat_start_times: dict[int, float] = {}

SESSION_STATS = {
    "processed_messages_count": 0,
    "created_tasks_count": 0,
}

# Кэш ID колонок после развёртывания доски
COLUMNS_CACHE: dict[str, str | None] = {
    "participants": None,
    "no_deadline": None,
    "has_deadline": None,
    "urgent_deadline": None,
    "done": None,
}

# ---------------------------------------------------------------------------
# FSM-состояния авторизации
# ---------------------------------------------------------------------------

class AuthStates(StatesGroup):
    waiting_for_company = State()
    waiting_for_project = State()

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def update_env_file(key: str, value: str) -> None:
    """
    Обновляет переменную в файле .env (создаёт файл, если его нет).
    Сразу же устанавливает значение в os.environ, чтобы текущий процесс
    подхватил изменение без перезапуска.
    """
    # Ищем .env рядом с этим файлом
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

    updated = False
    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)

    # Немедленно отражаем изменение в текущем процессе
    os.environ[key] = value


def calculate_msk_deadline(
    day_marker: str,
    week_marker: str,
    time_marker: str,
) -> datetime | None:
    """
    Преобразует маркеры дедлайна из LLM в datetime с учётом МСК (UTC+3).

    day_marker  : "today" | "tomorrow" | "monday"…"sunday" | "YYYY-MM-DD"
    week_marker : "current" | "next"
    time_marker : "HH:MM" или пустая строка → дефолт 18:00
    """
    if not day_marker:
        return None

    tz_msk = timezone(timedelta(hours=3))
    now_msk = datetime.now(tz_msk)
    time_str = time_marker.strip() if time_marker else "18:00"

    # Безопасный парсер времени
    def _apply_time(dt: datetime) -> datetime:
        try:
            hour, minute = map(int, time_str.split(":"))
        except ValueError:
            hour, minute = 18, 0
        return dt.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Точная дата YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", day_marker):
        try:
            base = datetime.strptime(day_marker, "%Y-%m-%d").replace(tzinfo=tz_msk)
            return _apply_time(base)
        except ValueError:
            return None

    # Относительные маркеры
    if day_marker == "today":
        return _apply_time(now_msk)

    if day_marker == "tomorrow":
        return _apply_time(now_msk + timedelta(days=1))

    # День недели
    days_of_week = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    target_day_num = days_of_week.get(day_marker.lower())
    if target_day_num is None:
        return None

    current_day_num = now_msk.weekday()

    if week_marker == "next":
        # «следующий понедельник» — всегда минимум через 7 дней
        days_ahead = (target_day_num - current_day_num) % 7
        if days_ahead == 0:
            days_ahead = 7
        else:
            # Добавляем полную неделю, чтобы это был именно СЛЕДУЮЩИЙ
            days_ahead += 7
    else:
        # Ближайший будущий день недели (включая сегодня, если совпадает)
        days_ahead = (target_day_num - current_day_num) % 7
        if days_ahead == 0:
            # Сегодня тот же день — берём через неделю только если время уже прошло
            candidate = _apply_time(now_msk)
            if candidate <= now_msk:
                days_ahead = 7

    target_date = now_msk + timedelta(days=days_ahead)
    return _apply_time(target_date)


def create_yougile_board_and_columns(api_key: str, project_id: str) -> dict:
    """
    Создаёт доску «Канбан AI PM» и 5 колонок внутри указанного проекта.
    Сначала пробует мастер-ключ из .env, если не сработало — персональный ключ.
    Заполняет COLUMNS_CACHE при успехе.
    """
    master_key = os.getenv("YOUGILE_API_KEY", "")
    # Попробуем сначала мастер-ключ, затем переданный
    keys_to_try = list(dict.fromkeys(filter(None, [master_key, api_key])))

    headers = {"Content-Type": "application/json"}
    board_id: str | None = None
    used_key: str | None = None

    for key in keys_to_try:
        headers["Authorization"] = f"Bearer {key}"
        resp = requests.post(
            "https://ru.yougile.com/api-v2/boards",
            json={"title": "Канбан AI PM", "projectId": project_id},
            headers=headers,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            board_id = resp.json().get("id")
            used_key = key
            print(f"[YouGile] Доска создана, ID={board_id}, ключ={'мастер' if key == master_key else 'персональный'}")
            break
        print(f"[YouGile] Ключ отклонён (HTTP {resp.status_code}): {resp.text[:120]}")

    if not board_id:
        return {"success": False, "used_key": None}

    columns_to_create = [
        "✅ Выполнено",
        "🔥 Горит (Дедлайн < 2 дней)",
        "📅 В работе (С дедлайном)",
        "📥 Бэклог (Без дедлайна)",
        "👥 Участники проекта",
    ]

    created_ids: list[str] = []
    for col_title in columns_to_create:
        resp = requests.post(
            "https://ru.yougile.com/api-v2/columns",
            json={"title": col_title, "boardId": board_id},
            headers=headers,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            created_ids.append(resp.json().get("id"))
            print(f"[YouGile] Колонка '{col_title}' создана.")
        else:
            print(f"[YouGile] Ошибка колонки '{col_title}': {resp.text[:120]}")

    if len(created_ids) == 5:
        COLUMNS_CACHE["participants"]    = created_ids[0]
        COLUMNS_CACHE["no_deadline"]     = created_ids[1]
        COLUMNS_CACHE["has_deadline"]    = created_ids[2]
        COLUMNS_CACHE["urgent_deadline"] = created_ids[3]
        COLUMNS_CACHE["done"]            = created_ids[4]
        return {"success": True, "used_key": used_key}

    print(f"[YouGile] Создано {len(created_ids)}/5 колонок — откатываем успех.")
    return {"success": False, "used_key": used_key}


def get_telegram_username_from_kanban(assignee_name: str, api_key: str) -> str | None:
    """
    Ищет в колонке «Участники проекта» карточку с именем assignee_name
    и возвращает @username, если он есть в названии карточки.
    """
    if not assignee_name or not COLUMNS_CACHE["participants"]:
        return None

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"https://ru.yougile.com/api-v2/tasks?columnId={COLUMNS_CACHE['participants']}"

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            tasks = resp.json().get("content", [])
            for task in tasks:
                title = task.get("title", "")
                if assignee_name.lower() in title.lower():
                    match = re.search(r"@\w+", title)
                    if match:
                        return match.group(0)
    except Exception as exc:
        print(f"[Юзер-кэш] Ошибка поиска: {exc}")

    return None

# ---------------------------------------------------------------------------
# Хэндлеры команд
# ---------------------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🤖 <b>Привет! Я AI Project Manager.</b>\n\n"
        "Я незаметно анализирую диалог в этой группе, вытаскиваю контекст "
        "и автоматически формирую задачи в YouGile.\n\n"
        "Все функции управления доступны в меню <b>[ Menu ]</b> слева от поля ввода.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(Command("get_key"))
async def cmd_get_key(message: Message) -> None:
    # Удаляем команду из чата, чтобы не засорять историю
    try:
        await message.delete()
    except Exception:
        pass

    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔑 Вход в YouGile", web_app=WebAppInfo(url=WEB_APP_URL))]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "Для безопасной авторизации нажмите кнопку <b>«🔑 Вход в YouGile»</b> внизу экрана.\n"
        "Ваш логин и пароль будут полностью скрыты от истории чата.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    chat_id = message.chat.id
    pending = len(chat_buffers.get(chat_id, []))
    await message.answer(
        "📋 <b>Статистика сессии (In-Memory):</b>\n"
        f"• Ожидают отправки в AI: <b>{pending}</b> реплик\n"
        f"• Всего обработано реплик: <b>{SESSION_STATS['processed_messages_count']}</b>\n"
        f"• Успешно создано задач: <b>{SESSION_STATS['created_tasks_count']}</b>",
        parse_mode="HTML",
    )

# ---------------------------------------------------------------------------
# Хэндлер Web App — получение логина/пароля
# ---------------------------------------------------------------------------

@dp.message(F.web_app_data)
async def process_web_app_data(message: Message, state: FSMContext) -> None:
    try:
        data: dict = json.loads(message.web_app_data.data)
    except (json.JSONDecodeError, AttributeError) as exc:
        await message.answer(
            f"🔴 Не удалось разобрать данные Web App: {exc}",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    login = data.get("login", "").strip()
    password = data.get("password", "").strip()

    if not login or not password:
        await message.answer("🔴 Логин или пароль не переданы. Попробуйте ещё раз.", reply_markup=ReplyKeyboardRemove())
        return

    status_msg = await message.answer("⏳ Подключаюсь к YouGile...", reply_markup=ReplyKeyboardRemove())

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    companies_url = "https://ru.yougile.com/api-v2/auth/companies"

    # API принимает как "login", так и "email" — пробуем оба варианта
    response = await asyncio.to_thread(
        requests.post, companies_url, json={"login": login, "password": password}, headers=headers, timeout=15
    )
    if response.status_code == 401:
        response = await asyncio.to_thread(
            requests.post, companies_url, json={"email": login, "password": password}, headers=headers, timeout=15
        )

    if response.status_code not in (200, 201):
        await status_msg.edit_text("🔴 Неверный логин или пароль. Попробуйте ещё раз через /get_key.")
        return

    # Нормализуем список компаний из разных форматов ответа
    body = response.json()
    if isinstance(body, list):
        companies_list = body
    elif isinstance(body, dict):
        companies_list = body.get("content") or body.get("data") or [body]
    else:
        companies_list = []

    if not companies_list:
        await status_msg.edit_text("🔴 У этого аккаунта нет доступных компаний.")
        return

    await state.set_state(AuthStates.waiting_for_company)
    await state.update_data(login=login, password=password)

    buttons = [
        [InlineKeyboardButton(
            text=c.get("name") or f"Компания {c.get('id', '?')}",
            callback_data=f"company_{c.get('id')}",
        )]
        for c in companies_list
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    await status_msg.delete()
    await message.answer(
        f"🍏 <b>Успешный вход!</b>\nАккаунт: <code>{login}</code>\n\n"
        "Выберите компанию, чтобы загрузить список её проектов:",
        parse_mode="HTML",
        reply_markup=markup,
    )

# ---------------------------------------------------------------------------
# FSM-шаг 1: выбор компании → загружаем список проектов
# ---------------------------------------------------------------------------

@dp.callback_query(AuthStates.waiting_for_company, F.data.startswith("company_"))
async def process_company_choice(callback: CallbackQuery, state: FSMContext) -> None:
    company_id = callback.data.removeprefix("company_")
    user_data = await state.get_data()
    login = user_data["login"]
    password = user_data["password"]

    await callback.message.edit_text("⏳ Получаю список проектов компании...")

    # Получаем токен для выбранной компании
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token_url = "https://ru.yougile.com/api-v2/auth/keys/get"

    resp = await asyncio.to_thread(
        requests.post,
        token_url,
        json={"login": login, "password": password, "companyId": company_id},
        headers=headers,
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        # Запасной вариант с "email"
        resp = await asyncio.to_thread(
            requests.post,
            token_url,
            json={"email": login, "password": password, "companyId": company_id},
            headers=headers,
            timeout=15,
        )

    if resp.status_code not in (200, 201):
        await callback.message.edit_text(
            f"🔴 Не удалось получить токен компании (HTTP {resp.status_code}). Попробуйте ещё раз."
        )
        await state.clear()
        await callback.answer()
        return

    token_body = resp.json()

    # YouGile может вернуть список токенов или одиночный словарь
    if isinstance(token_body, list):
        # Берём первый активный токен из списка
        token_obj = next(
            (t for t in token_body if isinstance(t, dict) and not t.get("deleted")),
            token_body[0] if token_body else None,
        )
    elif isinstance(token_body, dict):
        token_obj = token_body
    else:
        token_obj = None

    if isinstance(token_obj, dict):
        api_key = (
            token_obj.get("key")
            or token_obj.get("token")
            or (token_obj.get("content") or {}).get("key")
        )
    else:
        api_key = None

    if not api_key:
        await callback.message.edit_text("🔴 Сервер не вернул API-ключ. Обратитесь в поддержку YouGile.")
        await state.clear()
        await callback.answer()
        return

    # Загружаем проекты
    auth_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    proj_resp = await asyncio.to_thread(
        requests.get, "https://ru.yougile.com/api-v2/projects", headers=auth_headers, timeout=15
    )

    if proj_resp.status_code != 200:
        await callback.message.edit_text(f"🔴 Не удалось получить проекты (HTTP {proj_resp.status_code}).")
        await state.clear()
        await callback.answer()
        return

    proj_body = proj_resp.json()
    projects = proj_body if isinstance(proj_body, list) else proj_body.get("content", [])

    if not projects:
        await callback.message.edit_text("🔴 В этой компании нет доступных проектов.")
        await state.clear()
        await callback.answer()
        return

    await state.set_state(AuthStates.waiting_for_project)
    await state.update_data(api_key=api_key, company_id=company_id)

    buttons = [
        [InlineKeyboardButton(
            text=p.get("title") or f"Проект {p.get('id', '?')}",
            callback_data=f"project_{p.get('id')}",
        )]
        for p in projects
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        "📁 Выберите проект, в котором бот будет создавать задачи:",
        reply_markup=markup,
    )
    await callback.answer()

# ---------------------------------------------------------------------------
# FSM-шаг 2: выбор проекта → создаём доску и колонки
# ---------------------------------------------------------------------------

@dp.callback_query(AuthStates.waiting_for_project, F.data.startswith("project_"))
async def process_project_choice(callback: CallbackQuery, state: FSMContext) -> None:
    project_id = callback.data.removeprefix("project_")
    user_data = await state.get_data()

    api_key = user_data["api_key"]
    company_id = user_data["company_id"]

    await callback.message.edit_text("⏳ Разворачиваю доску «Канбан AI PM» и 5 колонок...")

    result = await asyncio.to_thread(create_yougile_board_and_columns, api_key, project_id)

    if result["success"]:
        working_key = result["used_key"]
        # Сохраняем в .env и сразу обновляем os.environ
        await asyncio.to_thread(update_env_file, "YOUGILE_API_KEY", working_key)
        await asyncio.to_thread(update_env_file, "YOUGILE_COMPANY_ID", company_id)

        # Пересоздаём клиент с актуальным ключом
        global kanban
        kanban = _make_kanban()

        await callback.message.edit_text(
            "🚀 <b>Структура AI-доски успешно развёрнута!</b>\n\n"
            "В проекте создана доска <b>«Канбан AI PM»</b> и все 5 колонок.\n"
            "Рабочий токен сохранён в <code>.env</code>. 📁\n\n"
            "<b>Следующий шаг:</b> откройте 1-й столбец <i>«Участники проекта»</i> "
            "и добавьте карточки участников в формате:\n"
            "<code>Иван Иванов (@username_tg)</code>\n\n"
            "Бот полностью готов к анализу диалогов! 🎯",
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            "🔴 Не удалось создать структуру колонок.\n"
            "Проверьте права токена в настройках YouGile и попробуйте снова."
        )

    await state.clear()
    await callback.answer()

# ---------------------------------------------------------------------------
# Конвейер умного анализа диалога
# ---------------------------------------------------------------------------

async def _cancel_timer(chat_id: int) -> None:
    """Безопасно отменяет дебаунс-таймер чата."""
    task = chat_timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def process_live_conversation(chat_id: int) -> None:
    """
    Забирает накопленный буфер, отправляет в LLM, создаёт задачи в YouGile.
    Всегда чистит таймеры и время начала сессии после завершения.
    """
    try:
        api_key = os.getenv("YOUGILE_API_KEY")
        if not api_key:
            print("[AI PM] YOUGILE_API_KEY не задан — пропускаю обработку.")
            return

        if not COLUMNS_CACHE["no_deadline"]:
            print("[AI PM] Колонки ещё не созданы — пропускаю обработку.")
            return

        messages = chat_buffers.pop(chat_id, [])
        if not messages:
            return

        SESSION_STATS["processed_messages_count"] += len(messages)

        conversation_text = "\n".join(
            f"[{m['username']}]: {m['text']}" for m in messages
        )

        tasks = await asyncio.to_thread(parse_tasks_from_text, conversation_text)
        if not tasks:
            return

        tz_msk = timezone(timedelta(hours=3))
        created_count = 0

        for task in tasks:
            title: str = task.get("title") or "Новая задача"
            assignee_name: str | None = task.get("assignee")

            # Ищем @username в колонке участников
            tg_username = await asyncio.to_thread(
                get_telegram_username_from_kanban, assignee_name, api_key
            )
            prefix = f"{tg_username} " if tg_username else ""
            final_title = f"{prefix}{title}"

            # Вычисляем дедлайн
            deadline_dt = calculate_msk_deadline(
                day_marker=task.get("deadline_day", ""),
                week_marker=task.get("deadline_week", "current"),
                time_marker=task.get("deadline_time", "18:00"),
            )

            # Определяем целевую колонку
            deadline_str: str | None = None
            if deadline_dt:
                deadline_str = deadline_dt.strftime("%Y-%m-%d %H:%M")
                now_msk = datetime.now(tz_msk)
                if (deadline_dt - now_msk) <= timedelta(days=2):
                    target_column = COLUMNS_CACHE["urgent_deadline"]
                else:
                    target_column = COLUMNS_CACHE["has_deadline"]
            else:
                target_column = COLUMNS_CACHE["no_deadline"]

            description = (
                f"Исполнитель: {assignee_name or 'Не назначен'}\n"
                "Приоритет выставлен AI-ботом."
            )

            try:
                await asyncio.to_thread(
                    kanban.create_task,
                    target_column,
                    final_title,
                    description,
                    deadline_str=deadline_str,
                    assignee_name=assignee_name,
                )
                created_count += 1
                SESSION_STATS["created_tasks_count"] += 1
            except Exception as exc:
                print(f"[YouGile] Ошибка создания задачи «{final_title}»: {exc}")

        if created_count > 0:
            await bot.send_message(
                chat_id,
                f"🤖 <b>AI PM:</b> Распознал контекст диалога. "
                f"Создано задач в YouGile: <b>{created_count}</b> 🚀",
                parse_mode="HTML",
            )

    except Exception as exc:
        print(f"[AI PM] Критическая ошибка конвейера: {exc}")

    finally:
        # Гарантированно очищаем состояние сессии
        chat_timers.pop(chat_id, None)
        chat_start_times.pop(chat_id, None)


async def _debounce_timer(chat_id: int) -> None:
    """Ждёт REALTIME_DELAY секунд тишины, затем запускает обработку."""
    await asyncio.sleep(REALTIME_DELAY)
    await process_live_conversation(chat_id)

# ---------------------------------------------------------------------------
# Хэндлер входящих текстовых сообщений
# ---------------------------------------------------------------------------

@dp.message(F.text)
async def handle_all_messages(message: Message) -> None:
    # Игнорируем команды — они обрабатываются отдельными хэндлерами
    if not message.text or message.text.startswith("/"):
        return

    username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else message.from_user.first_name
    )
    chat_id = message.chat.id
    now = time.monotonic()

    # Добавляем сообщение в буфер
    if chat_id not in chat_buffers:
        chat_buffers[chat_id] = []
    chat_buffers[chat_id].append({"username": username, "text": message.text})

    # Фиксируем начало сессии
    if chat_id not in chat_start_times:
        chat_start_times[chat_id] = now

    elapsed = now - chat_start_times[chat_id]

    if elapsed >= MAX_CONVERSATION_TIME:
        # Сессия слишком длинная — принудительно обрабатываем
        await _cancel_timer(chat_id)
        asyncio.create_task(process_live_conversation(chat_id))
    else:
        # Сбрасываем дебаунс-таймер
        await _cancel_timer(chat_id)
        chat_timers[chat_id] = asyncio.create_task(_debounce_timer(chat_id))

# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

async def main() -> None:
    commands = [
        BotCommand(command="get_key",  description="🔑 Авторизоваться / Выбрать проект YouGile"),
        BotCommand(command="stats",    description="📋 Статистика очереди чата"),
        BotCommand(command="start",    description="🤖 Информация о боте"),
    ]
    await bot.set_my_commands(commands)

    print("[SERVER] AI PM запущен. Режим In-Memory (СУБД не используется).")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())