import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import chromadb
from docx import Document as DocxDocument
from fastapi import UploadFile
from pptx import Presentation

from config import BASE_DIR, CHROMA_PERSIST_DIR
from rag.chunker import chunk_documents


CUSTOM_KB_DIR = Path(BASE_DIR) / "data" / "custom_kb"
CUSTOM_UPLOAD_DIR = CUSTOM_KB_DIR / "uploads"
CUSTOM_STATS_PATH = CUSTOM_KB_DIR / "stats.json"
CUSTOM_COLLECTION = "netmind_custom_knowledge"
SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".docx", ".pptx"}

_custom_collection = None
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_job(job_id: str, **changes) -> dict:
    with _jobs_lock:
        job = _jobs.setdefault(job_id, {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "message": "等待处理",
            "created_at": _now_iso(),
        })
        job.update(changes)
        job["updated_at"] = _now_iso()
        return dict(job)


def get_custom_knowledge_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def create_custom_rebuild_job(message: str) -> dict:
    job_id = uuid.uuid4().hex
    return _set_job(
        job_id,
        status="processing",
        stage="parsing",
        progress=18,
        message=message,
    )


def _safe_filename(filename: str) -> str:
    raw_name = Path(filename or "upload.txt").name.strip() or "upload.txt"
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._ -]+", "_", Path(raw_name).stem).strip(" ._")
    suffix = Path(raw_name).suffix.lower()
    return f"{stem or 'upload'}{suffix}"


def _read_text_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return []
    return [{
        "source_file": path.name,
        "resource_type": "custom_upload",
        "chapter": path.stem,
        "text": text,
    }]


def _read_docx(path: Path) -> list[dict]:
    doc = DocxDocument(str(path))
    documents = []
    current_heading = path.stem
    buffer = []

    def flush():
        nonlocal buffer
        if buffer:
            documents.append({
                "source_file": path.name,
                "resource_type": "custom_upload",
                "chapter": current_heading,
                "text": "\n".join(buffer).strip(),
            })
            buffer = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if para.style.name.startswith("Heading"):
            flush()
            current_heading = text
        else:
            buffer.append(text)
    flush()
    return documents


def _read_pptx(path: Path) -> list[dict]:
    prs = Presentation(str(path))
    documents = []
    for index, slide in enumerate(prs.slides, 1):
        title = ""
        body = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for para in shape.text_frame.paragraphs:
                text = para.text.strip()
                if not text:
                    continue
                if not title:
                    title = text
                else:
                    body.append(text)
        text = "\n".join([title, *body]).strip()
        if text:
            documents.append({
                "source_file": path.name,
                "resource_type": "custom_upload",
                "chapter": title or f"第 {index} 页",
                "slide_number": index,
                "text": text,
            })
    return documents


