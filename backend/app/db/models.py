# db/models.py — 도메인 모델 (PRD P0-8)
# 채널(분석 주제 스페이스) / 메시지·스레드 / 문서 메타 / 피드백
# P2-3 대비: 대화-대시보드 관계는 1:N 확장이 가능하도록 채널 단위로 모델링
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Channel(Base):
    """분석 주제 스페이스 (2a안의 채널). v1은 싱글 유저 관점."""
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(80), unique=True)          # e.g. "sales-analytics"
    description: Mapped[str] = mapped_column(String(255), default="")
    tableau_view_url: Mapped[str] = mapped_column(String(500), default="")   # 언펀 카드 기본 뷰
    dify_dataset_id: Mapped[str] = mapped_column(String(64), default="")     # 채널 지식 범위 (P0-5a)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    messages: Mapped[list["Message"]] = relationship(back_populates="channel", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship(back_populates="channel", cascade="all, delete-orphan")


class Message(Base):
    """채널 메시지. thread_root_id로 스레드 구성. role: user | assistant."""
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), index=True)
    thread_root_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(16))                       # user | assistant
    content: Mapped[str] = mapped_column(Text)
    # 봇 응답 메타 (P0-9 출처 표시): {"data_sources": [...], "doc_citations": [...], "route": "fusion"}
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    # 마크 선택 컨텍스트 (P0-1): {"month": "May", "region": "West", ...}
    mark_context_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    channel: Mapped[Channel] = relationship(back_populates="messages")
    feedbacks: Mapped[list["Feedback"]] = relationship(back_populates="message", cascade="all, delete-orphan")


class Document(Base):
    """지식베이스 문서 메타 (P0-5). 실제 콘텐츠/색인은 Dify가 보관."""
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    scope: Mapped[str] = mapped_column(String(16), default="channel")   # personal | channel | all (P0-5a)
    dify_dataset_id: Mapped[str] = mapped_column(String(64))
    dify_document_id: Mapped[str] = mapped_column(String(64))
    dify_batch: Mapped[str] = mapped_column(String(64))
    indexing_status: Mapped[str] = mapped_column(String(24), default="waiting")  # waiting|indexing|completed|error
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    channel: Mapped[Channel] = relationship(back_populates="documents")


class Feedback(Base):
    """답변 피드백 (P1-2 대비 스키마만 선반영)."""
    __tablename__ = "feedbacks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    rating: Mapped[float] = mapped_column(Float)                        # +1 / -1
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    message: Mapped[Message] = relationship(back_populates="feedbacks")
