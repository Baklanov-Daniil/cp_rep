import requests
import json
import os
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('YANDEX_API_KEY')
FOLDER_ID = os.getenv('YANDEX_FOLDER_ID')
URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Api-Key {API_KEY}",
    "x-folder-id": FOLDER_ID
}

def parse_tasks_from_text(text: str) -> list:
    """
    Извлекает задачи из текста переписки через YandexGPT.
    Возвращает относительные маркеры времени для точного расчета на Python.
    """
    prompt = f"""Ты — AI Project Manager. Твоя задача — извлечь задачи из текста переписки.

Верни СТРОГО JSON в формате:
{{"tasks": [{{"title": "суть задачи", "assignee": "имя", "deadline_day": "today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|YYYY-MM-DD", "deadline_week": "current|next", "deadline_time": "HH:MM", "priority": "low|medium|high"}}]}}

Правила вычисления полей дедлайна:
1. "deadline_day":
   - Если дедлайн привязан к дню недели (например, "в пятницу", "до четверга"), укажи этот день недели на английском (monday, tuesday и т.д.).
   - Если сказано "сегодня" — пиши "today", если "завтра" — "tomorrow".
   - Если указана конкретная дата (например, "25 декабря"), переведи её в "YYYY-MM-DD".
   - Если дедлайна нет — вообще не добавляй это поле.
2. "deadline_week":
   - Если контекст означает текущую неделю (например, "в эту пятницу", "до конца недели") — пиши "current".
   - Если контекст означает следующую неделю (например, "до конца следующей недели", "в пятницу на следующей неделе") — пиши "next".
   - По умолчанию (если не уточняется) — пиши "current".
3. "deadline_time":
   - Если указано точное время (например, "до 19:30", "к 15:00"), запиши его в формате "HH:MM".
   - Если время не указано, пиши "18:00".

Текст переписки:
{text}"""

    payload = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.1,
            "maxTokens": "1500"
        },
        "messages": [{"role": "user", "text": prompt}]
    }

    try:
        response = requests.post(URL, headers=HEADERS, json=payload, timeout=30)
        response.raise_for_status()
        
        answer_text = response.json()['result']['alternatives'][0]['message']['text'].strip()
        # Очищаем от возможных markdown оберток типа ```json ... ```
        answer_text = re.sub(r'^```json\s*|\s*```$', '', answer_text, flags=re.IGNORECASE)
        
        match = re.search(r'\{.*\}', answer_text, re.DOTALL)
        if match:
            answer_text = match.group(0)
            
        try:
            tasks_data = json.loads(answer_text)
            return tasks_data.get('tasks', [])
        except json.JSONDecodeError:
            # Аварийный нарезчик на случай Extra Data
            task_blocks = re.findall(r'\{[^{}]*\}', answer_text)
            valid_tasks = []
            for block in task_blocks:
                try:
                    t = json.loads(block)
                    if "title" in t and t["title"] not in ["суть задачи", None, ""]:
                        valid_tasks.append(t)
                except: continue
            return valid_tasks
    except Exception as e:
        print(f"[LLM] Ошибка: {e}")
        return []