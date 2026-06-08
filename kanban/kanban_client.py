"""
YouGile API клиент.

Возможности:
- Создание задач с дедлайном и исполнителем
- Перемещение задач между колонками (move_task)
- Получение задач из колонки (get_column_tasks)
- Динамический маппинг участников из колонки «Участники проекта»
- Автоматическое создание/обновление карточек участников
"""

import os
import re
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

YOUGILE_BASE = "https://ru.yougile.com/api-v2"


class YouGileClient:
    def __init__(self):
        self.api_key = os.getenv("YOUGILE_API_KEY")
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Внутренние утилиты
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict = None) -> dict | list | None:
        try:
            r = requests.get(f"{YOUGILE_BASE}{path}", headers=self._headers,
                             params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            print(f"[YouGile GET {path}] HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[YouGile GET {path}] Исключение: {e}")
        return None

    def _post(self, path: str, payload: dict) -> dict | None:
        try:
            r = requests.post(f"{YOUGILE_BASE}{path}", headers=self._headers,
                              json=payload, timeout=15)
            if r.status_code in (200, 201):
                return r.json()
            print(f"[YouGile POST {path}] HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[YouGile POST {path}] Исключение: {e}")
        return None

    def _patch(self, path: str, payload: dict) -> dict | None:
        try:
            r = requests.patch(f"{YOUGILE_BASE}{path}", headers=self._headers,
                               json=payload, timeout=15)
            if r.status_code in (200, 201):
                return r.json()
            print(f"[YouGile PATCH {path}] HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[YouGile PATCH {path}] Исключение: {e}")
        return None

    # ------------------------------------------------------------------
    # Работа с задачами
    # ------------------------------------------------------------------

    def get_column_tasks(self, column_id: str) -> list[dict]:
        """Возвращает все задачи из колонки (пагинация до 500 штук)."""
        if not column_id:
            return []
        result = self._get("/tasks", params={"columnId": column_id, "limit": 500})
        if result is None:
            return []
        # API может вернуть как список, так и {"content": [...]}
        if isinstance(result, list):
            return result
        return result.get("content", [])

    def move_task(self, task_id: str, target_column_id: str) -> bool:
        """Перемещает задачу в другую колонку через PATCH."""
        if not task_id or not target_column_id:
            return False
        result = self._patch(f"/tasks/{task_id}", {"columnId": target_column_id})
        return result is not None

    def create_task(
        self,
        column_id: str,
        title: str,
        description: str,
        deadline_str: str | None = None,
        assignee_name: str | None = None,
        participants_column_id: str | None = None,
    ) -> dict | None:
        """
        Создаёт задачу в указанной колонке.

        deadline_str        : "YYYY-MM-DD HH:MM" (МСК, уже рассчитанный)
        assignee_name       : имя/username исполнителя — ищется в колонке участников
        participants_column_id : если передан, ищет @username в карточках участников
        """
        payload: dict = {
            "columnId": column_id,
            "title": title,
            "description": description,
        }

        # --- Дедлайн ---
        if deadline_str and deadline_str not in ("null", "None", ""):
            try:
                dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
                # Считаем, что время уже МСК; переводим в UTC timestamp
                tz_msk = timezone(timedelta(hours=3))
                dt = dt.replace(tzinfo=tz_msk)
                payload["deadline"] = {
                    "deadline": int(dt.timestamp() * 1000),
                    "startDate": None,
                    "withTime": True,
                }
                print(f"[YouGile] Дедлайн: {deadline_str} МСК")
            except ValueError:
                try:
                    dt = datetime.strptime(deadline_str, "%Y-%m-%d")
                    tz_msk = timezone(timedelta(hours=3))
                    dt = dt.replace(tzinfo=tz_msk)
                    payload["deadline"] = {
                        "deadline": int(dt.timestamp() * 1000),
                        "startDate": None,
                        "withTime": False,
                    }
                except ValueError as e:
                    print(f"[YouGile] Не удалось распарсить дедлайн '{deadline_str}': {e}")

        # --- Исполнитель ---
        if assignee_name and assignee_name not in ("null", "None", ""):
            user_id = self._find_yougile_user_id(assignee_name, participants_column_id)
            if user_id:
                payload["assigned"] = [user_id]
                print(f"[YouGile] Назначен исполнитель: {assignee_name} → {user_id}")
            else:
                print(f"[YouGile] Исполнитель '{assignee_name}' не найден в YouGile.")

        result = self._post("/tasks", payload)
        if result:
            print(f"[YouGile] Задача создана: «{title}»")
        return result

    # ------------------------------------------------------------------
    # Управление участниками
    # ------------------------------------------------------------------

    def get_participants_map(self, participants_column_id: str) -> dict[str, dict]:
        """
        Читает карточки из колонки «Участники проекта».
        Возвращает словарь: lower(ФИО или @username) → {"task_id", "tg_username", "name"}
        """
        result: dict[str, dict] = {}
        if not participants_column_id:
            return result

        tasks = self.get_column_tasks(participants_column_id)
        for task in tasks:
            title: str = task.get("title", "")
            task_id: str = task.get("id", "")
            tg_match = re.search(r"@(\w+)", title)
            tg_username = f"@{tg_match.group(1)}" if tg_match else None
            # ФИО — всё до @username (если есть)
            name_part = re.sub(r"@\w+", "", title).strip(" ()-–")

            entry = {"task_id": task_id, "tg_username": tg_username, "name": name_part}

            if name_part:
                result[name_part.lower()] = entry
            if tg_username:
                result[tg_username.lower()] = entry

        return result

    # Мусорные имена, которые LLM может вернуть вместо реального ФИО
    _JUNK_NAMES = {
        "", "null", "none", "имя фамилия", "имя", "фамилия",
        "name", "fullname", "участник", "пользователь", "user",
    }

    def ensure_participant_card(
        self,
        participants_column_id: str,
        full_name: str,
        tg_username: str | None,
    ) -> str | None:
        """
        Убеждается, что в колонке «Участники проекта» есть карточка для этого человека.
        Если карточки нет — создаёт. Если есть — не трогает.
        Формат карточки: «Имя Фамилия (@username_tg)»
        Возвращает task_id карточки участника.
        """
        if not participants_column_id:
            return None

        # Нормализуем входные данные
        full_name = (full_name or "").strip()
        if tg_username and not tg_username.startswith("@"):
            tg_username = f"@{tg_username}"

        # Отбрасываем мусорные имена от LLM
        if full_name.lower() in self._JUNK_NAMES:
            # Если есть хотя бы @username — используем его как имя карточки
            if tg_username:
                full_name = tg_username
            else:
                print(f"[Участники] Пропускаю мусорную запись: '{full_name}'")
                return None

        if not full_name:
            return None

        existing = self.get_participants_map(participants_column_id)
        lookup_keys = [full_name.lower()]
        if tg_username:
            lookup_keys.append(tg_username.lower())

        for key in lookup_keys:
            if key in existing:
                print(f"[Участники] Карточка уже есть: {existing[key]['name']}")
                return existing[key]["task_id"]

        # Формируем название: «Иван Иванов (@username_tg)»
        if tg_username:
            card_title = f"{full_name} ({tg_username})"
        else:
            card_title = full_name

        result = self._post("/tasks", {
            "columnId": participants_column_id,
            "title": card_title,
            "description": "Карточка участника проекта. Создана AI PM автоматически.",
        })
        if result:
            print(f"[Участники] Создана карточка: «{card_title}»")
            return result.get("id")
        return None

    def get_tg_username_for_assignee(
        self,
        assignee_name: str,
        participants_column_id: str,
    ) -> str | None:
        """
        Ищет @tg_username для исполнителя по имени/никнейму в колонке участников.
        """
        if not assignee_name or not participants_column_id:
            return None
        pmap = self.get_participants_map(participants_column_id)
        key = assignee_name.lower().lstrip("@")
        entry = pmap.get(key) or pmap.get(f"@{key}")
        if entry:
            return entry.get("tg_username")
        # Нечёткий поиск — по любому ключу, содержащему подстроку
        for k, v in pmap.items():
            if key in k or k in key:
                return v.get("tg_username")
        return None

    # ------------------------------------------------------------------
    # Внутренний поиск YouGile-пользователя по имени
    # ------------------------------------------------------------------

    def _find_yougile_user_id(
        self,
        assignee_name: str,
        participants_column_id: str | None,
    ) -> str | None:
        """
        Пытается найти YouGile User ID:
        1. Запрашивает /users и ищет по имени.
        2. Если не нашёл — возвращает None (задача создастся без назначения).
        """
        users_data = self._get("/users")
        if not users_data:
            return None

        users = users_data if isinstance(users_data, list) else users_data.get("content", [])
        name_lower = assignee_name.lower().lstrip("@")

        for user in users:
            full_name = (user.get("name") or "").lower()
            email = (user.get("email") or "").lower()
            username = (user.get("username") or "").lower()
            if (
                name_lower in full_name
                or full_name in name_lower
                or name_lower in email
                or name_lower == username
            ):
                return user.get("id")

        return None

    # ------------------------------------------------------------------
    # Фоновая миграция задач (вызывается из bot.py периодически)
    # ------------------------------------------------------------------

    def migrate_tasks_by_deadline(self, columns_cache: dict) -> int:
        """
        Проверяет задачи во всех рабочих колонках и перемещает их
        в соответствии с актуальным дедлайном.

        Правила:
          - no_deadline    → если у задачи появился дедлайн → has_deadline или urgent_deadline
          - has_deadline   → если дедлайн < 2 дней          → urgent_deadline
          - has_deadline   → если дедлайн прошёл            → done
          - urgent_deadline→ если дедлайн прошёл            → done

        Возвращает количество перемещённых задач.
        """
        tz_msk = timezone(timedelta(hours=3))
        now_ms = int(datetime.now(tz_msk).timestamp() * 1000)
        two_days_ms = int(timedelta(days=2).total_seconds() * 1000)
        moved = 0

        check_columns = [
            ("no_deadline",      columns_cache.get("no_deadline")),
            ("has_deadline",     columns_cache.get("has_deadline")),
            ("urgent_deadline",  columns_cache.get("urgent_deadline")),
        ]

        for col_key, col_id in check_columns:
            if not col_id:
                continue

            tasks = self.get_column_tasks(col_id)
            for task in tasks:
                task_id = task.get("id")
                if not task_id:
                    continue

                raw_deadline = (task.get("deadline") or {}).get("deadline")

                # ── Задача без дедлайна в колонке no_deadline: ничего не делаем
                if col_key == "no_deadline" and raw_deadline is None:
                    continue

                # ── Задача без дедлайна, но в рабочей колонке — пропускаем
                if raw_deadline is None:
                    continue

                target_col: str | None = None

                if raw_deadline < now_ms:
                    # Дедлайн истёк → «Выполнено» (или оставить на усмотрение — можно изменить)
                    target_col = columns_cache.get("done")
                elif (raw_deadline - now_ms) <= two_days_ms:
                    # Горит
                    if col_key != "urgent_deadline":
                        target_col = columns_cache.get("urgent_deadline")
                else:
                    # Обычный дедлайн
                    if col_key != "has_deadline":
                        target_col = columns_cache.get("has_deadline")

                if target_col and target_col != col_id:
                    if self.move_task(task_id, target_col):
                        print(f"[Миграция] Задача «{task.get('title', task_id)[:40]}» → {target_col}")
                        moved += 1

        return moved