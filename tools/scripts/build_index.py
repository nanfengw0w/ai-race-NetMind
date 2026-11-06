import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from rag.loader import load_base_data
from rag.chunker import chunk_documents
from rag.embedder import build_chroma_index
from config import BASE_DIR

KB_DIR = BASE_DIR / "data" / "kb"
CHUNKS_FILE = BASE_DIR / "data" / "chunks.jsonl"


def main():
    base_data_dir = str(BASE_DIR.parent / "base_data")
    print(f"Loading from: {base_data_dir}")

    documents = load_base_data(base_data_dir)
    print(f"Loaded {len(documents)} document segments")

    chunks = chunk_documents(documents)
    print(f"Generated {len(chunks)} chunks")

    Path(KB_DIR).mkdir(parents=True, exist_ok=True)
    kb_by_source = {}
    for chunk in chunks:
        src = chunk["source_file"]
        if src not in kb_by_source:
            kb_by_source[src] = []
        kb_by_source[src].append(chunk)

    for source, source_chunks in kb_by_source.items():
        md_path = Path(KB_DIR) / f"{Path(source).stem}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# {source}\n\n")
            for c in source_chunks:
                chapter = c.get("chapter", "")
                if chapter:
                    f.write(f"## {chapter}\n\n")
                f.write(c["text"] + "\n\n")

    with open(CHUNKS_FILE, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    print(f"Saved {len(kb_by_source)} knowledge base files to {KB_DIR}")
    print(f"Saved chunks metadata to {CHUNKS_FILE}")

    build_chroma_index(chunks)
    print("Done!")


if __name__ == "__main__":
    main()
