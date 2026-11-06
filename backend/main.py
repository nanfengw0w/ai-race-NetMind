import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from db.database import init_db
from api.rag import router as rag_router
from api.chat import router as chat_router
from api.settings import router as settings_router
from api.artifacts import router as artifacts_router
from api.artifact_revision import router as artifact_revision_router
from api.understanding_checks import router as understanding_checks_router
from api.documents import router as documents_router
from services.pdf_documents import PDFDocumentError, init_pdf_documents
from services.pdf_chat import init_pdf_chat
from services.artifact_store import ArtifactError
from services.conversation_context import ContextBudgetError
from agents.knowledge_agent import retrieve_knowledge

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

app = FastAPI(title="NetMind Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rag_router)
app.include_router(chat_router)
app.include_router(settings_router)
app.include_router(artifacts_router)
app.include_router(artifact_revision_router)
app.include_router(understanding_checks_router)
app.include_router(documents_router)


@app.exception_handler(ArtifactError)
@app.exception_handler(PDFDocumentError)
async def artifact_error_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(ContextBudgetError)
async def context_error_handler(request, exc):
    return JSONResponse(status_code=413, content={"detail": str(exc)})

@app.on_event("startup")
def startup():
    init_db()
    init_pdf_documents()
    init_pdf_chat()
    try:
        retrieve_knowledge("计算机网络", top_k=1)
    except Exception:
        # The API can still serve requests when the optional knowledge index is unavailable.
        pass

@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}
