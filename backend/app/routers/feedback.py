# routers/feedback.py — 답변 피드백 👍/👎 (P1-2)
# 메시지당 피드백 1건 유지: 같은 평가 재클릭 시 rating=0으로 취소, 다른 평가 클릭 시 교체
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Feedback, Message

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    message_id: str
    rating: int              # +1(👍) | -1(👎) | 0(취소)
    comment: str = ""


@router.post("")
def submit_feedback(req: FeedbackRequest, db: Session = Depends(get_db)):
    if req.rating not in (1, -1, 0):
        raise HTTPException(422, "rating은 1, -1, 0 중 하나")
    msg = db.get(Message, req.message_id)
    if not msg or msg.role != "assistant":
        raise HTTPException(404, "피드백 대상 assistant 메시지가 없습니다")

    fb = db.query(Feedback).filter(Feedback.message_id == req.message_id).first()
    if req.rating == 0:                      # 취소
        if fb:
            db.delete(fb)
            db.commit()
        return {"message_id": req.message_id, "rating": 0}
    if fb:                                   # 교체 (👍→👎 등)
        fb.rating = req.rating
        fb.comment = req.comment
    else:
        db.add(Feedback(message_id=req.message_id, rating=req.rating, comment=req.comment))
    db.commit()
    return {"message_id": req.message_id, "rating": req.rating}


@router.get("/stats")
def feedback_stats(db: Session = Depends(get_db)):
    """파일럿 지표(PRD: 만족도 ≥70%) 수집용 요약."""
    rows = db.query(Feedback.rating).all()
    up = sum(1 for (r,) in rows if r > 0)
    down = sum(1 for (r,) in rows if r < 0)
    total = up + down
    return {"up": up, "down": down, "total": total,
            "satisfaction": round(up / total, 3) if total else None}
