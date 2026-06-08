"""
Telegram-бот AI PM для автоматизации YouGile.
"""

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kanban.kanban_client import YouGileClient
from utils.llm import parse_tasks_from_text

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

load_dotenv()

WEB_APP_URL           = "https://jolly-flan-a04ec.netlify.app/"
REALTIME_DELAY        = 15    # сек тишины → триггер обработки
MAX_CONVERSATION_TIME = 900   # сек максимальной длины сессии
MIGRATION_INTERVAL    = 300   # сек между фоновыми проверками дедлайнов (5 мин)

# ---------------------------------------------------------------------------
# Глобальные объекты
# ---------------------------------------------------------------------------

bot    = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
dp     = Dispatcher()

def _make_kanban() -> YouGileClient:
    return YouGileClient()

kanban: YouGileClient = _make_kanban()

# ---------------------------------------------------------------------------
# In-memory хранилища
# ---------------------------------------------------------------------------

chat_buffers:     dict[int, list[dict]] = {}   # {chat_id: [{"username", "text"}]}
chat_timers:      dict[int, asyncio.Task] = {}  # дебаунс-таймеры
chat_start_times: dict[int, float] = {}         # начало текущей сессии

SESSION_STATS = {"processed_messages_count": 0, "created_tasks_count": 0}

# Кэш ID колонок — заполняется после /get_key → выбор проекта
COLUMNS_CACHE: dict[str, str | None] = {
    "participants":   None,  # 1. Участники проекта
    "no_deadline":    None,  # 2. Без дедлайна
    "has_deadline":   None,  # 3. Дедлайн есть
    "urgent_deadline":None,  # 4. Дедлайн < 2 дней
    "done":           None,  # 5. Выполнено
}

# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------

class AuthStates(StatesGroup):
    waiting_for_company = State()
    waiting_for_project = State()

# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def update_env_file(key: str, value: str) -> None:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

    updated = False
    new_lines = []
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
    os.environ[key] = value


def calculate_msk_deadline(
    day_marker: str,
    week_marker: str,
    time_marker: str,
) -> datetime | None:
    if not day_marker:
        return None

    tz_msk   = timezone(timedelta(hours=0))
    now_msk  = datetime.now(tz_msk)
    time_str = (time_marker or "18:00").strip()

    def _apply_time(dt: datetime) -> datetime:
        try:
            h, m = map(int, time_str.split(":"))
        except ValueError:
            h, m = 18, 0
        return dt.replace(hour=h, minute=m, second=0, microsecond=0)

    if re.match(r"^\d{4}-\d{2}-\d{2}$", day_marker):
        try:
            base = datetime.strptime(day_marker, "%Y-%m-%d").replace(tzinfo=tz_msk)
            return _apply_time(base)
        except ValueError:
            return None

    if day_marker == "today":
        return _apply_time(now_msk)
    if day_marker == "tomorrow":
        return _apply_time(now_msk + timedelta(days=1))

    days_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    target_num = days_map.get(day_marker.lower())
    if target_num is None:
        return None

    cur = now_msk.weekday()
    if week_marker == "next":
        days_ahead = (target_num - cur) % 7 or 7
        days_ahead += 7
    else:
        days_ahead = (target_num - cur) % 7
        if days_ahead == 0 and _apply_time(now_msk) <= now_msk:
            days_ahead = 7

    return _apply_time(now_msk + timedelta(days=days_ahead))


