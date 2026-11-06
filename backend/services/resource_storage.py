from datetime import datetime, timedelta, timezone

from db.database import get_db


RESOURCE_LABELS = {
    "document": "文稿作品",
    "interactive": "互动教学",
    "lecture": "课程讲解",
    "quiz": "练习题",
    "mindmap": "思维导图",
    "ppt": "PPT课件",
    "lab": "实操案例",
    "learning_path": "学习路径",
    "assessment": "评估报告",
}


def _text_bytes(*values) -> int:
    return sum(len(str(value or "").encode("utf-8")) for value in values)


def get_resource_storage_stats() -> dict:
    rows = get_db().execute(
        "SELECT id, type, title, content, metadata_json, created_at "
        "FROM resources ORDER BY created_at ASC"
    ).fetchall()
    by_type: dict[str, dict] = {}
    versions = get_db().execute(
        "SELECT resource_id, title, content, metadata_json FROM resource_versions"
    ).fetchall()
    version_bytes = {}
    for version in versions:
        version_bytes[version["resource_id"]] = version_bytes.get(version["resource_id"], 0) + _text_bytes(
            version["title"], version["content"], version["metadata_json"],
        )
    db_bytes = 0
    for row in rows:
        resource_type = row["type"] or "other"
        stored_bytes = (_text_bytes(row["title"], row["content"], row["metadata_json"])
                        + version_bytes.get(row["id"], 0))
        db_bytes += stored_bytes
        item = by_type.setdefault(resource_type, {
            "type": resource_type,
            "label": RESOURCE_LABELS.get(resource_type, resource_type),
            "count": 0,
            "db_bytes": 0,
            "total_bytes": 0,
        })
        item["count"] += 1
        item["db_bytes"] += stored_bytes
        item["total_bytes"] += stored_bytes

    return {
        "count": len(rows),
        "db_bytes": db_bytes,
        "total_bytes": db_bytes,
        "version_count": len(versions),
        "version_bytes": sum(version_bytes.values()),
        "oldest_created_at": rows[0]["created_at"] if rows else None,
        "newest_created_at": rows[-1]["created_at"] if rows else None,
        "by_type": sorted(
            by_type.values(),
            key=lambda item: (-item["total_bytes"], item["label"]),
        ),
    }


def cleanup_resources_older_than(days: int, resource_types: list[str] | None = None) -> dict:
    safe_days = max(1, min(int(days), 3650))
    selected_types = sorted({
        str(item).strip()
        for item in (resource_types or [])
        if str(item).strip()
    })
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=safe_days)
    params: list = [cutoff.strftime("%Y-%m-%d %H:%M:%S")]
    where = "created_at < ?"
    if selected_types:
        placeholders = ", ".join("?" for _ in selected_types)
        where += f" AND type IN ({placeholders})"
        params.extend(selected_types)

    db = get_db()
    rows = db.execute(
        f"SELECT id, type, title, content, metadata_json FROM resources WHERE {where}",
        params,
    ).fetchall()
    resource_ids = [int(row["id"]) for row in rows]
    if not resource_ids:
        return {
            "deleted_count": 0,
            "freed_bytes": 0,
            "cutoff": cutoff.isoformat(),
            "resource_types": selected_types,
            "storage": get_resource_storage_stats(),
        }

    freed_bytes = sum(
        _text_bytes(row["title"], row["content"], row["metadata_json"])
        for row in rows
    )
    placeholders = ", ".join("?" for _ in resource_ids)
    versions = db.execute(
        f"SELECT title, content, metadata_json FROM resource_versions WHERE resource_id IN ({placeholders})",
        resource_ids,
    ).fetchall()
    freed_bytes += sum(_text_bytes(row["title"], row["content"], row["metadata_json"]) for row in versions)
    quiz_rows = db.execute(
        f"SELECT id FROM quizzes WHERE resource_id IN ({placeholders})",
        resource_ids,
    ).fetchall()
    quiz_ids = [int(row["id"]) for row in quiz_rows]
    if quiz_ids:
        quiz_placeholders = ", ".join("?" for _ in quiz_ids)
        db.execute(
            f"DELETE FROM quiz_records WHERE quiz_id IN ({quiz_placeholders})",
            quiz_ids,
        )
    db.execute(f"DELETE FROM quizzes WHERE resource_id IN ({placeholders})", resource_ids)
    db.execute(
        f"UPDATE learning_events SET resource_id = NULL WHERE resource_id IN ({placeholders})",
        resource_ids,
    )
    db.execute(f"DELETE FROM resources WHERE id IN ({placeholders})", resource_ids)
    db.commit()
    return {
        "deleted_count": len(resource_ids),
        "freed_bytes": freed_bytes,
        "cutoff": cutoff.isoformat(),
        "resource_types": selected_types,
        "storage": get_resource_storage_stats(),
    }
