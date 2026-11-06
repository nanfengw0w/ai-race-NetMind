"""Artifact routes; identity follows existing student_id/user_id ownership."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services import artifact_store


router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


class ArtifactIdentity(BaseModel):
    student_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class CreateArtifactRequest(ArtifactIdentity):
    type: str = Field(min_length=1)
    title: str
    content: str
    metadata: dict | None = None
    source_conversation_id: int | None = None


class FromMessageRequest(ArtifactIdentity):
    conversation_id: int = Field(gt=0)
    title: str | None = None


class RestoreArtifactRequest(ArtifactIdentity):
    version: int = Field(gt=0)
    expected_version: int = Field(gt=0)
    slide_number: int | None = Field(default=None, gt=0, strict=True)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except artifact_store.ArtifactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("")
def search_artifacts(student_id: str = Query(min_length=1), user_id: str = Query(min_length=1), q: str = ""):
    return _call(artifact_store.search_artifacts, student_id, user_id, q)


@router.post("")
def create_artifact(body: CreateArtifactRequest):
    return _call(artifact_store.create_artifact, **body.model_dump())


@router.post("/from-message")
def create_from_message(body: FromMessageRequest):
    return _call(artifact_store.create_artifact_from_message, **body.model_dump())


@router.get("/{resource_id}")
def read_artifact(resource_id: int, student_id: str = Query(min_length=1),
                  user_id: str = Query(min_length=1), version: int | None = Query(default=None, gt=0)):
    return _call(artifact_store.read_artifact, resource_id, version, student_id, user_id)


@router.get("/{resource_id}/versions")
def list_versions(resource_id: int, student_id: str = Query(min_length=1), user_id: str = Query(min_length=1)):
    return _call(artifact_store.list_versions, resource_id, student_id, user_id)


@router.get("/{resource_id}/slides")
def list_slides(resource_id: int, student_id: str = Query(min_length=1),
                user_id: str = Query(min_length=1), version: int = Query(gt=0)):
    from services.ppt_slides import list_slides as extract
    artifact = _call(artifact_store.read_artifact, resource_id, version, student_id, user_id)
    if artifact["type"] != "ppt":
        raise HTTPException(400, "只有 PPT 课件可以选择页面。")
    try:
        return extract(artifact["content"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{resource_id}/restore")
def restore_artifact(resource_id: int, body: RestoreArtifactRequest):
    values = body.model_dump(exclude_none=True)
    function = artifact_store.restore_slide_version if body.slide_number is not None else artifact_store.restore_version
    return _call(function, resource_id, **values)
