"""
LLM-модуль: парсинг задач и участников через YandexGPT.
"""

import json
import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY   = os.getenv("YANDEX_API_KEY")
FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")
URL       = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Api-Key {API_KEY}",
    "x-folder-id": FOLDER_ID,
}

# ---------------------------------------------------------------------------
# Промпт
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """Ты — AI Project Manager. Анализируй переписку и извлекай структурированные данные.

Верни СТРОГО валидный JSON (без markdown-блоков, без пояснений):
{{
  "tasks": [
    {{
      "title": "конкретная суть задачи одной фразой",
      "assignee": "имя или @username исполнителя, если упоминается (иначе null)",
      "deadline_day":  "today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|YYYY-MM-DD или null",
      "deadline_week": "current|next",
      "deadline_time": "HH:MM",
      "priority":      "low|medium|high"
    }}
  ],
  "participants": [
    {{
      "full_name":    "Имя Фамилия (как упоминается в тексте)",
      "tg_username":  "@username или null"
    }}
  ]
}}

Правила для задач:
- "title": коротко и конкретно, без воды.
- "assignee": если исполнитель явно назван или очевиден из контекста.
- "deadline_day":
    - "today" / "tomorrow" для сегодня/завтра.
    - День недели (monday…sunday) если привязка к дню недели.
    - "YYYY-MM-DD" если указана конкретная дата.
    - null если дедлайна нет.
- "deadline_week": "next" если сказано «следующая неделя», иначе "current".
- "deadline_time": точное время если есть, иначе "18:00".
- "priority": "high" если «срочно/критично», "low" если явно второстепенное, иначе "medium".
- Не выдумывай задачи — только то, что явно обсуждалось.
- Если задач нет, верни пустой список: "tasks": [].

Правила для участников:
- Извлеки ВСЕХ людей, упомянутых в переписке: отправителей, исполнителей, кого упоминают.
- "full_name": имя/фамилия как в тексте.
- "tg_username": @username если упоминается рядом с именем или от его имени.
- Если участников нет/неизвестны — верни пустой список: "participants": [].

Текст переписки:
{text}"""

# ---------------------------------------------------------------------------
# Публичная функция
# ---------------------------------------------------------------------------

def parse_tasks_from_text(text: str) -> tuple[list[dict], list[dict]]:
    """
    Разбирает переписку через YandexGPT.

    Возвращает:
        (tasks, participants)
        tasks        : список задач
        participants : список участников {"full_name", "tg_username"}
    """
    prompt = _PROMPT_TEMPLATE.format(text=text)

    payload = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.1,
            "maxTokens": "2000",
        },
        "messages": [{"role": "user", "text": prompt}],
    }

    try:
        response = requests.post(URL, headers=HEADERS, json=payload, timeout=30)
        response.raise_for_status()

        raw = response.json()["result"]["alternatives"][0]["message"]["text"].strip()

        # Убираем возможные ```json ... ``` обёртки
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.IGNORECASE)

        # Ищем JSON-объект
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            raw = match.group(0)

        data = _safe_parse(raw)
        tasks        = _clean_tasks(data.get("tasks", []))
        participants = _clean_participants(data.get("participants", []))

        print(f"[LLM] Извлечено задач: {len(tasks)}, участников: {len(participants)}")
        return tasks, participants

    except Exception as e:
        print(f"[LLM] Ошибка запроса: {e}")
        return [], []


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _safe_parse(text: str) -> dict:
    """Пытается распарсить JSON; в крайнем случае собирает объекты по кускам."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Аварийный вариант: выдёргиваем отдельные объекты задач
    task_blocks   = re.findall(r'\{[^{}]*"title"[^{}]*\}', text, re.DOTALL)
    person_blocks = re.findall(r'\{[^{}]*"full_name"[^{}]*\}', text, re.DOTALL)

    tasks, people = [], []
    for block in task_blocks:
        try:
            obj = json.loads(block)
            if obj.get("title"):
                tasks.append(obj)
        except Exception:
            pass
    for block in person_blocks:
        try:
            obj = json.loads(block)
            if obj.get("full_name"):
                people.append(obj)
        except Exception:
            pass

    return {"tasks": tasks, "participants": people}


_PLACEHOLDER_TITLES = {"суть задачи", "название", "задача", "title", ""}

def _clean_tasks(raw: list) -> list[dict]:
    result = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        title = (t.get("title") or "").strip()
        if title.lower() in _PLACEHOLDER_TITLES:
            continue
        # Нормализуем поля
        t.setdefault("deadline_week", "current")
        t.setdefault("deadline_time", "18:00")
        t.setdefault("priority", "medium")
        # null-строки → None
        for field in ("assignee", "deadline_day"):
            if t.get(field) in ("null", "None", "", None):
                t[field] = None
        result.append(t)
    return result


_JUNK_NAMES = {
    "", "null", "none", "имя фамилия", "имя", "фамилия",
    "name", "fullname", "full_name", "участник", "пользователь", "user",
}

def _clean_participants(raw: list) -> list[dict]:
    result = []
    seen: set[str] = set()

    for p in raw:
        if not isinstance(p, dict):
            continue

        name = (p.get("full_name") or "").strip()
        tg   = (p.get("tg_username") or "").strip().lstrip("@")
        tg_clean = f"@{tg}" if tg else None

        # Если имя мусорное — используем @username как отображаемое имя
        if name.lower() in _JUNK_NAMES or not name:
            if tg_clean:
                name = tg_clean
            else:
                continue

        # Дедупликация по имени и по @username
        key    = name.lower()
        tg_key = tg_clean.lower() if tg_clean else None
        if key in seen or (tg_key and tg_key in seen):
            continue
        seen.add(key)
        if tg_key:
            seen.add(tg_key)

        result.append({"full_name": name, "tg_username": tg_clean})

    return result