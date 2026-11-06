import re


def _is_chinese(text: str) -> bool:
    chinese_chars = len(re.findall(r'[一-鿿]', text))
    return chinese_chars > len(text) * 0.3


def chunk_documents(documents: list[dict], min_size=200, max_size=800) -> list[dict]:
    chunks = []
    global_counter = 0
    for doc_idx, doc in enumerate(documents):
        text = doc.get("text", "").strip()
        if not text or len(text) < 50:
            continue

        meta = {
            "source_file": doc.get("source_file", ""),
            "resource_type": doc.get("resource_type", ""),
            "chapter": doc.get("chapter", ""),
        }
        if doc.get("slide_number"):
            meta["slide_number"] = doc["slide_number"]

        if _is_chinese(text):
            split_chunks = _split_text(text, min_size=min_size, max_size=max_size)
        else:
            split_chunks = _split_text(text, min_size=150, max_size=500)

        for i, chunk_text in enumerate(split_chunks):
            chunks.append({
                "id": f"chunk_{global_counter:05d}",
                "text": chunk_text,
                **meta,
            })
            global_counter += 1

    return chunks


def _split_text(text: str, min_size: int, max_size: int) -> list[str]:
    if len(text) <= max_size:
        return [text]

    paragraphs = re.split(r'\n{2,}', text)
    result = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 1 <= max_size:
            current = f"{current}\n{para}" if current else para
        else:
            if current:
                result.append(current.strip())
            if len(para) > max_size:
                sentences = re.split(r'(?<=[。！？.!?\n])', para)
                sub = ""
                for s in sentences:
                    if len(sub) + len(s) <= max_size:
                        sub += s
                    else:
                        if sub:
                            result.append(sub.strip())
                        sub = s
                if sub:
                    current = sub
                else:
                    current = ""
            else:
                current = para

    if current.strip():
        result.append(current.strip())

    return [c for c in result if len(c) >= min_size // 2]
