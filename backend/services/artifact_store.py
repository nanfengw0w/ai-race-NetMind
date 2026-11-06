"""Versioned artifact storage. No generation, HTML rewriting, or provider calls."""
import hashlib
import json
import re
from contextlib import contextmanager

from db.database import get_db


class ArtifactError(Exception):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _metadata(raw):
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _encode_metadata(value):
    if not isinstance(value, dict):
        raise ArtifactError(400, "metadata 必须是对象")
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ArtifactError(400, "metadata 必须可序列化为 JSON") from exc


@contextmanager
def _transaction(db, write=True):
    # Also cooperate with a caller already saving a generated resource.
    nested = db.in_transaction
    db.execute("SAVEPOINT artifact_store" if nested else ("BEGIN IMMEDIATE" if write else "BEGIN"))
    try:
        yield
    except BaseException:
        if nested:
            db.execute("ROLLBACK TO artifact_store")
            db.execute("RELEASE artifact_store")
        else:
            db.rollback()
        raise
    else:
        if nested:
            db.execute("RELEASE artifact_store")
        else:
            db.commit()


def _authorize(db, student_id, user_id=None):
    if not student_id:
        raise ArtifactError(400, "student_id 不能为空")
    student = db.execute("SELECT owner_id FROM students WHERE id = ?", (student_id,)).fetchone()
    if student is None:
        raise ArtifactError(404, "学习会话不存在")
    if user_id is not None and (not user_id or student["owner_id"] != user_id):
        raise ArtifactError(403, "无权访问此学习会话")


def _resource(db, resource_id, student_id=None, user_id=None):
    if user_id is not None:
        _authorize(db, student_id, user_id)
    row = db.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
    if row is None or (student_id is not None and row["student_id"] != student_id):
        raise ArtifactError(404, "作品不存在于此学习会话")
    return row


def _require_version(db, row):
    current = int(row["current_version"] or 0)
    snapshot = db.execute("SELECT 1 FROM resource_versions WHERE resource_id = ? AND version = ?",
                          (row["id"], current)).fetchone()
    if snapshot is None:
        raise ArtifactError(404, "此资源没有作品版本记录，请重新生成或保存为作品。")
    return row


def _snapshot(db, row, version=None):
    version = row["current_version"] if version is None else version
    snapshot = db.execute(
        "SELECT * FROM resource_versions WHERE resource_id = ? AND version = ?",
        (row["id"], version),
    ).fetchone()
    if snapshot is None:
        raise ArtifactError(404, "作品版本不存在")
    result = dict(snapshot)
    result.pop("resource_id")
    result["id"] = row["id"]
    result["student_id"] = row["student_id"]
    result["current_version"] = row["current_version"]
    result["metadata"] = _metadata(result["metadata_json"])
    return result


def read_artifact(resource_id, version=None, student_id=None, user_id=None) -> dict:
    db = get_db()
    with _transaction(db, write=False):
        row = _require_version(db, _resource(db, resource_id, student_id, user_id))
        return _snapshot(db, row, version)


def list_versions(resource_id, student_id=None, user_id=None) -> list[dict]:
    db = get_db()
    with _transaction(db, write=False):
        row = _require_version(db, _resource(db, resource_id, student_id, user_id))
        snapshots = db.execute(
            "SELECT version,type,title,parent_version,change_request,source_conversation_id,"
            "created_at,content_hash FROM resource_versions WHERE resource_id = ? ORDER BY version DESC",
            (row["id"],),
        ).fetchall()
        return [dict(item, resource_id=row["id"], current_version=row["current_version"])
                for item in snapshots]


def _append(db, row, content, metadata, title, base_version, expected_version, change_request):
    current = int(row["current_version"])
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
        raise ArtifactError(400, "expected_version 必须是正整数")
    if expected_version != current:
        raise ArtifactError(409, {"message": "作品已更新，请重新读取后修改", "current_version": current})
    if not isinstance(content, str):
        raise ArtifactError(400, "content 必须是字符串")
    base = _snapshot(db, row, base_version)
    metadata_json = base["metadata_json"] if metadata is None else _encode_metadata(metadata)
    title = base["title"] if title is None else title
    next_version = current + 1
    db.execute(
        "INSERT INTO resource_versions (resource_id,version,type,title,content,metadata_json,"
        "parent_version,change_request,source_conversation_id,content_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (row["id"], next_version, base["type"], title, content, metadata_json,
         base["version"], change_request, base["source_conversation_id"], _hash(content)),
    )
    db.execute(
        "UPDATE resources SET type=?,title=?,content=?,metadata_json=?,current_version=? WHERE id=?",
        (base["type"], title, content, metadata_json, next_version, row["id"]),
    )
    updated = db.execute("SELECT * FROM resources WHERE id = ?", (row["id"],)).fetchone()
    return _snapshot(db, updated)


def append_revision(resource_id, content, metadata=None, title=None, base_version=None,
                    expected_version=None, change_request=None, student_id=None, user_id=None) -> dict:
    db = get_db()
    with _transaction(db):
        row = _require_version(db, _resource(db, resource_id, student_id, user_id))
        return _append(db, row, content, metadata, title, base_version, expected_version, change_request)


def restore_version(resource_id, version, expected_version, student_id=None, user_id=None) -> dict:
    db = get_db()
    with _transaction(db):
        row = _require_version(db, _resource(db, resource_id, student_id, user_id))
        source = _snapshot(db, row, version)
        return _append(db, row, source["content"], None, source["title"], version,
                       expected_version, f"恢复至版本 {version}")


