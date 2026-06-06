import requests
from config import YOUGILE_API_KEY

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"YOUGILE-KEY${YOUGILE_API_KEY}"
}

COLUMN_ID = "сюда_вставь_id_колонки"

response = requests.post(
    "https://yougile.com/data/api-v1/tasks",
    headers=HEADERS,
    json={
        "title": "Тестовая задача от бота",
        "location": COLUMN_ID,
        "description": "Создано автоматически через API"
    }
)

data = response.json()
print("Ответ:", data)

if data.get("result") == "ok":
    task_id = data.get("id")
    print(f"\n✅ Задача создана!")
    print(f"ID задачи: {task_id}")
else:
    print(f"\n❌ Ошибка: {data}")