def create_yougile_board_and_columns(api_key: str, project_id: str) -> dict:
    """Создаёт доску «Канбан AI PM» и 5 колонок; заполняет COLUMNS_CACHE."""
    master_key    = os.getenv("YOUGILE_API_KEY", "")
    keys_to_try   = list(dict.fromkeys(filter(None, [master_key, api_key])))
    headers       = {"Content-Type": "application/json"}
    board_id      = None
    used_key      = None

    for key in keys_to_try:
        headers["Authorization"] = f"Bearer {key}"
        r = requests.post(
            "https://ru.yougile.com/api-v2/boards",
            json={"title": "Канбан AI PM", "projectId": project_id},
            headers=headers,
            timeout=15,
        )
        if r.status_code in (200, 201):
            board_id = r.json().get("id")
            used_key = key
            print(f"[YouGile] Доска создана ID={board_id}")
            break
        print(f"[YouGile] Ключ отклонён HTTP {r.status_code}")

    if not board_id:
        return {"success": False, "used_key": None}

    # Порядок колонок ВАЖЕН — совпадает с индексами COLUMNS_CACHE
    columns_ordered = [
        "👥 Участники проекта",     # 0 → participants
        "🔥 Дедлайн < 2 дней",       # 3 → urgent_deadline
        "📅 Дедлайн есть",           # 2 → has_deadline
        "📥 Без дедлайна",           # 1 → no_deadline
        "✅ Выполнено",              # 4 → done
    ]
    cache_keys = ["participants", "no_deadline", "has_deadline", "urgent_deadline", "done"]

    created_ids: list[str] = []
    for title in columns_ordered:
        r = requests.post(
            "https://ru.yougile.com/api-v2/columns",
            json={"title": title, "boardId": board_id},
            headers=headers,
            timeout=15,
        )
        if r.status_code in (200, 201):
            created_ids.append(r.json().get("id"))
        else:
            print(f"[YouGile] Ошибка колонки '{title}': {r.text[:120]}")

    if len(created_ids) == 5:
        for i, cache_key in enumerate(cache_keys):
            COLUMNS_CACHE[cache_key] = created_ids[i]
        return {"success": True, "used_key": used_key}

    print(f"[YouGile] Создано {len(created_ids)}/5 колонок.")
    return {"success": False, "used_key": used_key}

# ---------------------------------------------------------------------------
# Фоновый воркер: миграция задач по дедлайну
# ---------------------------------------------------------------------------

async def background_migrator() -> None:
    """
    Каждые MIGRATION_INTERVAL секунд проверяет задачи во всех рабочих колонках
    и перемещает их в соответствии с текущим дедлайном.
    """
    await asyncio.sleep(30)  # небольшая задержка после старта
    while True:
        try:
            if all(COLUMNS_CACHE.values()):
                moved = await asyncio.to_thread(
                    kanban.migrate_tasks_by_deadline, COLUMNS_CACHE
                )
                if moved:
                    print(f"[Миграция] Перемещено задач: {moved}")
        except Exception as e:
            print(f"[Миграция] Ошибка: {e}")
        await asyncio.sleep(MIGRATION_INTERVAL)

# ---------------------------------------------------------------------------
# Команды бота
# ---------------------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🤖 <b>Привет! Я AI Project Manager.</b>\n\n"
        "Анализирую диалог в группе, вытаскиваю задачи и создаю их в YouGile.\n"
        "Задачи автоматически переезжают между колонками при изменении дедлайна.\n\n"
        "Управление — в меню <b>[ Menu ]</b> слева от поля ввода.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(Command("get_key"))
async def cmd_get_key(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(
            text="🔑 Вход в YouGile",
            web_app=WebAppInfo(url=WEB_APP_URL),
        )]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "Нажмите <b>«🔑 Вход в YouGile»</b> для безопасной авторизации.\n"
        "Логин и пароль будут скрыты от истории чата.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    pending = len(chat_buffers.get(message.chat.id, []))
    await message.answer(
        "📋 <b>Статистика сессии:</b>\n"
        f"• В буфере (ожидают AI): <b>{pending}</b> реплик\n"
        f"• Всего обработано реплик: <b>{SESSION_STATS['processed_messages_count']}</b>\n"
        f"• Создано задач в YouGile: <b>{SESSION_STATS['created_tasks_count']}</b>",
        parse_mode="HTML",
    )

# ---------------------------------------------------------------------------
# Web App: получение логина/пароля
# ---------------------------------------------------------------------------

