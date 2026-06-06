import requests

API_KEY = "AkGEjwzOGMyj0wAsS1inmcK8qGBiR4wQr946mkOb3wOwJ97jMiSpv6a6PvuoQ4Mn"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# Запрашиваем все колонки компании
response = requests.get("https://ru.yougile.com/api-v2/columns", headers=headers)

if response.status_code == 200:
    columns = response.json().get("content", [])
    print("\n🍏 Доступные колонки в YouGile:")
    print("=" * 50)
    for col in columns:
        print(f"📋 Название: {col.get('title')}")
        print(f"🆔 ID колонки: {col.get('id')}")
        print(f"📌 Проект ID: {col.get('projectId')}")
        print("-" * 50)
else:
    print(f"🔴 Ошибка: {response.status_code}, {response.text}")