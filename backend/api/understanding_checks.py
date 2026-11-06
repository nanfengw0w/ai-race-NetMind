"""Owner-scoped understanding-check endpoints."""
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, ConfigDict
from services import understanding_checks as checks
from services.usage_tracker import usage_scope

router = APIRouter(prefix='/api/understanding-checks', tags=['understanding-checks'])


class AttemptRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    student_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)


class SubmitRequest(AttemptRequest):
    user_answer: str = Field(min_length=1, max_length=20000)
    request_id: str = Field(min_length=1, max_length=200)


@router.get('/{check_id}')
def get_check(check_id: str, student_id: str = Query(min_length=1), user_id: str = Query(min_length=1)):
    return {'check': checks.get_check(check_id, student_id, user_id)}


@router.post('/{check_id}/hint')
def hint(check_id: str, body: AttemptRequest):
    return {'check': checks.request_hint(check_id, **body.model_dump())}


@router.post('/{check_id}/retry')
def retry(check_id: str, body: AttemptRequest):
    return {'check': checks.retry_check(check_id, **body.model_dump())}


@router.post('/{check_id}/submit')
def submit(check_id: str, body: SubmitRequest):
    with usage_scope(body.user_id, body.student_id):
        return {'check': checks.submit_answer(check_id, **body.model_dump())}
