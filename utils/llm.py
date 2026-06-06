import requests
import json
import os
import re
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
    Извлекает задачи из текста переписки через YandexGPT
    """
    prompt = f"""Ты — AI Project Manager. Извлеки ВСЕ задачи и поручения из текста переписки.

Верни СТРОГО JSON в формате: {{"tasks": [{{"title": "суть задачи", "assignee": "имя или @username", "deadline": "сегодня|завтра|понедельник|2026-06-10", "priority": "low|medium|high"}}]}}

Что считать задачей:
- ЛЮБОЕ поручение или просьба ("поправь", "сделай", "напиши", "зайди", "посмотри", "проверь")
- Встречи и созвоны ("зайти в созвон", "встретиться", "позвонить")
- Напоминания с дедлайном ("надо зайти", "не забудь", "важно сделать")
- Упоминания с действием (@username + глагол)

Правила извлечения:
- Извлекай дедлайн ВСЕГДА, если есть ЛЮБОЕ указание на время: "сегодня", "завтра", "к обеду", "в 15:00", "на этой неделе", "24:00"
- Если точной даты нет — пиши как есть: "сегодня", "завтра", "в пятницу"
- Если ответственный не указан явно — не добавляй поле assignee
- Приоритет: high если есть "срочно/важно/ASAP/быстро/надо/нужно", medium по умолчанию, low если "когда будет время"
- Если задач нет — верни {{"tasks": []}}
- Не пиши ничего кроме JSON

Текст переписки:
{text}"""

    payload = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.2,
            "maxTokens": "1000"
        },
        "messages": [{"role": "user", "text": prompt}]
    }

    try:
        response = requests.post(URL, headers=HEADERS, json=payload, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        answer_text = result['result']['alternatives'][0]['message']['text']
        
        match = re.search(r'\{.*\}', answer_text, re.DOTALL)
        if match:
            answer_text = match.group(0)
        
        tasks_data = json.loads(answer_text)
        tasks = tasks_data.get('tasks', [])
        
        print(f"LLM нашла {len(tasks)} задач")
        return tasks
        
    except requests.exceptions.Timeout:
        print("Превышено время ожидания ответа от YandexGPT")
        return []
    except requests.exceptions.RequestException as e:
        print(f"Ошибка сети: {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"LLM вернул невалидный JSON: {e}")
        print(f"Получен текст: {answer_text}")
        return []
    except Exception as e:
        print(f"Неизвестная ошибка: {e}")
        return []