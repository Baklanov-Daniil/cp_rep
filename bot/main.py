"""
Telegram entrypoint for AI PM: Telegram conversations in, YouGile tasks out.
"""

import asyncio
import json
import os
import sys
import time

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, CallbackQuery, Message, ReplyKeyboardRemove

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.board_setup import create_ai_pm_board, update_env_file
from bot.config import load_config
from bot.conversation import SessionStats, process_conversation, username_from_message_user
from bot.repositories import (
    columns_from_settings,
    get_chat_settings,
    is_chat_connected,
    pending_count,
    save_board_settings,
    save_pending_message,
    upsert_chat_member,
)
from bot.scheduler import monitor_deadlines
from bot.states import AuthStates
from bot.task_digest import send_task_digest
from bot.ui import companies_keyboard, connected_setup_keyboard, main_menu_keyboard, projects_keyboard, reconnect_keyboard
from bot.yougile_auth import get_companies, get_or_create_api_key, get_projects
from db.database import init_db


config = load_config()
if not config.telegram_token:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

bot = Bot(token=config.telegram_token)
dp = Dispatcher()
stats = SessionStats()

chat_timers: dict[int, asyncio.Task] = {}
chat_start_times: dict[int, float] = {}


async def is_group_admin(message: Message) -> bool:
    if message.chat.type == "private":
        return True
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    return member.status in {"administrator", "creator"}


async def is_callback_admin(callback: CallbackQuery) -> bool:
    if callback.message.chat.type == "private":
        return True
    member = await bot.get_chat_member(callback.message.chat.id, callback.from_user.id)
    return member.status in {"administrator", "creator"}


async def remember_sender(message: Message, role: str = "user") -> None:
    if not message.from_user:
        return
    username = username_from_message_user(message.from_user)
    await upsert_chat_member(message.chat.id, message.from_user.id, username, role=role)


async def render_status(chat_id: int) -> str:
    settings = await get_chat_settings(chat_id)
    pending = await pending_count(chat_id)
    if not is_chat_connected(settings):
        return (
            "📊 <b>Статус AI PM</b>\n\n"
            "🔴 YouGile ещё не подключён.\n"
            f"💬 Реплик в очереди: <b>{pending}</b>\n\n"
            "Нажмите <b>🔑 Подключить YouGile</b>, чтобы выбрать компанию и проект."
        )

    columns = columns_from_settings(settings)
    return (
        "📊 <b>Статус AI PM</b>\n\n"
        "🟢 YouGile подключён\n"
        f"🧩 Project ID: <code>{settings.project_id}</code>\n"
        f"🗂 Board ID: <code>{settings.board_id}</code>\n"
        f"💬 Реплик в очереди: <b>{pending}</b>\n\n"
        "Колонки:\n"
        f"👥 Участники: <code>{columns['participants']}</code>\n"
        f"📥 Без дедлайна: <code>{columns['no_deadline']}</code>\n"
        f"📅 С дедлайном: <code>{columns['has_deadline']}</code>\n"
        f"🔥 Срочно: <code>{columns['urgent_deadline']}</code>\n"
        f"✅ Выполнено: <code>{columns['done']}</code>"
    )


async def show_main_menu(message: Message) -> None:
    await remember_sender(message)
    settings = await get_chat_settings(message.chat.id)
    await message.answer(
        "🧭 <b>AI PM меню</b>\n\n"
        "Выберите действие. Я могу подключить YouGile, показать состояние очереди, "
        "обработать накопленные сообщения или разослать задачи ответственным.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(is_chat_connected(settings)),
    )


async def ask_for_yougile_login(message: Message) -> None:
    await message.answer(
        "🔑 <b>Подключение YouGile</b>\n\n"
        "Нажмите кнопку ниже и войдите в YouGile. После этого я предложу компанию и проект.\n"
        "Если чат уже был подключён, повторная авторизация не нужна — используйте меню настроек.",
        parse_mode="HTML",
        reply_markup=reconnect_keyboard(config.web_app_url),
    )


