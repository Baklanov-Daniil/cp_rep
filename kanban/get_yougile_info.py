import requests
from config import YOUGILE_API_KEY

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"{YOUGILE_API_KEY}"
}

print("📋 Получаем список всех задач...")

# Получаем все задачи (без указания id)
response = requests.get(
    "https://yougile.com/data/api-v1/tasks",
    headers=HEADERS
)

data = response.json()
print(f"Статус: {response.status_code}")

if data.get("result") == "ok":
    tasks = data.get("list", [])
    print(f"\n✅ Найдено задач: {len(tasks)}")
    
    if tasks:
        # Берём первую задачу
        task = tasks[0]
        print(f"\nПервая задача:")
        print(f"  • Название: {task.get('title')}")
        print(f"  • ID задачи: {task.get('id')}")
        print(f"  • ID колонки (location): {task.get('location')}")
        
        location = task.get("location")
        print(f"\n📝 Добавь это в config.py:")
        print(f'YOUGILE_COLUMN_ID = "{location}"')
        
        # Сохраняем в config.py
        with open("config.py", "a", encoding="utf-8") as f:
            f.write(f'\nYOUGILE_COLUMN_ID = "{location}"\n')
        print("✅ Сохранено в config.py!")
else:
    print(f"❌ Ошибка: {data}")