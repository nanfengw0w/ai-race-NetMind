import json
from pathlib import Path

from config import BASE_DIR, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


SETTINGS_PATH = Path(BASE_DIR) / "data" / "app_settings.json"
KNOWLEDGE_BASES = {
    "computer_network": "计算机网络预设",
    "custom": "上传资料建库",
    "blank": "空白知识库",
}

DEFAULT_SETTINGS = {
    "ai": {
        "base_url": LLM_BASE_URL,
        "api_key": LLM_API_KEY,
        "model": LLM_MODEL,
    },
    "knowledge_base": "blank",
}

_settings_cache = None


def _load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    settings["ai"].update(saved.get("ai") or {})
    if saved.get("knowledge_base") in KNOWLEDGE_BASES:
        settings["knowledge_base"] = saved["knowledge_base"]
    return settings


def get_app_settings() -> dict:
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = _load_settings()
    return _settings_cache


def get_public_settings() -> dict:
    settings = get_app_settings()
    effective_ai = get_ai_settings()
    knowledge_base = settings.get("knowledge_base", "blank")
    try:
        from services.custom_kb import get_custom_knowledge_stats
        custom_knowledge_base = get_custom_knowledge_stats()
    except Exception:
        custom_knowledge_base = {"files": 0, "chunks": 0, "file_names": []}
    return {
        "ai": {
            "base_url": effective_ai.get("base_url", ""),
            "model": effective_ai.get("model", ""),
            "api_key_configured": bool(effective_ai.get("api_key")),
        },
        "knowledge_base": knowledge_base,
        "knowledge_base_label": KNOWLEDGE_BASES.get(knowledge_base, knowledge_base),
        "knowledge_base_options": [
            {"key": key, "label": label}
            for key, label in KNOWLEDGE_BASES.items()
        ],
        "custom_knowledge_base": custom_knowledge_base,
    }


def update_app_settings(payload: dict) -> dict:
    settings = get_app_settings()
    ai = payload.get("ai") or {}
    if "base_url" in ai:
        settings["ai"]["base_url"] = str(ai.get("base_url") or "").strip()
    if "model" in ai:
        settings["ai"]["model"] = str(ai.get("model") or "").strip()
    if "api_key" in ai and ai.get("api_key") is not None:
        settings["ai"]["api_key"] = str(ai.get("api_key") or "").strip()

    knowledge_base = payload.get("knowledge_base")
    if knowledge_base in KNOWLEDGE_BASES:
        settings["knowledge_base"] = knowledge_base

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return get_public_settings()


def get_ai_settings() -> dict:
    ai = get_app_settings()["ai"]
    return {
        "base_url": ai.get("base_url") or LLM_BASE_URL or "https://api.openai.com/v1",
        "api_key": ai.get("api_key") or LLM_API_KEY,
        "model": ai.get("model") or LLM_MODEL or "gpt-4o",
    }


def is_knowledge_base_enabled() -> bool:
    return get_app_settings().get("knowledge_base") != "blank"


def get_knowledge_base_mode() -> str:
    return get_app_settings().get("knowledge_base", "blank")