@dp.message(F.web_app_data)
async def process_web_app_data(message: Message, state: FSMContext) -> None:
    try:
        data: dict = json.loads(message.web_app_data.data)
    except (json.JSONDecodeError, AttributeError) as e:
        await message.answer(f"🔴 Ошибка разбора Web App данных: {e}",
                             reply_markup=ReplyKeyboardRemove())
        return

    login    = data.get("login", "").strip()
    password = data.get("password", "").strip()
    if not login or not password:
        await message.answer("🔴 Логин или пароль не переданы.",
                             reply_markup=ReplyKeyboardRemove())
        return

    status = await message.answer("⏳ Подключаюсь к YouGile...",
                                  reply_markup=ReplyKeyboardRemove())
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    url     = "https://ru.yougile.com/api-v2/auth/companies"

    resp = await asyncio.to_thread(
        requests.post, url,
        json={"login": login, "password": password},
        headers=headers, timeout=15,
    )
    if resp.status_code == 401:
        resp = await asyncio.to_thread(
            requests.post, url,
            json={"email": login, "password": password},
            headers=headers, timeout=15,
        )

    if resp.status_code not in (200, 201):
        await status.edit_text("🔴 Неверный логин или пароль.")
        return

    body = resp.json()
    companies = (
        body if isinstance(body, list)
        else body.get("content") or body.get("data") or [body]
    )
    if not companies:
        await status.edit_text("🔴 Нет доступных компаний.")
        return

    await state.set_state(AuthStates.waiting_for_company)
    await state.update_data(login=login, password=password)

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=c.get("name") or f"Компания {c.get('id','?')}",
            callback_data=f"company_{c.get('id')}",
        )]
        for c in companies
    ])
    await status.delete()
    await message.answer(
        f"🍏 <b>Успешный вход!</b>\nАккаунт: <code>{login}</code>\n\n"
        "Выберите компанию:",
        parse_mode="HTML",
        reply_markup=markup,
    )

# ---------------------------------------------------------------------------
# FSM шаг 1: выбор компании
# ---------------------------------------------------------------------------

@dp.callback_query(AuthStates.waiting_for_company, F.data.startswith("company_"))
async def process_company_choice(callback: CallbackQuery, state: FSMContext) -> None:
    company_id = callback.data.removeprefix("company_")
    data       = await state.get_data()
    login, password = data["login"], data["password"]

    await callback.message.edit_text("⏳ Получаю список проектов...")

    headers   = {"Content-Type": "application/json", "Accept": "application/json"}
    token_url = "https://ru.yougile.com/api-v2/auth/keys/get"

    resp = await asyncio.to_thread(
        requests.post, token_url,
        json={"login": login, "password": password, "companyId": company_id},
        headers=headers, timeout=15,
    )
    if resp.status_code not in (200, 201):
        resp = await asyncio.to_thread(
            requests.post, token_url,
            json={"email": login, "password": password, "companyId": company_id},
            headers=headers, timeout=15,
        )

    if resp.status_code not in (200, 201):
        await callback.message.edit_text(
            f"🔴 Не удалось получить токен (HTTP {resp.status_code})."
        )
        await state.clear()
        await callback.answer()
        return

    # Извлекаем ключ из любого формата ответа (list или dict)
    token_body = resp.json()
    if isinstance(token_body, list):
        token_obj = next(
            (t for t in token_body if isinstance(t, dict) and not t.get("deleted")),
            token_body[0] if token_body else None,
        )
    elif isinstance(token_body, dict):
        token_obj = token_body
    else:
        token_obj = None

    api_key = None
    if isinstance(token_obj, dict):
        api_key = (
            token_obj.get("key")
            or token_obj.get("token")
            or (token_obj.get("content") or {}).get("key")
        )

    if not api_key:
        await callback.message.edit_text("🔴 Сервер не вернул API-ключ.")
        await state.clear()
        await callback.answer()
        return

    # Загружаем проекты
    auth_h = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    proj_r = await asyncio.to_thread(
        requests.get, "https://ru.yougile.com/api-v2/projects",
        headers=auth_h, timeout=15,
    )
    if proj_r.status_code != 200:
        await callback.message.edit_text(
            f"🔴 Не удалось получить проекты (HTTP {proj_r.status_code})."
        )
        await state.clear()
        await callback.answer()
        return

    proj_body = proj_r.json()
    projects  = proj_body if isinstance(proj_body, list) else proj_body.get("content", [])
    if not projects:
        await callback.message.edit_text("🔴 Нет доступных проектов в этой компании.")
        await state.clear()
        await callback.answer()
        return

    await state.set_state(AuthStates.waiting_for_project)
    await state.update_data(api_key=api_key, company_id=company_id)

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=p.get("title") or f"Проект {p.get('id','?')}",
            callback_data=f"project_{p.get('id')}",
        )]
        for p in projects
    ])
    await callback.message.edit_text(
        "📁 Выберите проект для создания задач:",
        reply_markup=markup,
    )
    await callback.answer()

