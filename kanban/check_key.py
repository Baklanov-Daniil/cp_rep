import requests

EMAIL = "daniil.baklanov04leonidovich@gmail.com"
PASSWORD = "10QPALZM"
COMPANY_ID = "d2d009e4-28e7-4701-98f9-e5ca054b7b1e"

response = requests.get(
    "https://yougile.com/data/api-v1/keys",
    headers={"Content-Type": "application/json"},
    json={
        "email": EMAIL,
        "password": PASSWORD,
        "companyId": COMPANY_ID
    }
)

print("Статус:", response.status_code)
print("Ответ:", response.json())

data = response.json()
if data.get("result") == "ok":
    keys = data.get("keys", {})
    print(f"\nНайдено ключей: {len(keys)}")
    for key, info in keys.items():
        print(f"  • {key[:20]}... (создан: {info.get('timestamp')})")