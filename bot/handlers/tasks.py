from kanban.kanban_client import YouGileClient


def create_yougile_board_and_columns(api_key: str, project_id: str) -> dict:
    """Shared board bootstrap helper for router-based auth handlers."""
    result = YouGileClient(api_key).create_ai_pm_board_and_columns(project_id)
    result["used_key"] = api_key if result.get("success") else None
    return result