# ---------------------------------------------------------------------------
# FSM шаг 2: выбор проекта → создаём доску
# ---------------------------------------------------------------------------

@dp.callback_query(AuthStates.waiting_for_project, F.data.startswith("project_"))
async def process_project_choice(callback: CallbackQuery, state: FSMContext) -> None:
    project_id = callback.data.removeprefix("project_")
    data       = await state.get_data()
    api_key    = data["api_key"]
    company_id = data["company_id"]

    await callback.message.edit_text("⏳ Разворачиваю доску «Канбан AI PM» и 5 колонок...")

    result = await asyncio.to_thread(create_yougile_board_and_columns, api_key, project_id)

    if result["success"]:
        working_key = result["used_key"]
        await asyncio.to_thread(update_env_file, "YOUGILE_API_KEY", working_key)
        await asyncio.to_thread(update_env_file, "YOUGILE_COMPANY_ID", company_id)
        global kanban
        kanban = _make_kanban()

        await callback.message.edit_text(
            "🚀 <b>Структура AI-доски успешно развёрнута!</b>\n\n"
            "Создана доска <b>«Канбан AI PM»</b> с 5 колонками:\n"
            "👥 Участники → 📥 Без дедлайна → 📅 Дедлайн есть → 🔥 Горит → ✅ Выполнено\n\n"
            "Токен сохранён в <code>.env</code>. 📁\n\n"
            "<b>Что дальше:</b> просто общайтесь в группе — бот сам создаст задачи "
            "и карточки участников. Или добавьте участников вручную в колонку "
            "<i>«👥 Участники проекта»</i> в формате:\n"
            "<code>Иван Иванов (@username_tg)</code>",
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            "🔴 Не удалось создать структуру колонок.\n"
            "Проверьте права токена и попробуйте снова."
        )

    await state.clear()
    await callback.answer()

# ---------------------------------------------------------------------------
# Конвейер анализа диалога
# ---------------------------------------------------------------------------

