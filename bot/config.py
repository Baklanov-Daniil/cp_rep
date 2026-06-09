import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    telegram_token: str
    web_app_url: str
    realtime_delay: int = 15
    max_conversation_time: int = 900
    migration_interval: int = 300


def load_config() -> AppConfig:
    load_dotenv()
    return AppConfig(
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        web_app_url=os.getenv("WEB_APP_URL", "https://jolly-flan-a04ec.netlify.app/"),
    )
