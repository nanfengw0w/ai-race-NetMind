"""Immutable original document reader API; offsets are Unicode codepoints in page.text."""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from services import pdf_documents as store

router = APIRouter(prefix='/api/documents', tags=['documents'])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except store.PDFDocumentError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.post('/upload')
async def upload_document(file: UploadFile = File(...), user_id: str = Form(...), student_id: str = Form(...)):
    try:
        data = await file.read(store.MAX_BYTES + 1)
        from services.chat_service import _ensure_student
        _ensure_student(student_id, user_id)
        return _call(store.create_document, data, file.filename, student_id, user_id)
    finally:
        await file.close()


@router.get('')
def documents(user_id: str, student_id: str):
    return _call(store.list_documents, student_id, user_id)


@router.get('/{document_id}')
def document(document_id: str, user_id: str, student_id: str):
    return _call(store.get_document, document_id, student_id, user_id)


@router.get('/{document_id}/layout')
def layout(document_id: str, user_id: str, student_id: str):
    return _call(store.get_document_layout, document_id, student_id, user_id)


@router.get('/{document_id}/pages/{page_number}')
def page(document_id: str, page_number: int, user_id: str, student_id: str):
    return _call(store.get_page, document_id, page_number, student_id, user_id)


@router.get('/{document_id}/pages/{page_number}/image')
def image(document_id: str, page_number: int, user_id: str, student_id: str):
    return Response(_call(store.get_page, document_id, page_number, student_id, user_id, image=True),
                    media_type='image/png', headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
