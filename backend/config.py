import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")

CHROMA_PERSIST_DIR = str(BASE_DIR / "data" / "chroma_db")
DB_PATH = os.getenv("NETMIND_DB_PATH", str(BASE_DIR / "data" / "netmind.db"))

CHROMA_COLLECTION = "netmind_knowledge"
