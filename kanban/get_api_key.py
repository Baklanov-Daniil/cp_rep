import requests

EMAIL = "daniil.baklanov04leonidovich@gmail.com"
PASSWORD = "10QPALZM"
COMPANY_ID = "d2d009e4-28e7-4701-98f9-e5ca054b7b1e" 


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
    print("\nТвой API ключ:")
    print(api_key)
    print("\nСохрани его в config.py!")
else:
    print("\nОшибка:", data)