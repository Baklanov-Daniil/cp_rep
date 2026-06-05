import requests

# Твои данные от YouGile
EMAIL = "твой_email@example.com"
PASSWORD = "твой_пароль"
COMPANY_ID = "d2d009e4-28e7-4701-98f9-e5ca054b7b1e" 

# Запрос на создание API-ключа
response = requests.post(
    "https://yougile.com/data/api-v1/keys",
    headers={"Content-Type": "application/json"},
    json={
        "email": EMAIL,
        "password": PASSWORD,
        "companyId": COMPANY_ID
    }
)

data = response.json()
print("Ответ API:", data)

if data.get("result") == "ok":
    api_key = data.get("key")
    print("\n✅ Твой API ключ:")
    print(api_key)
    print("\nСохрани его в config.py!")
else:
    print("\n❌ Ошибка:", data)