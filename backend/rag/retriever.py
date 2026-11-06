import chromadb
from config import CHROMA_PERSIST_DIR, CHROMA_COLLECTION

_collection = None


def get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
        _collection = client.get_collection(CHROMA_COLLECTION)
    return _collection


def search(query: str, top_k: int = 5, resource_type: str = None) -> list[dict]:
    collection = get_collection()
    where = {"resource_type": resource_type} if resource_type else None
    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    items = []
    for i in range(len(results["ids"][0])):
        items.append({
            "id": results["ids"][0][i],
            "text": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        })
    return items
