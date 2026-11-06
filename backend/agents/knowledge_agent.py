from rag.retriever import search
from services.app_settings import get_knowledge_base_mode, is_knowledge_base_enabled
from services.custom_kb import search_custom_knowledge


def retrieve_knowledge(query: str, top_k: int = 5, resource_type: str = None) -> list[dict]:
    if not is_knowledge_base_enabled():
        return []
    if get_knowledge_base_mode() == "custom":
        return search_custom_knowledge(query, top_k=top_k)
    return search(query, top_k=top_k, resource_type=resource_type)


RESOURCE_RETRIEVAL_POLICIES = {
    "lecture": {"top_k": 10, "max_chars": 10000, "primary_type": "lecture_slide"},
    "quiz": {"top_k": 9, "max_chars": 9000, "primary_type": "lecture_slide"},
    "mindmap": {"top_k": 8, "max_chars": 7000, "primary_type": "lecture_slide"},
    "ppt": {"top_k": 10, "max_chars": 10000, "primary_type": "lecture_slide"},
    "interactive": {"top_k": 8, "max_chars": 9000, "primary_type": "lecture_slide"},
    "lab": {"top_k": 10, "max_chars": 10000, "primary_type": "lab"},
}


def _merge_unique_results(*groups: list[dict], limit: int) -> list[dict]:
    merged = []
    seen = set()
    for group in groups:
        for item in group:
            item_id = item.get("id")
            if item_id in seen:
                continue
            seen.add(item_id)
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


def _dynamic_top_k(intent: str, query: str, profile: dict | None = None) -> int:
    policy = RESOURCE_RETRIEVAL_POLICIES.get(intent)
    if not policy:
        return 0

    weak_points = (profile or {}).get("薄弱知识点", []) or []
    bonus = 0
    if len(query or "") >= 100:
        bonus += 1
    if len(weak_points) >= 3:
        bonus += 1
    return min(policy["top_k"] + bonus, 12)


def build_references(results: list[dict]) -> list[dict]:
    references = []
    seen = set()
    for result in results:
        metadata = result.get("metadata", {})
        source_file = metadata.get("source_file", "未知")
        chapter = metadata.get("chapter", "")
        resource_type = metadata.get("resource_type", "")
        key = (source_file, chapter, resource_type)
        if key in seen:
            continue
        seen.add(key)
        distance = result.get("distance")
        references.append({
            "source_file": source_file,
            "chapter": chapter,
            "resource_type": resource_type,
            "relevance": round(max(0, 1 - float(distance)) * 100) if distance is not None else None,
            "excerpt": str(result.get("text", "")).strip()[:180],
        })
    return references


def retrieve_resource_context(intent: str, query: str, profile: dict | None = None) -> dict:
    if not is_knowledge_base_enabled():
        return {
            "context": "当前使用空白知识库：没有系统预设课程资料。生成内容应完全依据学生输入、对话历史和学习画像，不要声称引用了课程知识库。",
            "references": [],
            "query": query,
            "matched_chunks": 0,
            "top_k": 0,
        }
    if get_knowledge_base_mode() == "custom":
        top_k = _dynamic_top_k(intent, query, profile) or 8
        results = retrieve_knowledge(query, top_k=top_k)
        return {
            "context": format_context(results, max_chars=10000),
            "references": build_references(results),
            "query": query,
            "matched_chunks": len(results),
            "top_k": top_k,
        }

    policy = RESOURCE_RETRIEVAL_POLICIES.get(intent)
    if not policy:
        return {
            "context": "当前资源无需直接检索知识库。",
            "references": [],
            "query": query,
            "matched_chunks": 0,
            "top_k": 0,
        }

    top_k = _dynamic_top_k(intent, query, profile)
    primary_count = max(1, round(top_k * (0.65 if intent == "lab" else 0.75)))
    supplement_count = max(1, top_k - primary_count)
    primary_results = retrieve_knowledge(query, top_k=primary_count, resource_type=policy["primary_type"])

    if intent == "lab":
        supplement_results = retrieve_knowledge(query, top_k=supplement_count, resource_type="lecture_slide")
    else:
        supplement_results = retrieve_knowledge(query, top_k=supplement_count)

    merged = _merge_unique_results(primary_results, supplement_results, limit=top_k)
    if len(merged) < top_k:
        merged = _merge_unique_results(
            merged,
            retrieve_knowledge(query, top_k=top_k),
            limit=top_k,
        )

    return {
        "context": format_context(merged, max_chars=policy["max_chars"]),
        "references": build_references(merged),
        "query": query,
        "matched_chunks": len(merged),
        "top_k": top_k,
    }


def attach_resource_references(resource: dict | None, knowledge: dict) -> dict | None:
    if not resource:
        return resource
    resource["metadata"] = {
        **resource.get("metadata", {}),
        "references": knowledge.get("references", []),
        "retrieval": {
            "query": knowledge.get("query", ""),
            "matched_chunks": knowledge.get("matched_chunks", 0),
            "top_k": knowledge.get("top_k", 0),
        },
    }
    return resource


def format_context(results: list[dict], max_chars: int | None = None) -> str:
    if not results:
        return "未找到相关知识库内容。"
    parts = []
    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {})
        source = meta.get("source_file", "未知")
        chapter = meta.get("chapter", "")
        part = f"[{i}] 来源: {source} | 章节: {chapter}\n{r['text']}"
        if max_chars is not None:
            used_chars = sum(len(item) for item in parts) + max(0, len(parts) * 2)
            remaining_chars = max_chars - used_chars
            if remaining_chars <= 0:
                break
            if len(part) > remaining_chars:
                part = f"{part[:max(0, remaining_chars - 10)].rstrip()}\n[片段截断]"
        parts.append(part)
    return "\n\n".join(parts)
