import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


YOUGILE_BASE = "https://ru.yougile.com/api-v2"
MSK = timezone(timedelta(hours=3))

BOARD_TITLE = "Канбан AI PM"
COLUMN_SPECS = [
    ("participants", "👥 Участники проекта"),
    ("urgent_deadline", "🔥 Дедлайн < 2 дней"),
    ("has_deadline", "📅 Дедлайн есть"),
    ("no_deadline", "📥 Без дедлайна"),
    ("done", "✅ Выполнено"),
]


class YouGileClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("YOUGILE_API_KEY")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = requests.request(
                method,
                f"{YOUGILE_BASE}{path}",
                headers=self.headers,
                timeout=15,
                **kwargs,
            )
            if response.status_code in (200, 201):
                return response.json()
            print(f"[YouGile {method}] HTTP {response.status_code}: {response.text[:160]}")
        except Exception as exc:
            print(f"[YouGile {method} Error] {exc}")
        return None

    def _get(self, path: str, params: dict | None = None):
        return self._request("GET", path, params=params)

    def _post(self, path: str, payload: dict):
        return self._request("POST", path, json=payload)

    def _patch(self, path: str, payload: dict):
        return self._request("PATCH", path, json=payload)

    @staticmethod
    def _as_items(payload) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            content = payload.get("content")
            if isinstance(content, list):
                return [item for item in content if isinstance(item, dict)]
        return []

    def create_ai_pm_board_and_columns(self, project_id: str) -> dict:
        board_id = self._create_board(project_id)
        if not board_id:
            return {"success": False, "board_id": None, "columns": {}}

        columns = self._create_columns(board_id)
        return {
            "success": len(columns) == len(COLUMN_SPECS),
            "board_id": board_id,
            "columns": columns,
        }

    def _create_board(self, project_id: str) -> str | None:
        board = self._post("/boards", {"title": BOARD_TITLE, "projectId": project_id})
        return board.get("id") if isinstance(board, dict) else None

    def _create_columns(self, board_id: str) -> dict[str, str]:
        columns = {}
        for key, title in COLUMN_SPECS:
            column = self._post("/columns", {"title": title, "boardId": board_id})
            column_id = column.get("id") if isinstance(column, dict) else None
            if not column_id:
                break
            columns[key] = column_id
        return columns

    def get_column_tasks(self, column_id: str | None) -> list[dict]:
        if not column_id:
            return []
        return self._as_items(self._get("/tasks", params={"columnId": column_id, "limit": 500}))

    def move_task(self, task_id: str | None, target_column_id: str | None) -> bool:
        if not task_id or not target_column_id:
            return False
        return self._patch(f"/tasks/{task_id}", {"columnId": target_column_id}) is not None

    def create_task(
        self,
        column_id: str,
        title: str,
        description: str,
        deadline_str: str | None = None,
        assignee_name: str | None = None,
        parts_col: str | None = None,
        participants_column_id: str | None = None,
    ) -> dict | None:
        payload = self._task_payload(column_id, title, description, deadline_str)
        user_id = self._find_yougile_user_id(assignee_name) if assignee_name else None
        if user_id:
            payload["assigned"] = [user_id]
        return self._post("/tasks", payload)

    def _task_payload(
        self,
        column_id: str,
        title: str,
        description: str,
        deadline_str: str | None,
    ) -> dict:
        payload = {"columnId": column_id, "title": title, "description": description}
        deadline = self._parse_deadline_string(deadline_str)
        if deadline:
            payload["deadline"] = {
                "deadline": int(deadline.timestamp() * 1000),
                "startDate": None,
                "withTime": True,
            }
        return payload

    @staticmethod
    def _parse_deadline_string(deadline_str: str | None) -> datetime | None:
        if not deadline_str:
            return None
        try:
            return datetime.strptime(deadline_str, "%Y-%m-%d %H:%M").replace(tzinfo=MSK)
        except ValueError:
            return None

    def migrate_tasks_by_deadline(self, columns: dict[str, str | None]) -> int:
        if not all(columns.get(key) for key in ("no_deadline", "has_deadline", "urgent_deadline", "done")):
            return 0

        moved = 0
        for source_key in ("no_deadline", "has_deadline", "urgent_deadline"):
            for task in self.get_column_tasks(columns[source_key]):
                target_column = self._target_column_for_existing_task(task, columns)
                if target_column and target_column != task.get("columnId"):
                    moved += int(self.move_task(task.get("id"), target_column))
        return moved

    def _target_column_for_existing_task(self, task: dict, columns: dict[str, str | None]) -> str | None:
        deadline = self._extract_deadline(task)
        if not deadline:
            return columns["no_deadline"]
        if deadline - datetime.now(MSK) <= timedelta(days=2):
            return columns["urgent_deadline"]
        return columns["has_deadline"]

    @staticmethod
    def _extract_deadline(task: dict) -> datetime | None:
        raw = task.get("deadline")
        if isinstance(raw, dict):
            raw = raw.get("deadline") or raw.get("date")
        if raw is None:
            return None
        try:
            timestamp = int(raw)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            return datetime.fromtimestamp(timestamp, tz=MSK)
        except (TypeError, ValueError):
            return None

    def get_participants_map(self, participants_column_id: str | None) -> dict[str, dict]:
        result = {}
        for task in self.get_column_tasks(participants_column_id):
            entry = self._participant_entry(task)
            if entry["name"]:
                result[entry["name"].lower()] = entry
            if entry["tg_username"]:
                result[entry["tg_username"].lower()] = entry
        return result

    @staticmethod
    def _participant_entry(task: dict) -> dict:
        title = task.get("title", "")
        match = re.search(r"@(\w+)", title)
        tg_username = f"@{match.group(1)}" if match else None
        name = re.sub(r"@\w+", "", title).strip(" ()-–")
        return {"task_id": task.get("id"), "tg_username": tg_username, "name": name}

    def ensure_participant_card(
        self,
        participants_column_id: str | None,
        full_name: str | None,
        tg_username: str | None,
        existing_map: dict | None = None,
    ) -> str | None:
        title = self._participant_title(full_name, tg_username)
        if not participants_column_id or not title:
            return None

        existing = existing_map if existing_map is not None else self.get_participants_map(participants_column_id)
        existing_id = self._find_existing_participant(title, tg_username, existing)
        if existing_id:
            return existing_id

        created = self._post(
            "/tasks",
            {"columnId": participants_column_id, "title": title, "description": "AI Auto Card"},
        )
        return created.get("id") if isinstance(created, dict) else None

    @staticmethod
    def _participant_title(full_name: str | None, tg_username: str | None) -> str | None:
        name = (full_name or "").strip()
        if tg_username and not tg_username.startswith("@"):
            tg_username = f"@{tg_username}"
        if not name:
            name = tg_username or ""
        if not name:
            return None
        return f"{name} ({tg_username})" if tg_username and name != tg_username else name

    @staticmethod
    def _find_existing_participant(title: str, tg_username: str | None, existing: dict) -> str | None:
        candidates = [title.lower()]
        if tg_username:
            candidates.append(tg_username.lower())
        for key in candidates:
            if key in existing:
                return existing[key]["task_id"]
        return None

    def get_tg_username_for_assignee(self, name: str | None, participants_column_id: str | None) -> str | None:
        if not name or not participants_column_id:
            return None

        needle = name.lower().lstrip("@")
        participants = self.get_participants_map(participants_column_id)
        exact = participants.get(needle) or participants.get(f"@{needle}")
        if exact:
            return exact.get("tg_username")

        for key, entry in participants.items():
            if needle in key or key in needle:
                return entry.get("tg_username")
        return None

    def _find_yougile_user_id(self, name: str | None) -> str | None:
        if not name:
            return None

        needle = name.lower().lstrip("@")
        users = self._as_items(self._get("/users"))
        for user in users:
            full_name = (user.get("name") or "").lower()
            email = (user.get("email") or "").lower()
            if needle in full_name or needle in email:
                return user.get("id")
        return None
