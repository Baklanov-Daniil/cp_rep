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

def test_gpt():
    prompt = """Ты — AI Project Manager. Извлеки задачи из текста.
Верни СТРОГО JSON в формате: {"tasks": [{"title": "суть", "assignee": "имя", "deadline": "YYYY-MM-DD", "priority": "low|medium|high"}]}
Если задач нет, верни {"tasks": []}. Не пиши ничего кроме JSON.

Текст:
[Иван]: Привет! Нужно срочно пофиксить баг с авторизацией до пятницы
[Маша]: Окей, возьму. А ещё надо сделать дизайн главной страницы к среде"""

    payload = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.2,
            "maxTokens": "1000"
        },
        "messages": [{"role": "user", "text": prompt}]
    }

    print("Отправляю запрос в YandexGPT...")
    
    try:
        response = requests.post(URL, headers=HEADERS, json=payload)
        response.raise_for_status()
        
        result = response.json()
        answer_text = result['result']['alternatives'][0]['message']['text']
        
        print(f"\nСырой ответ:\n{answer_text}\n")
        
        match = re.search(r'\{.*\}', answer_text, re.DOTALL)
        if match:
            answer_text = match.group(0)
        
        tasks_data = json.loads(answer_text)
        print(f"Успех! Найдено задач: {len(tasks_data.get('tasks', []))}")
        
        for i, task in enumerate(tasks_data.get('tasks', []), 1):
            print(f"\nЗадача {i}:")
            print(f"   Название: {task.get('title')}")
            print(f"   Ответственный: {task.get('assignee', 'Не указан')}")
            print(f"   Дедлайн: {task.get('deadline', 'Не указан')}")
            print(f"   Приоритет: {task.get('priority', 'Не указан')}")
        
    except requests.exceptions.HTTPError as e:
        print(f"Ошибка HTTP {e.response.status_code}: {e.response.text}")
    except json.JSONDecodeError as e:
        print(f"Ошибка парсинга JSON: {e}")
        print(f"Получен текст: {answer_text}")
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_gpt()