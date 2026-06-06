import os
import requests
from config import YOUGILE_API_KEY, YOUGILE_BOARD_ID, YOUGILE_COLUMN_ID

class YouGileClient:
    """Клиент для управления карточками в YouGile через API."""

    def __init__(self):
        self.base_url = "https://api.yougile.com/v1"
        self.headers = {
            "Authorization": f"Bearer {YOUGILE_API_KEY}",
            "Content-Type": "application/json"
        }

    def create_task_card(self, title: str, description: str, deadline: str = None) -> dict:
        """Создает карточку задачи в YouGile и возвращает полную информацию."""
        url = f"{self.base_url}/tasks"

        payload = {
            "title": title,
            "description": description,
            "boardId": YOUGILE_BOARD_ID,
            "columnId": YOUGILE_COLUMN_ID
        }

        if deadline:
            payload["deadline"] = {"date": deadline}

        response = requests.post(url, json=payload, headers=self.headers)

        if response.status_code == 201:
            task_data = response.json()
            return {
                "id": task_data.get("id"),
                "title": title,
                "url": f"https://yougile.com/task/{task_data.get('id')}"
            }
        else:
            raise Exception(f"Ошибка создания задачи в YouGile: {response.text}")