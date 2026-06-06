from datetime import datetime, timedelta
import re
from typing import Optional

# Словарь для русских названий месяцев
MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

def parse_deadline(text: str) -> Optional[str]:
    """
    Преобразует относительные даты и время в формат YYYY-MM-DD HH:MM.
    """
    if not text:
        return None
    
    text = text.lower().strip()
    today = datetime.now()
    time_str = "00:00"
    
    # Проверяем время суток
    if "обед" in text or "полдень" in text:
        time_str = "12:00"
    elif "вечер" in text:
        time_str = "18:00"
    elif "утро" in text:
        time_str = "09:00"
    elif "ночь" in text:
        time_str = "23:59"
    
    # Точное время
    time_match = re.search(r'(\d{1,2}):(\d{2})', text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        time_str = f"{hour:02d}:{minute:02d}"
    
    # "завтра"
    if "завтра" in text:
        tomorrow = today + timedelta(days=1)
        return f"{tomorrow.strftime('%Y-%m-%d')} {time_str}"
    
    # "послезавтра"
    if "послезавтра" in text:
        day_after = today + timedelta(days=2)
        return f"{day_after.strftime('%Y-%m-%d')} {time_str}"
    
    # "сегодня"
    if "сегодня" in text:
        return f"{today.strftime('%Y-%m-%d')} {time_str}"
    
    # Дни недели
    days_map = {
        "понедельник": 0, "пн": 0,
        "вторник": 1, "вт": 1,
        "среда": 2, "ср": 2,
        "четверг": 3, "чт": 3,
        "пятница": 4, "пт": 4,
        "суббота": 5, "сб": 5,
        "воскресенье": 6, "вс": 6,
    }
    
    for day_name, weekday_num in days_map.items():
        if day_name in text:
            days_ahead = weekday_num - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            target_date = today + timedelta(days=days_ahead)
            return f"{target_date.strftime('%Y-%m-%d')} {time_str}"
    
    # Абсолютные даты YYYY-MM-DD
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)} {time_str}"
    
    return None


def format_deadline_for_display(deadline: str) -> str:
    """
    Форматирует дедлайн для красивого отображения на русском.
    YYYY-MM-DD HH:MM → "7 июня 2026 в 00:00"
    """
    if not deadline:
        return "не указан"
    
    try:
        if len(deadline) > 10:  # Есть время
            dt = datetime.strptime(deadline, "%Y-%m-%d %H:%M")
            return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year} в {dt.strftime('%H:%M')}"
        else:
            dt = datetime.strptime(deadline, "%Y-%m-%d")
            return f"{dt.day} {MONTHS_RU[dt.month]} {dt.year}"
    except ValueError:
        return deadline