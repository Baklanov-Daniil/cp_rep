import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("YOUGILE_API_KEY")
COLUMN_ID = os.getenv("YOUGILE_COLUMN_ID")

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

def get_tasks_from_column():
    # Запрашиваем задачи, отфильтрованные по нашей колонке
    url = f"https://ru.yougile.com/api-v2/tasks?columnId={COLUMN_ID}"
    
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        # API YouGile возвращает словарь, где в ключе 'content' лежит список задач
        tasks = data.get("content", [])
        
        print(f"🍏 Успешно подключено! Найдено задач в колонке: {len(tasks)}\n")
        for i, task in enumerate(tasks, 1):
            print(f"{i}. 📋 Название: {task.get('title')}")
            print(f"   🆔 ID задачи: {task.get('id')}")
            print(f"   📝 Описание:\n{task.get('description')}")
            print("-" * 40)
    else:
        print(f"🔴 Ошибка запроса: {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    if not API_KEY or not COLUMN_ID:
        print("Ошибка: Проверь, заполнены ли YOUGILE_API_KEY и YOUGILE_COLUMN_ID в .env")
    else:
        get_tasks_from_column()