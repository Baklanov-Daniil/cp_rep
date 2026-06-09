import json
import os
import asyncio
import requests
from datetime import datetime
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    WebAppInfo, 
    ReplyKeyboardRemove, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery
)
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import ChatMember, ChatSettings
from bot.handlers.tasks import create_yougile_board_and_columns

router = Router()

class AuthStates(StatesGroup):
    waiting_for_company = State()
    waiting_for_project = State()

@router.message(Command("start"))
async def cmd_start(m: Message, cm: ChatMember):
    if not cm.is_authorized:
        return await m.answer("🤖 Доступ ограничен. Ожидайте одобрения администратора.")
    await m.answer("🤖 <b>AI PM готов к мониторингу группы.</b> Настройки панели: /admin_panel", parse_mode="HTML")

@router.message(Command("get_key"))
async def cmd_get_key(m: Message, cm: ChatMember):
    if cm.role != "admin":
        return await m.answer("❌ Настраивать интеграцию YouGile может только администратор группы.")
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔑 Войти в YouGile", web_app=WebAppInfo(url=os.getenv("WEB_APP_URL")))]], 
        resize_keyboard=True
    )
    await m.answer("Пройдите авторизацию в YouGile:", reply_markup=kb)

@router.message(F.web_app_data)
async def process_web_app(m: Message, state: FSMContext, cm: ChatMember):
    if cm.role != "admin":
        return
    
    try:
        data = json.loads(m.web_app_data.data)
    except Exception:
        await m.answer("❌ Ошибка при обработке данных Web App.")
        return

    login = data.get("login", "").strip()
    password = data.get("password", "").strip()
    status = await m.answer("⏳ Подключение к инстансу...", reply_markup=ReplyKeyboardRemove())
    
    companies_url = "https://ru.yougile.com/api-v2/auth/companies"
    auth_payload = {"login": login, "password": password}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    resp = await asyncio.to_thread(requests.post, companies_url, json=auth_payload, headers=headers, timeout=15)
    
    if resp.status_code == 401:
        auth_payload["email"] = auth_payload.pop("login")
        resp = await asyncio.to_thread(requests.post, companies_url, json=auth_payload, headers=headers, timeout=15)

    if resp.status_code not in (200, 201):
        await status.edit_text("❌ Ошибка авторизации. Проверьте логин и пароль.")
        return

    companies_data = resp.json()

    if isinstance(companies_data, dict):
        if "content" in companies_data:
            companies_list = companies_data["content"]
        elif "data" in companies_data:
            companies_list = companies_data["data"]
        else:
            companies_list = [companies_data]
    else:
        companies_list = companies_data

    if not companies_list:
        await status.edit_text("❌ Доступных компаний не найдено.")
        return

    await state.set_state(AuthStates.waiting_for_company)
    await state.update_data(auth_payload=auth_payload, password=password)
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=c.get("name", "Без названия"), callback_data=f"comp_{c.get('id')}")] 
        for c in companies_list
    ])
    await status.delete()
    await m.answer("🍏 Успешный вход! Выберите компанию YouGile:", reply_markup=markup)

@router.callback_query(AuthStates.waiting_for_company, F.data.startswith("comp_"))
async def comp_res(cb: CallbackQuery, state: FSMContext):
    company_id = cb.data.removeprefix("comp_")
    sd = await state.get_data()
    auth_payload = sd["auth_payload"]
    
    await cb.message.edit_text("⏳ Получение токенов доступа...")
    token_get_url = "https://ru.yougile.com/api-v2/auth/keys/get"
    token_create_url = "https://ru.yougile.com/api-v2/auth/keys"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    payload = auth_payload.copy()
    payload["companyId"] = company_id

    # Пробуем получить существующий ключ
    r = await asyncio.to_thread(requests.post, token_get_url, json=payload, headers=headers, timeout=15)
    
    api_key = None
    if r.status_code in (200, 201):
        body = r.json()
        keys_list = body if isinstance(body, list) else body.get("content", [])
        active_key_obj = next((k for k in keys_list if isinstance(k, dict) and not k.get("deleted")), None)
        if active_key_obj:
            api_key = active_key_obj.get("key")

    # Если ключей нет — автоматически создаем новый
    if not api_key:
        payload["title"] = f"AI PM Bot Key ({datetime.now().strftime('%d.%m.%Y')})"
        cr = await asyncio.to_thread(requests.post, token_create_url, json=payload, headers=headers, timeout=15)
        if cr.status_code in (200, 201):
            c_body = cr.json()
            api_key = c_body.get("key") if isinstance(c_body, dict) else None

    if not api_key:
        await cb.message.edit_text(
            "🔴 Сервер не вернул API-ключ.\n\n"
            "<b>Как исправить:</b>\n"
            "1. Зайдите в YouGile через браузер.\n"
            "2. Органайзер -> Интеграции -> API-ключи.\n"
            "3. Создайте один ключ вручную и повторите команду /get_key.",
            parse_mode="HTML"
        )
        await state.clear()
        return

    pr_url = "https://ru.yougile.com/api-v2/projects"
    proj_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    
    pr = await asyncio.to_thread(requests.get, pr_url, headers=proj_headers, timeout=15)
    if pr.status_code != 200:
        await cb.message.edit_text("❌ Ошибка получения списка проектов.")
        await state.clear()
        return

    projs_data = pr.json()
    projs = projs_data if isinstance(projs_data, list) else projs_data.get("content", [])

    if not projs:
        await cb.message.edit_text("❌ В этой компании нет доступных проектов.")
        await state.clear()
        return

    await state.set_state(AuthStates.waiting_for_project)
    await state.update_data(api_key=api_key)
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=p.get("title", "Без названия"), callback_data=f"proj_{p.get('id')}")] 
        for p in projs
    ])
    await cb.message.edit_text("📁 Выберите проект для привязки к чату:", reply_markup=markup)

@router.callback_query(AuthStates.waiting_for_project, F.data.startswith("proj_"))
async def proj_res(cb: CallbackQuery, state: FSMContext, session: AsyncSession, chat_settings: ChatSettings):
    pid = cb.data.removeprefix("proj_")
    sd = await state.get_data()
    api_key = sd["api_key"]
    
    await cb.message.edit_text("⏳ Инициализация Agile-доски и колонок...")
    
    res = await asyncio.to_thread(create_yougile_board_and_columns, api_key, pid)
    if res["success"]:
        chat_settings.yougile_api_key = api_key
        chat_settings.project_id = pid
        chat_settings.board_id = res["board_id"]
        chat_settings.col_participants = res["columns"]["participants"]
        chat_settings.col_no_deadline = res["columns"]["no_deadline"]
        chat_settings.col_has_deadline = res["columns"]["has_deadline"]
        chat_settings.col_urgent = res["columns"]["urgent_deadline"]
        chat_settings.col_done = res["columns"]["done"]
        await session.commit()
        await cb.message.edit_text("🚀 Настройка завершена! Локальные параметры доски зафиксированы.")
    else:
        await cb.message.edit_text("❌ Критическая ошибка при генерации колонок.")
    await state.clear()