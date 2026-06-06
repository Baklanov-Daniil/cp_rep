import requests

LOGIN = ""
PASSWORD = ""

headers = {
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# ШАГ 1: Запрашиваем список компаний, чтобы узнать точный UUID
print("1. Получаем список компаний для аккаунта...")
companies_url = "https://ru.yougile.com/api-v2/auth/companies"
auth_payload = {
    "login": LOGIN,
    "password": PASSWORD
}

comp_response = requests.post(companies_url, json=auth_payload, headers=headers)

if comp_response.status_code not in [200, 201]:
    print(f"🔴 Ошибка получения компаний ({comp_response.status_code}): {comp_response.text}")
    exit()

companies = comp_response.json()

# Если пришел словарь, а не список, проверим, где лежат данные
if isinstance(companies, dict) and "content" in companies:
    companies = companies["content"]
elif isinstance(companies, dict):
    companies = [companies]

if not companies:
    print("🔴 Компаний не найдено. Проверь логин/пароль.")
    exit()

# Берем первую компанию из списка
first_company = companies[0]
company_id = first_company.get("id")
company_name = first_company.get("name", "Без названия")

print(f"🍏 Найдена компания: '{company_name}'")
print(f"🆔 Её точный UUID: {company_id}\n")

# ШАГ 2: Генерируем сам API-ключ, используя полученный UUID
print("2. Создаем API-ключ...")
keys_url = "https://ru.yougile.com/api-v2/auth/keys"
key_payload = {
    "login": LOGIN,
    "password": PASSWORD,
    "companyId": company_id
}

key_response = requests.post(keys_url, json=key_payload, headers=headers)

if key_response.status_code in [200, 201]:
    data = key_response.json()
    api_key = data.get("key") or data.get("token")
    print("\n🎉 УСПЕХ! Ключ успешно получен.")
    print("="*60)
    print("Скопируй этот API-ключ в твой .env:")
    print(api_key)
    print("="*60)
    print(f"И не забудь обновить там же YOUGILE_COMPANY_ID={company_id}")
else:
    print(f"🔴 Ошибка создания ключа ({key_response.status_code}): {key_response.text}")