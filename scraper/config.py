import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", os.getenv("POSTGRES_PORT", "5432"))),
    "dbname": os.getenv("DB_NAME", os.getenv("POSTGRES_DB", "social_analytics")),
    "user": os.getenv("DB_USER", os.getenv("POSTGRES_USER", "postgres")),
    "password": os.getenv("DB_PASSWORD", os.getenv("POSTGRES_PASSWORD", "postgres")),
}