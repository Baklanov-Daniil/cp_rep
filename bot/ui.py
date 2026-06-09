from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo


def main_menu_keyboard(is_connected: bool) -> InlineKeyboardMarkup:
    setup_text = "⚙️ Настройки подключения" if is_connected else "🔑 Подключить YouGile"
    rows = [
        [InlineKeyboardButton(text=setup_text, callback_data="menu_setup")],
        [
            InlineKeyboardButton(text="📊 Статус", callback_data="menu_status"),
            InlineKeyboardButton(text="📨 Разослать задачи", callback_data="menu_send_tasks"),
        ],
        [InlineKeyboardButton(text="🧹 Обработать очередь", callback_data="menu_process_now")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def connected_setup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Показать статус", callback_data="menu_status")],
        [InlineKeyboardButton(text="🔁 Переподключить YouGile", callback_data="setup_reconnect")],
        [InlineKeyboardButton(text="📨 Разослать задачи", callback_data="menu_send_tasks")],
    ])


def reconnect_keyboard(web_app_url: str) -> ReplyKeyboardMarkup:
    return yougile_login_keyboard(web_app_url)


def yougile_login_keyboard(web_app_url: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔑 Вход в YouGile", web_app=WebAppInfo(url=web_app_url))]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def companies_keyboard(companies: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=company.get("name") or f"Компания {company.get('id', '?')}",
            callback_data=f"company_{company.get('id')}",
        )]
        for company in companies
    ])


def projects_keyboard(projects: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=project.get("title") or f"Проект {project.get('id', '?')}",
            callback_data=f"project_{project.get('id')}",
        )]
        for project in projects
    ])
