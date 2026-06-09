import os

from kanban.kanban_client import YouGileClient


def update_env_file(key: str, value: str) -> None:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as file:
            lines = file.readlines()

    updated = False
    result = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            result.append(f"{key}={value}\n")
            updated = True
        else:
            result.append(line)

    if not updated:
        result.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as file:
        file.writelines(result)
    os.environ[key] = value


def create_ai_pm_board(api_key: str, project_id: str) -> dict:
    result = YouGileClient(api_key).create_ai_pm_board_and_columns(project_id)
    result["used_key"] = api_key if result.get("success") else None
    return result
