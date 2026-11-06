from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

from db.database import get_db


_active_user_id = ContextVar("netmind_user_id", default=None)
_active_student_id = ContextVar("netmind_student_id", default=None)
_usage_lock = Lock()


def ensure_user(user_id: str | None) -> str:
    resolved = str(user_id or "local_user")
    with _usage_lock:
        db = get_db()
        db.execute(
            "INSERT INTO user_accounts (id) VALUES (?) ON CONFLICT(id) DO UPDATE SET updated_at=CURRENT_TIMESTAMP",
            (resolved,),
        )
        db.commit()
    return resolved


@contextmanager
def usage_scope(user_id: str | None, student_id: str | None):
    resolved_user = ensure_user(user_id)
    user_token = _active_user_id.set(resolved_user)
    student_token = _active_student_id.set(student_id)
    try:
        yield
    finally:
        _active_user_id.reset(user_token)
        _active_student_id.reset(student_token)


def record_token_usage(diagnostics: dict):
    total_tokens = int(diagnostics.get("total_tokens") or 0)
    user_id = _active_user_id.get()
    if total_tokens <= 0 or not user_id:
        return
    with _usage_lock:
        get_db().execute(
            "INSERT INTO token_usage "
            "(user_id, student_id, model, prompt_tokens, completion_tokens, total_tokens, estimated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                _active_student_id.get(),
                diagnostics.get("model"),
                int(diagnostics.get("prompt_tokens") or 0),
                int(diagnostics.get("completion_tokens") or 0),
                total_tokens,
                1 if diagnostics.get("estimated") else 0,
            ),
        )
        get_db().commit()


def estimate_tokens(text: str) -> int:
    value = str(text or "")
    if not value:
        return 0
    return max(1, round(len(value) / 2.2))