async def _cancel_timer(chat_id: int) -> None:
    task = chat_timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def process_live_conversation(chat_id: int) -> None:
    """
    1. Берёт буфер сообщений из памяти.
    2. Отправляет в LLM → получает задачи + участников.
    3. Создаёт карточки участников в колонке «Участники проекта».
    4. Создаёт задачи в нужных колонках на основе дедлайна.
    """
    try:
        api_key = os.getenv("YOUGILE_API_KEY")
        if not api_key:
            print("[AI PM] YOUGILE_API_KEY не задан.")
            return
        if not COLUMNS_CACHE["no_deadline"]:
            print("[AI PM] Колонки не инициализированы.")
            return

        messages = chat_buffers.pop(chat_id, [])
        if not messages:
            return

        SESSION_STATS["processed_messages_count"] += len(messages)
        conversation_text = "\n".join(f"[{m['username']}]: {m['text']}" for m in messages)

        # --- LLM ---
        tasks, participants = await asyncio.to_thread(parse_tasks_from_text, conversation_text)

        # --- Обрабатываем участников ---
        parts_col = COLUMNS_CACHE["participants"]
        if participants and parts_col:
            for person in participants:
                name       = person.get("full_name", "").strip()
                tg_username = person.get("tg_username")
                if not name:
                    continue
                await asyncio.to_thread(
                    kanban.ensure_participant_card,
                    parts_col,
                    name,
                    tg_username,
                )

        if not tasks:
            return

        # --- Создаём задачи ---
        tz_msk        = timezone(timedelta(hours=3))
        created_count = 0

        for task in tasks:
            title          = task.get("title") or "Новая задача"
            assignee_name  = task.get("assignee")

            # Ищем @tg_username исполнителя
            tg_username = None
            if assignee_name and parts_col:
                tg_username = await asyncio.to_thread(
                    kanban.get_tg_username_for_assignee,
                    assignee_name,
                    parts_col,
                )

            # Добавляем @mention в заголовок задачи
            mention_prefix = f"{tg_username} " if tg_username else ""
            final_title    = f"{mention_prefix}{title}"

            # Дедлайн
            deadline_dt = calculate_msk_deadline(
                day_marker  = task.get("deadline_day", ""),
                week_marker = task.get("deadline_week", "current"),
                time_marker = task.get("deadline_time", "18:00"),
            )

            # Выбираем колонку
            deadline_str: str | None = None
            if deadline_dt:
                deadline_str = deadline_dt.strftime("%Y-%m-%d %H:%M")
                now_msk      = datetime.now(tz_msk)
                diff         = deadline_dt - now_msk
                if diff.total_seconds() < 0:
                    target_column = COLUMNS_CACHE["done"]
                elif diff <= timedelta(days=2):
                    target_column = COLUMNS_CACHE["urgent_deadline"]
                else:
                    target_column = COLUMNS_CACHE["has_deadline"]
            else:
                target_column = COLUMNS_CACHE["no_deadline"]

            description = (
                f"Исполнитель: {assignee_name or 'Не назначен'}\n"
                f"Приоритет: {task.get('priority', 'medium')}\n"
                "Создано AI PM автоматически."
            )

            try:
                await asyncio.to_thread(
                    kanban.create_task,
                    target_column,
                    final_title,
                    description,
                    deadline_str=deadline_str,
                    assignee_name=assignee_name,
                    participants_column_id=parts_col,
                )
                created_count += 1
                SESSION_STATS["created_tasks_count"] += 1
            except Exception as e:
                print(f"[AI PM] Ошибка создания задачи «{final_title}»: {e}")

        if created_count > 0:
            await bot.send_message(
                chat_id,
                f"🤖 <b>AI PM:</b> создал <b>{created_count}</b> задач в YouGile 🚀\n"
                f"Автомиграция задач по дедлайну работает в фоне (каждые 5 мин).",
                parse_mode="HTML",
            )

    except Exception as e:
        print(f"[AI PM] Критическая ошибка: {e}")
    finally:
        chat_timers.pop(chat_id, None)
        chat_start_times.pop(chat_id, None)


async def _debounce_timer(chat_id: int) -> None:
    await asyncio.sleep(REALTIME_DELAY)
    await process_live_conversation(chat_id)

# ---------------------------------------------------------------------------
# Хэндлер всех текстовых сообщений
# ---------------------------------------------------------------------------

@dp.message(F.text)
async def handle_all_messages(message: Message) -> None:
    if not message.text or message.text.startswith("/"):
        return

    username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else message.from_user.first_name
    )
    chat_id = message.chat.id
    now     = time.monotonic()

    chat_buffers.setdefault(chat_id, []).append(
        {"username": username, "text": message.text}
    )
    chat_start_times.setdefault(chat_id, now)

    elapsed = now - chat_start_times[chat_id]

    if elapsed >= MAX_CONVERSATION_TIME:
        await _cancel_timer(chat_id)
        asyncio.create_task(process_live_conversation(chat_id))
    else:
        await _cancel_timer(chat_id)
        chat_timers[chat_id] = asyncio.create_task(_debounce_timer(chat_id))

# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

async def main() -> None:
    await bot.set_my_commands([
        BotCommand(command="get_key", description="🔑 Авторизоваться / Выбрать проект YouGile"),
        BotCommand(command="stats",   description="📋 Статистика очереди чата"),
        BotCommand(command="start",   description="🤖 Информация о боте"),
    ])

    # Запускаем фоновый воркер миграции задач
    asyncio.create_task(background_migrator())

    print("[SERVER] AI PM запущен. In-Memory режим. Миграция задач активна.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())