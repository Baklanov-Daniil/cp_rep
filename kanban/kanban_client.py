import requests
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

class YouGileClient:
    def __init__(self):
        self.api_key = os.getenv("YOUGILE_API_KEY")
        self.url = "https://ru.yougile.com/api-v2/tasks"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Твой маппинг: Имя из ТГ -> ID пользователя в YouGile
        # ID своего аккаунта (Артём Мартын) ты видел на скриншоте!
        self.user_mapping = {
            "артем": "7f0cd028-eeb0-408d-9e34-530020660a2a",
            "artem": "7f0cd028-eeb0-408d-9e34-530020660a2a",
            "artyom": "7f0cd028-eeb0-408d-9e34-530020660a2a",
            "dexffe": "7f0cd028-eeb0-408d-9e34-530020660a2a",
            # Сюда через запятую добавь ID Даниила или Димы, когда узнаешь их ID
            # "дима": "uuid-димы-из-yougile", 
        }

    def create_task(self, column_id: str, title: str, description: str, deadline_str: str = None, assignee_name: str = None):
        """
        Создает задачу в YouGile с отображением дедлайна по МСК и исполнителя
        """
        payload = {
            "columnId": column_id,
            "title": title,
            "description": description
        }

        # Защита от ИИ: если пришла строка 'null', 'None' или пустая переменная
        if deadline_str in [None, "", "null", "None"]:
            deadline_str = None

        if assignee_name in [None, "", "null", "None"]:
            assignee_name = None

        # 1. Обрабатываем дедлайн с учетом часов и минут
        if deadline_str:
            try:
                # Парсим дату и время, полученные от ИИ (они уже будут рассчитаны по МСК)
                dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
                timestamp_ms = int(dt.timestamp() * 1000)
                
                payload["deadline"] = {
                    "deadline": timestamp_ms,
                    "startDate": None,
                    "withTime": True
                }
                print(f"[YouGile] Успешно установлен дедлайн по МСК: {deadline_str}")
            except Exception as e:
                try:
                    # Резервный вариант, если времени нет, только дата
                    dt = datetime.strptime(deadline_str, "%Y-%m-%d")
                    timestamp_ms = int(dt.timestamp() * 1000)
                    payload["deadline"] = {
                        "deadline": timestamp_ms,
                        "startDate": None,
                        "withTime": False
                    }
                except Exception:
                    print(f"[YouGile] Ошибка парсинга даты '{deadline_str}': {e}")

        # 2. Обрабатываем исполнителя
        if assignee_name:
            name_key = assignee_name.lower().replace("@", "").strip()
            user_id = self.user_mapping.get(name_key)
            if user_id:
                payload["assigned"] = [user_id]
            else:
                print(f"[YouGile] Предупреждение: Пользователь '{assignee_name}' не найден в user_mapping")

        # Отправляем запрос
        response = requests.post(self.url, headers=self.headers, json=payload)
        if response.status_code not in [200, 201]:
            raise Exception(f"Ошибка YouGile API ({response.status_code}): {response.text}")
        
        return response.json()