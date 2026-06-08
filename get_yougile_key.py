import requests

LOGIN = "dexffe@gmail.com"
PASSWORD = "Artem230906."

headers = {
    "Content-Type": "application/json",
    "Accept": "application/json"
}

print("1. Получаем полный список компаний для аккаунта...")
companies_url = "https://ru.yougile.com/api-v2/auth/companies"
auth_payload = {
    "login": LOGIN,
    "password": PASSWORD
}

comp_response = requests.post(companies_url, json=auth_payload, headers=headers)

# Пробуем альтернативный вариант payload, если первый вернул 401
if comp_response.status_code == 401:
    print("🔄 Роут с 'login' вернул 401, пробуем 'email'...")
    auth_payload["email"] = auth_payload.pop("login")
    comp_response = requests.post(companies_url, json=auth_payload, headers=headers)

if comp_response.status_code not in [200, 201]:
    print(f"🔴 Ошибка получения компаний ({comp_response.status_code}): {comp_response.text}")
    exit()

companies_data = comp_response.json()

# Приводим ответ API к единому списку, учитывая особенности YouGile API
if isinstance(companies_data, dict):
    if "content" in companies_data:
        companies_list = companies_data["content"]
    elif "data" in companies_data:
        companies_list = companies_data["data"]
    else:
        companies_list = [companies_data]
else:
    companies_list = companies_data

if not companies_list:
    print("🔴 Компаний не найдено. Проверьте логин/пароль.")
    exit()

print(f"\n🍏 Успешно найдено компаний: {len(companies_list)}")
print("=" * 60)

# Перебираем все компании в цикле и выводим их данные
for index, company in enumerate(companies_list, start=1):
    company_name = company.get("name", "Без названия")
    company_id = company.get("id", "ID отсутствует")
    
    print(f"{index}. Название: '{company_name}'")
    print(f"   🆔 UUID: {company_id}")
    print("-" * 40)

print("=" * 60)
print("Выберите нужный UUID для настройки вашего .env файла или создания API-ключа.")