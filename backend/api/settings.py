from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from services.app_settings import get_public_settings, update_app_settings
from services.custom_kb import (
    clear_custom_knowledge_files,
    create_custom_knowledge_job,
    delete_custom_knowledge_file,
    get_custom_knowledge_job,
    run_custom_knowledge_job,
)
from services.resource_storage import (
    cleanup_resources_older_than,
    get_resource_storage_stats,
)
from services.user_memory import get_user_memory, update_user_memory


router = APIRouter(prefix="/api/settings", tags=["settings"])


class AISettingsRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class AppSettingsRequest(BaseModel):
    ai: AISettingsRequest | None = None
    knowledge_base: str | None = None


class ResourceCleanupRequest(BaseModel):
    days: int = 30
    resource_types: list[str] = Field(default_factory=list)


class UserMemoryRequest(BaseModel):
    enabled: bool = False
    content: str = ""


@router.get("")
def read_settings():
    return get_public_settings()


@router.put("")
def write_settings(req: AppSettingsRequest):
    payload = req.model_dump(exclude_unset=True)
    return update_app_settings(payload)


@router.get("/storage")
def read_resource_storage():
    return get_resource_storage_stats()


@router.post("/storage/cleanup")
def cleanup_resource_storage(req: ResourceCleanupRequest):
    return cleanup_resources_older_than(req.days, req.resource_types)


@router.get("/memory/{user_id}")
def read_user_memory(user_id: str):
    return get_user_memory(user_id)


@router.put("/memory/{user_id}")
def write_user_memory(user_id: str, req: UserMemoryRequest):
    return update_user_memory(user_id, req.enabled, req.content)


@router.post("/knowledge-base/upload")
async def upload_knowledge_base(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
):
    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一个资料文件。")
    job = await create_custom_knowledge_job(files)
    settings = update_app_settings({"knowledge_base": "custom"})
    background_tasks.add_task(run_custom_knowledge_job, job["id"])
    return {
        "settings": settings,
        "job": job,
    }


@router.get("/knowledge-base/jobs/{job_id}")
def read_knowledge_base_job(job_id: str):
    job = get_custom_knowledge_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="未找到该建库任务。")
    return job


@router.delete("/knowledge-base/files/{filename}")
def delete_knowledge_base_file(
    filename: str,
    background_tasks: BackgroundTasks,
):
    try:
        job = delete_custom_knowledge_file(filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="未找到该资料文件。") from None
    background_tasks.add_task(run_custom_knowledge_job, job["id"])
    return {"job": job}


@router.delete("/knowledge-base/files")
def clear_knowledge_base_files(background_tasks: BackgroundTasks):
    job = clear_custom_knowledge_files()
    background_tasks.add_task(run_custom_knowledge_job, job["id"])
    return {"job": job}