def _load_uploaded_documents(progress: Callable[[int, str], None] | None = None) -> list[dict]:
    documents = []
    paths = [
        path for path in sorted(CUSTOM_UPLOAD_DIR.glob("*"))
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    total = max(len(paths), 1)
    for index, path in enumerate(paths, 1):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if path.suffix.lower() in {".txt", ".md", ".markdown"}:
            documents.extend(_read_text_file(path))
        elif path.suffix.lower() == ".docx":
            documents.extend(_read_docx(path))
        elif path.suffix.lower() == ".pptx":
            documents.extend(_read_pptx(path))
        if progress:
            progress(round(index / total * 100), f"正在解析 {path.name}")
    return documents


def _write_stats(files: list[Path], chunks: list[dict]) -> dict:
    chunk_counts: dict[str, int] = {}
    for chunk in chunks:
        source = chunk.get("source_file", "未知文件")
        chunk_counts[source] = chunk_counts.get(source, 0) + 1
    stats = {
        "files": len(files),
        "chunks": len(chunks),
        "file_names": [path.name for path in files],
        "file_details": [{
            "name": path.name,
            "type": path.suffix.lower().lstrip("."),
            "size_bytes": path.stat().st_size,
            "chunks": chunk_counts.get(path.name, 0),
        } for path in files],
        "chunk_previews": [{
            "source_file": chunk.get("source_file", ""),
            "chapter": chunk.get("chapter", ""),
            "excerpt": str(chunk.get("text", "")).strip()[:180],
        } for chunk in chunks[:8]],
        "updated_at": _now_iso(),
    }
    CUSTOM_KB_DIR.mkdir(parents=True, exist_ok=True)
    CUSTOM_STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def get_custom_knowledge_stats() -> dict:
    if CUSTOM_STATS_PATH.exists():
        try:
            stats = json.loads(CUSTOM_STATS_PATH.read_text(encoding="utf-8"))
            if "file_details" in stats and "chunk_previews" in stats:
                return stats
            client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
            collection = client.get_collection(CUSTOM_COLLECTION)
            stored = collection.get(include=["documents", "metadatas"])
            chunk_counts: dict[str, int] = {}
            reconstructed = []
            for index, text in enumerate(stored.get("documents") or []):
                metadata = (stored.get("metadatas") or [])[index] or {}
                source = metadata.get("source_file", "未知文件")
                chunk_counts[source] = chunk_counts.get(source, 0) + 1
                reconstructed.append({
                    "source_file": source,
                    "chapter": metadata.get("chapter", ""),
                    "text": text,
                })
            files = sorted(path for path in CUSTOM_UPLOAD_DIR.glob("*") if path.is_file())
            return _write_stats(files, reconstructed)
        except (OSError, json.JSONDecodeError):
            pass
        except Exception:
            return stats
    files = sorted(path for path in CUSTOM_UPLOAD_DIR.glob("*") if path.is_file()) if CUSTOM_UPLOAD_DIR.exists() else []
    return {
        "files": len(files),
        "chunks": 0,
        "file_names": [path.name for path in files],
        "file_details": [],
        "chunk_previews": [],
    }


def rebuild_custom_knowledge_base(
    progress: Callable[[str, int, str], None] | None = None,
) -> dict:
    global _custom_collection
    CUSTOM_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if progress:
        progress("parsing", 28, "正在读取文件结构与正文")
    documents = _load_uploaded_documents(
        lambda percent, message: progress("parsing", 28 + round(percent * 0.2), message)
        if progress else None
    )
    if progress:
        progress("chunking", 52, f"已提取 {len(documents)} 个内容区块，正在切分知识片段")
    chunks = chunk_documents(documents, min_size=120, max_size=700)
    for index, chunk in enumerate(chunks):
        chunk["id"] = f"custom_{index:05d}"

    if progress:
        progress("embedding", 66, f"已生成 {len(chunks)} 个片段，正在写入向量库")
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    try:
        client.delete_collection(CUSTOM_COLLECTION)
    except Exception:
        pass

    if chunks:
        collection = client.create_collection(
            name=CUSTOM_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        for start in range(0, len(chunks), 100):
            batch = chunks[start:start + 100]
            collection.add(
                ids=[item["id"] for item in batch],
                documents=[item["text"] for item in batch],
                metadatas=[{
                    "source_file": item.get("source_file", ""),
                    "resource_type": item.get("resource_type", "custom_upload"),
                    "chapter": item.get("chapter", ""),
                } for item in batch],
            )
            if progress:
                written = min(start + len(batch), len(chunks))
                percent = 66 + round((written / max(len(chunks), 1)) * 29)
                progress("embedding", percent, f"已向量化 {written}/{len(chunks)} 个知识片段")
        _custom_collection = collection
    else:
        _custom_collection = None

    files = sorted(path for path in CUSTOM_UPLOAD_DIR.glob("*") if path.is_file())
    return _write_stats(files, chunks)


async def create_custom_knowledge_job(files: list[UploadFile]) -> dict:
    job_id = uuid.uuid4().hex
    _set_job(
        job_id,
        status="uploading",
        stage="uploading",
        progress=5,
        message="正在保存上传文件",
        files=[],
    )
    CUSTOM_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    rejected = []
    total = max(len(files), 1)
    for index, upload in enumerate(files, 1):
        name = _safe_filename(upload.filename)
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            rejected.append({"file": upload.filename, "reason": "unsupported_type"})
            continue
        target = CUSTOM_UPLOAD_DIR / name
        if target.exists():
            target = CUSTOM_UPLOAD_DIR / f"{target.stem}-{uuid.uuid4().hex[:6]}{target.suffix}"
        with target.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        saved.append(target.name)
        _set_job(
            job_id,
            progress=5 + round(index / total * 18),
            message=f"已保存 {index}/{len(files)} 个文件",
            files=saved,
            rejected=rejected,
        )
    return _set_job(
        job_id,
        status="processing",
        stage="parsing",
        progress=24,
        message="文件上传完成，准备解析内容",
        files=saved,
        rejected=rejected,
    )


def delete_custom_knowledge_file(filename: str) -> dict:
    target_name = Path(filename or "").name
    target = CUSTOM_UPLOAD_DIR / target_name
    if not target_name or not target.is_file():
        raise FileNotFoundError(target_name)
    target.unlink()
    return create_custom_rebuild_job(f"已删除 {target_name}，正在重建知识库")


def clear_custom_knowledge_files() -> dict:
    CUSTOM_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    deleted = 0
    for path in CUSTOM_UPLOAD_DIR.iterdir():
        if path.is_file():
            path.unlink()
            deleted += 1
    job = create_custom_rebuild_job(f"已删除 {deleted} 个文件，正在清空向量库")
    job["deleted_files"] = deleted
    return _set_job(job["id"], deleted_files=deleted)


def run_custom_knowledge_job(job_id: str):
    def report(stage: str, progress: int, message: str):
        _set_job(
            job_id,
            status="processing",
            stage=stage,
            progress=min(max(progress, 0), 99),
            message=message,
        )

    try:
        stats = rebuild_custom_knowledge_base(report)
        _set_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            message=f"建库完成，共 {stats['files']} 个文件、{stats['chunks']} 个知识片段",
            result=stats,
        )
    except Exception as exc:
        _set_job(
            job_id,
            status="failed",
            stage="failed",
            message=f"建库失败：{exc}",
            error=str(exc),
        )


def get_custom_collection():
    global _custom_collection
    if _custom_collection is not None:
        return _custom_collection
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    try:
        _custom_collection = client.get_collection(CUSTOM_COLLECTION)
    except Exception:
        _custom_collection = None
    return _custom_collection


def search_custom_knowledge(query: str, top_k: int = 5) -> list[dict]:
    collection = get_custom_collection()
    if collection is None:
        return []
    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    items = []
    ids = results.get("ids", [[]])[0]
    for index in range(len(ids)):
        items.append({
            "id": ids[index],
            "text": results["documents"][0][index],
            "metadata": results["metadatas"][0][index],
            "distance": results["distances"][0][index],
        })
    return items
