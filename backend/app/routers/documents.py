# routers/documents.py — 문서 업로드 → Dify 색인 (P0-5, P0-5a)
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.base import get_db
from app.db.models import Channel, Document
from app.services.dify import DifyClient

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".pptx", ".xlsx", ".csv"}
MAX_FILE_BYTES = 30 * 1024 * 1024  # 30MB


def _dify(settings: Settings = Depends(get_settings)) -> DifyClient:
    return DifyClient(settings)


@router.post("")
async def upload_document(
    file: UploadFile = File(...),
    channel: str = Form("sales-analytics"),
    scope: str = Form("channel"),                 # personal | channel | all (P0-5a)
    settings: Settings = Depends(get_settings),
    dify: DifyClient = Depends(_dify),
    db: Session = Depends(get_db),
):
    # 검증
    name = file.filename or "unnamed"
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, f"지원하지 않는 형식: {ext} (허용: {sorted(ALLOWED_EXTENSIONS)})")
    content = await file.read()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, f"파일이 너무 큽니다 ({len(content)} bytes > {MAX_FILE_BYTES})")
    if scope not in ("personal", "channel", "all"):
        raise HTTPException(422, "scope는 personal|channel|all 중 하나")

    # 채널 확보 + 채널 전용 dataset 확보 (P0-5a: 범위별 dataset 분리)
    ch = db.query(Channel).filter(Channel.name == channel).first()
    if not ch:
        ch = Channel(name=channel)
        db.add(ch)
        db.commit()
    dataset_id = ch.dify_dataset_id or settings.dify_default_dataset_id
    if not dataset_id:
        ds = dify.create_dataset(f"channel-{channel}")
        dataset_id = ds["id"]
        ch.dify_dataset_id = dataset_id
        db.commit()

    # Dify 색인 시작
    res = dify.create_document_by_file(dataset_id, name, content)
    doc = Document(
        channel_id=ch.id, filename=name, scope=scope,
        dify_dataset_id=dataset_id,
        dify_document_id=res["document"]["id"],
        dify_batch=res["batch"],
        indexing_status="indexing",
    )
    db.add(doc)
    db.commit()
    return {"id": doc.id, "filename": name, "scope": scope, "indexing_status": "indexing"}


@router.get("/{doc_id}/status")
def document_status(doc_id: str, dify: DifyClient = Depends(_dify), db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "문서 없음")
    if doc.indexing_status not in ("completed", "error"):
        st = dify.indexing_status(doc.dify_dataset_id, doc.dify_batch)
        doc.indexing_status = st.get("indexing_status", doc.indexing_status)
        doc.error_message = st.get("error") or ""
        db.commit()
    return {"id": doc.id, "filename": doc.filename,
            "indexing_status": doc.indexing_status, "error": doc.error_message}


@router.get("")
def list_documents(channel: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(Document)
    if channel:
        ch = db.query(Channel).filter(Channel.name == channel).first()
        if not ch:
            return {"documents": []}
        q = q.filter(Document.channel_id == ch.id)
    docs = q.order_by(Document.created_at.desc()).limit(100).all()
    return {"documents": [
        {"id": d.id, "filename": d.filename, "scope": d.scope,
         "indexing_status": d.indexing_status, "created_at": d.created_at.isoformat()}
        for d in docs
    ]}


@router.delete("/{doc_id}")
def delete_document(doc_id: str, dify: DifyClient = Depends(_dify), db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "문서 없음")
    dify.delete_document(doc.dify_dataset_id, doc.dify_document_id)
    db.delete(doc)
    db.commit()
    return {"deleted": doc_id}
