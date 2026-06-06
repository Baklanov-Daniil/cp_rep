import os
import requests
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

class YouGileClient:
    def __init__(self):
        self.api_key = os.getenv("YOUGILE_API_KEY")
        self.base_url = "https://ru.yougile.com/api-v2"
        
        if not self.api_key:
            raise ValueError("Критическая ошибка: YOUGILE_API_KEY не найден в файле .env")
        
        # Настраиваем заголовки для авторизации в YouGile
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def get_projects(self):
        """Получить список всех проектов"""
        url = f"{self.base_url}/projects"
        response = requests.get(url, headers=self.headers)
        return response.json()

    def get_boards(self, project_id=None):
        """Получить список досок (можно отфильтровать по project_id)"""
        url = f"{self.base_url}/boards"
        params = {"projectId": project_id} if project_id else {}
        response = requests.get(url, headers=self.headers, params=params)
        return response.json()

    def create_task(self, column_id, title, description=""):
        """Создать задачу в определенной колонке доски"""
        url = f"{self.base_url}/tasks"
        payload = {
            "columnId": column_id,
            "title": title,
            "description": description
        }
        response = requests.post(url, headers=self.headers, json=payload)
        return response.json()