def restore_slide_version(resource_id, version, expected_version, slide_number,
                         student_id=None, user_id=None) -> dict:
    """Atomically restore one historical slide into the latest complete deck."""
    from services.ppt_slides import restore_slide
    db = get_db()
    with _transaction(db):
        row = _require_version(db, _resource(db, resource_id, student_id, user_id))
        current = _snapshot(db, row)
        if type(expected_version) is not int or expected_version < 1:
            raise ArtifactError(400, "expected_version 必须是正整数")
        if expected_version != current["version"]:
            raise ArtifactError(409, {"message": "作品已更新，请重新读取后恢复", "current_version": current["version"]})
        if current["type"] != "ppt":
            raise ArtifactError(400, "只有 PPT 课件支持单页恢复。")
        historical = _snapshot(db, row, version)
        try:
            content = restore_slide(current["content"], historical["content"], slide_number)
        except ValueError as exc:
            raise ArtifactError(400, str(exc)) from exc
        if content == current["content"]:
            return {**current, "unchanged": True}
        metadata = dict(current["metadata"])
        metadata.pop("revision_scope", None)
        metadata["restore_scope"] = {"type": "slide", "slide_number": slide_number,
                                     "source_version": version, "base_version": current["version"]}
        return _append(db, row, content, metadata, current["title"], current["version"],
                       expected_version, f"仅将第 {slide_number} 页恢复至版本 {version}，其他页面保持 v{current['version']}")


def _create(db, student_id, type, title, content, metadata, source_conversation_id):
    if not isinstance(content, str) or not isinstance(type, str) or not type.strip():
        raise ArtifactError(400, "type 不能为空，content 必须是字符串")
    metadata_json = _encode_metadata({} if metadata is None else metadata)
    cursor = db.execute(
        "INSERT INTO resources (student_id,type,title,content,metadata_json,current_version) VALUES (?,?,?,?,?,0)",
        (student_id, type, title, content, metadata_json),
    )
    resource_id = cursor.lastrowid
    db.execute(
        "INSERT INTO resource_versions (resource_id,version,type,title,content,metadata_json,"
        "source_conversation_id,content_hash) VALUES (?,1,?,?,?,?,?,?)",
        (resource_id, type, title, content, metadata_json, source_conversation_id, _hash(content)),
    )
    db.execute("UPDATE resources SET current_version = 1 WHERE id = ?", (resource_id,))
    return _snapshot(db, _resource(db, resource_id, student_id))


def create_artifact(student_id, type, title, content, metadata=None,
                    source_conversation_id=None, user_id=None) -> dict:
    db = get_db()
    with _transaction(db):
        _authorize(db, student_id, user_id)
        if source_conversation_id is not None:
            source = db.execute("SELECT student_id FROM conversations WHERE id = ?",
                                (source_conversation_id,)).fetchone()
            if source is None or source["student_id"] != student_id:
                raise ArtifactError(404, "来源消息不存在于此学习会话")
        return _create(db, student_id, type, title, content, metadata, source_conversation_id)


def create_artifact_from_message(conversation_id, student_id, user_id, title=None) -> dict:
    db = get_db()
    with _transaction(db):
        _authorize(db, student_id, user_id)
        source = db.execute("SELECT * FROM conversations WHERE id = ? AND student_id = ?",
                            (conversation_id, student_id)).fetchone()
        if source is None:
            raise ArtifactError(404, "来源消息不存在于此学习会话")
        if source["role"] != "assistant":
            raise ArtifactError(400, "只能保存已持久化的 assistant 消息")
        existing = db.execute("SELECT resource_id FROM resource_source_messages WHERE source_message_id = ?",
                              (conversation_id,)).fetchone()
        if existing:
            return _snapshot(db, _require_version(db, _resource(db, existing["resource_id"], student_id, user_id)))
        first_line = next((line.strip() for line in source["content"].splitlines() if line.strip()), "")
        suggested_title = re.sub(r"^[#>*`\s]+", "", first_line)[:50] or f"对话文稿 #{conversation_id}"
        result = _create(db, student_id, "document", title or suggested_title, source["content"],
                         {"source_message": conversation_id}, conversation_id)
        db.execute("INSERT INTO resource_source_messages (source_message_id,resource_id) VALUES (?,?)",
                   (conversation_id, result["id"]))
        return result


def search_artifacts(student_id, user_id=None, query="") -> list[dict]:
    db = get_db()
    with _transaction(db, write=False):
        _authorize(db, student_id, user_id)
        needle = str(query or "").strip().casefold()
        rows = db.execute("SELECT * FROM resources WHERE student_id = ? "
                          "AND EXISTS(SELECT 1 FROM resource_versions v "
                          "WHERE v.resource_id=resources.id AND v.version=resources.current_version) "
                          "ORDER BY created_at DESC,id DESC",
                          (student_id,)).fetchall()
        result = []
        for row in rows:
            if needle and needle not in (str(row["title"] or "") + "\n" + row["content"]).casefold():
                continue
            snapshot = _snapshot(db, row)
            content = snapshot.pop("content")
            # Metadata may itself contain complete HTML/bundles: summaries expose
            # only identifying fields, never large generated payloads.
            snapshot.pop("metadata")
            snapshot.pop("metadata_json")
            snapshot["summary"] = content[:180]
            result.append(snapshot)
        return result
