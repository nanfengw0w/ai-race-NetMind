import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from services.llm_client import LLM_MAX_OUTPUT_TOKENS, chat
from services.usage_tracker import estimate_tokens


DIRECT_INPUT_TOKENS = int(os.getenv("NETMIND_DIRECT_INPUT_TOKENS", "12000"))
CHUNK_TOKENS = int(os.getenv("NETMIND_LONG_INPUT_CHUNK_TOKENS", "5000"))
REDUCE_INPUT_TOKENS = int(os.getenv("NETMIND_REDUCE_INPUT_TOKENS", "9000"))
MAX_WORKERS = max(1, int(os.getenv("NETMIND_LONG_INPUT_WORKERS", "3")))


def _split_large_block(text: str, max_chars: int) -> list[str]:
    return [text[index:index + max_chars] for index in range(0, len(text), max_chars)]


def split_text(text: str, target_tokens: int = CHUNK_TOKENS) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    max_chars = max(target_tokens * 2, 2000)
    paragraphs = re.split(r"\n{2,}", value)
    chunks = []
    current = []
    current_chars = 0
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_chars = 0
            chunks.extend(_split_large_block(paragraph, max_chars))
            continue
        if current and current_chars + len(paragraph) > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_chars = 0
        current.append(paragraph)
        current_chars += len(paragraph)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _fallback_extract(chunk: str) -> str:
    if len(chunk) <= 2200:
        return chunk
    return f"{chunk[:1400]}\n\n[...]\n\n{chunk[-700:]}"


def _extract_chunk(index: int, total: int, chunk: str, query_hint: str) -> str:
    prompt = [
        {
            "role": "system",
            "content": (
                "提取资料中与问题相关的事实、术语、数值和来源，用中文Markdown整理；无关时返回“无相关信息”。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"问题：{query_hint}\n\n"
                f"资料 {index}/{total}：\n{chunk}"
            ),
        },
    ]
    try:
        return chat(prompt, temperature=0.1, max_tokens=LLM_MAX_OUTPUT_TOKENS).strip()
    except Exception:
        return _fallback_extract(chunk)


def _reduce_summaries(summaries: list[str], query_hint: str) -> str:
    combined = "\n\n".join(
        f"### Chunk {index}\n{summary}" for index, summary in enumerate(summaries, 1)
        if summary.strip() and summary.strip() != "无相关信息"
    )
    if estimate_tokens(combined) <= REDUCE_INPUT_TOKENS:
        return combined

    groups = split_text(combined, REDUCE_INPUT_TOKENS)
    reduced = []
    for index, group in enumerate(groups, 1):
        try:
            reduced.append(chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "合并资料要点并去重，保留事实、数值与来源，用中文Markdown整理。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"问题：{query_hint}\n\n资料 {index}/{len(groups)}：\n{group}",
                    },
                ],
                temperature=0.1,
                max_tokens=LLM_MAX_OUTPUT_TOKENS,
            ).strip())
        except Exception:
            reduced.append(_fallback_extract(group))
    return "\n\n".join(reduced)


def prepare_model_input(
    user_message: str,
    attachment_text: str = "",
    attachment_names: list[str] | None = None,
) -> tuple[str, dict]:
    started_at = time.perf_counter()
    message = str(user_message or "").strip()
    attachment_text = str(attachment_text or "").strip()
    combined = message
    if attachment_text:
        combined = (
            f"{message}\n\n"
            "附件资料：\n\n"
            f"{attachment_text}"
        )

    original_tokens = estimate_tokens(combined)
    diagnostics = {
        "strategy": "direct",
        "original_tokens": original_tokens,
        "compressed_tokens": original_tokens,
        "chunks": 1 if combined else 0,
        "attachment_names": attachment_names or [],
        "elapsed_ms": 0,
    }
    if original_tokens <= DIRECT_INPUT_TOKENS:
        diagnostics["elapsed_ms"] = round((time.perf_counter() - started_at) * 1000)
        return combined, diagnostics

    query_hint = message
    if estimate_tokens(query_hint) > 1800:
        query_hint = f"{message[:2200]}\n\n[...]\n\n{message[-1200:]}"

    chunks = split_text(combined)
    summaries = [""] * len(chunks)
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(chunks))) as executor:
        futures = {
            executor.submit(_extract_chunk, index, len(chunks), chunk, query_hint): index - 1
            for index, chunk in enumerate(chunks, 1)
        }
        for future in as_completed(futures):
            summaries[futures[future]] = future.result()

    evidence = _reduce_summaries(summaries, query_hint)
    prepared = (
        f"问题：{query_hint}\n\n"
        f"资料摘要：\n{evidence}"
    )
    diagnostics.update({
        "strategy": "map_reduce",
        "chunks": len(chunks),
        "compressed_tokens": estimate_tokens(prepared),
        "elapsed_ms": round((time.perf_counter() - started_at) * 1000),
    })
    print(f"[NetMind Long Input] {json.dumps(diagnostics, ensure_ascii=False)}", flush=True)
    return prepared, diagnostics
