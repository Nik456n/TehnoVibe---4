import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]

# URL Mini App для кнопки «Открыть отчёт». Должен быть https:// (Telegram блокирует http).
MINI_APP_URL: str = os.environ.get("MINI_APP_URL", "http://localhost:8000/report")

# Адрес бэкенда для запросов к API (например /providers).
BACKEND_URL: str = os.environ.get("BACKEND_URL", "http://localhost:8000")
