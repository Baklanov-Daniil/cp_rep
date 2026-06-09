import asyncio
from datetime import datetime

import requests


AUTH_BASE = "https://ru.yougile.com/api-v2/auth"
API_BASE = "https://ru.yougile.com/api-v2"
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


def normalize_collection(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for field in ("content", "data", "result"):
            value = payload.get(field)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def extract_yougile_api_key(payload) -> str | None:
    if isinstance(payload, list):
        for item in payload:
            key = extract_yougile_api_key(item)
            if key:
                return key
        return None

    if not isinstance(payload, dict) or payload.get("deleted"):
        return None

    for field in ("key", "token", "apiKey", "api_key"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for field in ("content", "data", "result"):
        key = extract_yougile_api_key(payload.get(field))
        if key:
            return key

    return None


async def post_auth(path: str, payload: dict) -> requests.Response:
    return await asyncio.to_thread(
        requests.post,
        f"{AUTH_BASE}{path}",
        json=payload,
        headers=JSON_HEADERS,
        timeout=15,
    )


async def get_companies(login: str, password: str) -> tuple[list[dict], int]:
    response = await post_auth("/companies", {"login": login, "password": password})
    if response.status_code == 401:
        response = await post_auth("/companies", {"email": login, "password": password})
    if response.status_code not in (200, 201):
        return [], response.status_code
    return normalize_collection(response.json()), response.status_code


async def get_or_create_api_key(login: str, password: str, company_id: str) -> tuple[str | None, int]:
    login_payload = {"login": login, "password": password, "companyId": company_id}
    email_payload = {"email": login, "password": password, "companyId": company_id}

    response = await post_auth("/keys/get", login_payload)
    if response.status_code not in (200, 201):
        response = await post_auth("/keys/get", email_payload)
    if response.status_code not in (200, 201):
        return None, response.status_code

    api_key = extract_yougile_api_key(response.json())
    if api_key:
        return api_key, response.status_code

    title = f"AI PM Bot Key ({datetime.now().strftime('%d.%m.%Y %H:%M')})"
    response = await post_auth("/keys", {**login_payload, "title": title})
    if response.status_code not in (200, 201):
        response = await post_auth("/keys", {**email_payload, "title": title})
    if response.status_code not in (200, 201):
        return None, response.status_code

    return extract_yougile_api_key(response.json()), response.status_code


async def get_projects(api_key: str) -> tuple[list[dict], int]:
    response = await asyncio.to_thread(
        requests.get,
        f"{API_BASE}/projects",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=15,
    )
    if response.status_code != 200:
        return [], response.status_code
    return normalize_collection(response.json()), response.status_code