async def send_digest_and_report(message: Message) -> None:
    result = await send_task_digest(bot, message.chat.id)
    if result["error"] == "not_connected":
        await message.answer("🔴 Сначала подключите YouGile через /get_key или /menu.")
        return

    await message.answer(
        "📨 <b>Рассылка задач завершена</b>\n\n"
        f"Задач в дайджесте: <b>{result['tasks']}</b>\n"
        f"Личных сообщений отправлено: <b>{result['sent']}</b>\n"
        f"Сообщений в группу: <b>{result['fallback']}</b>\n\n"
        "Если кому-то задача пришла в группу, значит пользователь ещё не открывал личный чат с ботом "
        "или его Telegram ID не найден в участниках.",
        parse_mode="HTML",
    )


async def cancel_timer(chat_id: int) -> None:
    task = chat_timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def debounce_processing(chat_id: int) -> None:
    await asyncio.sleep(config.realtime_delay)
    await process_chat_queue(chat_id)


async def process_chat_queue(chat_id: int) -> None:
    try:
        await process_conversation(chat_id, bot, stats)
    finally:
        chat_timers.pop(chat_id, None)
        chat_start_times.pop(chat_id, None)


async def schedule_chat_processing(chat_id: int) -> None:
    elapsed = time.monotonic() - chat_start_times[chat_id]
    await cancel_timer(chat_id)

    if elapsed >= config.max_conversation_time:
        asyncio.create_task(process_chat_queue(chat_id))
        return

    chat_timers[chat_id] = asyncio.create_task(debounce_processing(chat_id))


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await remember_sender(message)
    await message.answer(
        "🤖 <b>Привет! Я AI Project Manager.</b>\n\n"
        "Анализирую диалог в группе, вытаскиваю задачи и создаю их в YouGile.\n"
        "Задачи автоматически переезжают между колонками при изменении дедлайна.\n\n"
        "Управление — в меню <b>[ Menu ]</b> слева от поля ввода.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await show_main_menu(message)


@dp.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await show_main_menu(message)


@dp.message(Command("get_key"))
async def cmd_get_key(message: Message) -> None:
    is_admin = await is_group_admin(message)
    await remember_sender(message, role="admin" if is_admin else "user")
    if not is_admin:
        await message.answer("❌ Настраивать интеграцию YouGile может только администратор группы.")
        return

    try:
        await message.delete()
    except Exception:
        pass

    settings = await get_chat_settings(message.chat.id)
    if is_chat_connected(settings):
        await message.answer(
            "🟢 <b>YouGile уже подключён.</b>\n\n"
            "Повторно вводить логин и пароль не нужно. Можно посмотреть статус, "
            "разослать задачи или переподключить проект вручную.",
            parse_mode="HTML",
            reply_markup=connected_setup_keyboard(),
        )
        return

    await ask_for_yougile_login(message)


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    await remember_sender(message)
    pending = await pending_count(message.chat.id)
    await message.answer(
        "📋 <b>Статистика сессии:</b>\n"
        f"• В буфере (ожидают AI): <b>{pending}</b> реплик\n"
        f"• Всего обработано реплик: <b>{stats.processed_messages_count}</b>\n"
        f"• Создано задач в YouGile: <b>{stats.created_tasks_count}</b>",
        parse_mode="HTML",
    )


@dp.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await remember_sender(message)
    settings = await get_chat_settings(message.chat.id)
    await message.answer(
        await render_status(message.chat.id),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(is_chat_connected(settings)),
    )


@dp.message(Command("send_tasks"))
async def cmd_send_tasks(message: Message) -> None:
    is_admin = await is_group_admin(message)
    await remember_sender(message, role="admin" if is_admin else "user")
    if not is_admin:
        await message.answer("❌ Рассылать задачи может только администратор группы.")
        return
    await send_digest_and_report(message)


@dp.callback_query(F.data == "menu_setup")
async def cb_menu_setup(callback: CallbackQuery) -> None:
    if not await is_callback_admin(callback):
        await callback.answer("Только администратор может менять подключение.", show_alert=True)
        return
    settings = await get_chat_settings(callback.message.chat.id)
    if is_chat_connected(settings):
        await callback.message.edit_text(
            "⚙️ <b>Настройки подключения</b>\n\n"
            "YouGile уже подключён. Повторная авторизация не нужна.",
            parse_mode="HTML",
            reply_markup=connected_setup_keyboard(),
        )
    else:
        await callback.message.answer(
            "🔑 YouGile ещё не подключён. Запустите авторизацию кнопкой ниже.",
            reply_markup=reconnect_keyboard(config.web_app_url),
        )
    await callback.answer()


@dp.callback_query(F.data == "setup_reconnect")
async def cb_setup_reconnect(callback: CallbackQuery) -> None:
    if not await is_callback_admin(callback):
        await callback.answer("Только администратор может переподключать YouGile.", show_alert=True)
        return
    await callback.message.answer(
        "🔁 Переподключение YouGile. Войдите заново и выберите проект.",
        reply_markup=reconnect_keyboard(config.web_app_url),
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_status")
async def cb_menu_status(callback: CallbackQuery) -> None:
    settings = await get_chat_settings(callback.message.chat.id)
    await callback.message.edit_text(
        await render_status(callback.message.chat.id),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(is_chat_connected(settings)),
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_send_tasks")
async def cb_send_tasks(callback: CallbackQuery) -> None:
    if not await is_callback_admin(callback):
        await callback.answer("Только администратор может запускать рассылку.", show_alert=True)
        return
    fake_message = callback.message
    await send_digest_and_report(fake_message)
    await callback.answer()


@dp.callback_query(F.data == "menu_process_now")
async def cb_process_now(callback: CallbackQuery) -> None:
    if not await is_callback_admin(callback):
        await callback.answer("Только администратор может запускать обработку очереди.", show_alert=True)
        return
    await callback.message.answer("🧹 Обрабатываю текущую очередь сообщений...")
    await process_chat_queue(callback.message.chat.id)
    await callback.answer()


@dp.message(F.web_app_data)
async def process_web_app_data(message: Message, state: FSMContext) -> None:
    try:
        payload = json.loads(message.web_app_data.data)
    except (json.JSONDecodeError, AttributeError) as exc:
        await message.answer(f"🔴 Ошибка разбора Web App данных: {exc}", reply_markup=ReplyKeyboardRemove())
        return

    login = (payload.get("login") or "").strip()
    password = (payload.get("password") or "").strip()
    if not login or not password:
        await message.answer("🔴 Логин или пароль не переданы.", reply_markup=ReplyKeyboardRemove())
        return

    status = await message.answer("⏳ Подключаюсь к YouGile...", reply_markup=ReplyKeyboardRemove())
    companies, status_code = await get_companies(login, password)
    if not companies:
        text = "🔴 Нет доступных компаний." if status_code in (200, 201) else "🔴 Неверный логин или пароль."
        await status.edit_text(text)
        return

    await state.set_state(AuthStates.waiting_for_company)
    await state.update_data(login=login, password=password)
    await status.delete()
    await message.answer(
        f"🍏 <b>Успешный вход!</b>\nАккаунт: <code>{login}</code>\n\nВыберите компанию:",
        parse_mode="HTML",
        reply_markup=companies_keyboard(companies),
    )


@dp.callback_query(AuthStates.waiting_for_company, F.data.startswith("company_"))
async def process_company_choice(callback: CallbackQuery, state: FSMContext) -> None:
    company_id = callback.data.removeprefix("company_")
    auth_data = await state.get_data()

    await callback.message.edit_text("⏳ Получаю или создаю API-ключ YouGile...")
    api_key, status_code = await get_or_create_api_key(
        auth_data["login"],
        auth_data["password"],
        company_id,
    )
    if not api_key:
        await callback.message.edit_text(
            "🔴 Сервер не вернул API-ключ.\n\n"
            f"HTTP статус последней попытки: {status_code}.\n"
            "Я попробовал получить существующий ключ и создать новый автоматически. "
            "Проверьте права администратора/организатора в выбранной компании."
        )
        await state.clear()
        await callback.answer()
        return

    await callback.message.edit_text("⏳ API-ключ получен. Загружаю список проектов...")
    projects, status_code = await get_projects(api_key)
    if not projects:
        await callback.message.edit_text(f"🔴 Не удалось получить проекты (HTTP {status_code}).")
        await state.clear()
        await callback.answer()
        return

    await state.set_state(AuthStates.waiting_for_project)
    await state.update_data(api_key=api_key, company_id=company_id)
    await callback.message.edit_text(
        "📁 Выберите проект для создания задач:",
        reply_markup=projects_keyboard(projects),
    )
    await callback.answer()


@dp.callback_query(AuthStates.waiting_for_project, F.data.startswith("project_"))
async def process_project_choice(callback: CallbackQuery, state: FSMContext) -> None:
    project_id = callback.data.removeprefix("project_")
    auth_data = await state.get_data()
    api_key = auth_data["api_key"]

    await callback.message.edit_text("⏳ Разворачиваю доску «Канбан AI PM» и 5 колонок...")
    board_result = await asyncio.to_thread(create_ai_pm_board, api_key, project_id)
    if not board_result["success"]:
        await callback.message.edit_text("🔴 Не удалось создать структуру колонок. Проверьте права токена.")
        await state.clear()
        await callback.answer()
        return

    await save_board_settings(callback.message.chat.id, api_key, project_id, board_result)
    await asyncio.to_thread(update_env_file, "YOUGILE_API_KEY", api_key)
    await asyncio.to_thread(update_env_file, "YOUGILE_COMPANY_ID", auth_data["company_id"])

    await callback.message.edit_text(
        "🚀 <b>Структура AI-доски успешно развёрнута!</b>\n\n"
        "Создана доска <b>«Канбан AI PM»</b> с 5 колонками:\n"
        "👥 Участники → 📥 Без дедлайна → 📅 Дедлайн есть → 🔥 Горит → ✅ Выполнено\n\n"
        "<b>Что дальше:</b> просто общайтесь в группе — бот сам создаст задачи "
        "и карточки участников.",
        parse_mode="HTML",
    )
    await state.clear()
    await callback.answer()


@dp.message(F.text)
async def handle_all_messages(message: Message) -> None:
    if not message.text or message.text.startswith("/"):
        return

    chat_id = message.chat.id
    username = username_from_message_user(message.from_user)
    await upsert_chat_member(chat_id, message.from_user.id, username)
    await save_pending_message(chat_id, username, message.text)
    chat_start_times.setdefault(chat_id, time.monotonic())
    await schedule_chat_processing(chat_id)


async def set_bot_commands() -> None:
    await bot.set_my_commands([
        BotCommand(command="menu", description="🧭 Открыть панель управления"),
        BotCommand(command="get_key", description="🔑 Авторизоваться / Выбрать проект YouGile"),
        BotCommand(command="status", description="📊 Проверить подключение и очередь"),
        BotCommand(command="send_tasks", description="📨 Разослать задачи ответственным"),
        BotCommand(command="stats", description="📋 Статистика очереди чата"),
        BotCommand(command="start", description="🤖 Информация о боте"),
    ])


async def main() -> None:
    await init_db()
    await set_bot_commands()
    asyncio.create_task(monitor_deadlines(bot, config.migration_interval))

    print("[SERVER] AI PM запущен. Обвязка разнесена по сервисам.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
