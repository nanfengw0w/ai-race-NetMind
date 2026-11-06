import chromadb
from config import CHROMA_PERSIST_DIR, CHROMA_COLLECTION


def get_chroma_client():
    return chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)


def build_chroma_index(chunks: list[dict]):
    client = get_chroma_client()

    try:
        client.delete_collection(CHROMA_COLLECTION)
    except Exception:
        pass

    collection = client.create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    ids = []
    documents = []
    metadatas = []

    for chunk in chunks:
        ids.append(chunk["id"])
        documents.append(chunk["text"])
        metadatas.append({
            "source_file": chunk.get("source_file", ""),
            "resource_type": chunk.get("resource_type", ""),
            "chapter": chunk.get("chapter", ""),
        })

    batch_size = 100
    for i in range(0, len(ids), batch_size):
        end = min(i + batch_size, len(ids))
        collection.add(
            ids=ids[i:end],
            documents=documents[i:end],
            metadatas=metadatas[i:end],
        )

    print(f"Built Chroma index: {len(ids)} chunks in collection '{CHROMA_COLLECTION}'")
    return collection
