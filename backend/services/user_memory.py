from db.database import get_db
from services.usage_tracker import ensure_user


MAX_MEMORY_CHARS = 4000


def get_user_memory(user_id: str | None) -> dict:
    resolved = ensure_user(user_id)
    row = get_db().execute(
        "SELECT enabled, content, updated_at FROM user_memories WHERE user_id = ?",
        (resolved,),
    ).fetchone()
    if not row:
        return {
            "user_id": resolved,
            "enabled": False,
            "content": "",
            "updated_at": None,
            "max_chars": MAX_MEMORY_CHARS,
        }
    return {
        "user_id": resolved,
        "enabled": bool(row["enabled"]),
        "content": row["content"] or "",
        "updated_at": row["updated_at"],
        "max_chars": MAX_MEMORY_CHARS,
    }


def update_user_memory(user_id: str | None, enabled: bool, content: str) -> dict:
    resolved = ensure_user(user_id)
    normalized = str(content or "").strip()[:MAX_MEMORY_CHARS]
    db = get_db()
    db.execute(
        "INSERT INTO user_memories (user_id, enabled, content) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "enabled=excluded.enabled, content=excluded.content, updated_at=CURRENT_TIMESTAMP",
        (resolved, 1 if enabled else 0, normalized),
    )
    db.commit()
    return get_user_memory(resolved)


def get_active_memory_text(user_id: str | None) -> str:
    memory = get_user_memory(user_id)
    return memory["content"].strip() if memory["enabled"] else ""
