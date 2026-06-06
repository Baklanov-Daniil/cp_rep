import requests
from config import YOUGILE_API_KEY

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"{YOUGILE_API_KEY}"
}

print("🔍 Автоматическое определение ID колонки...")
print()

# Попытка 1: Получить задачу по ID (если знаешь ID созданной вручную задачи)
print("Вариант 1: Если создал задачу вручную")
print("Введи ID задачи (или нажми Enter для пропуска):")
task_id = input("ID задачи: ").strip()

if task_id:
    response = requests.get(
        "https://yougile.com/data/api-v1/tasks",
        headers=HEADERS,
        json={"id": task_id}
    )
    
    data = response.json()
    if data.get("result") == "ok":
        tasks = data.get("list", [])
        if tasks:
            task = tasks[0]
            location = task.get("location")
            print(f"\n✅ ID колонки найден: {location}")
            print(f"   Задача: {task.get('title')}")
            
            # Сохраняем в config.py
            with open("config.py", "a", encoding="utf-8") as f:
                f.write(f'\nYOUGILE_COLUMN_ID = "{location}"\n')
            print("✅ Сохранено в config.py!")
            exit(0)