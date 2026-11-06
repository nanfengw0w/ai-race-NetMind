from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.artifact_revision import revise_artifact
from services.artifact_selection import ArtifactSelection
from services.usage_tracker import usage_scope

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


class RevisionRequest(BaseModel):
    student_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    base_version: int = Field(gt=0)
    expected_version: int = Field(gt=0)
    instruction: str = Field(min_length=1)
    selection: ArtifactSelection | None = None


@router.post("/{resource_id}/revise")
def revise(resource_id: int, body: RevisionRequest):
    with usage_scope(body.user_id, body.student_id):
        return revise_artifact(resource_id, **body.model_dump())
