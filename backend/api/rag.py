from fastapi import APIRouter
from pydantic import BaseModel
from agents.knowledge_agent import retrieve_knowledge

router = APIRouter(prefix="/api/rag", tags=["rag"])


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    resource_type: str = None


@router.post("/search")
def rag_search(req: SearchRequest):
    results = retrieve_knowledge(req.query, top_k=req.top_k, resource_type=req.resource_type)
    return {"results": results}
