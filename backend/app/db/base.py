# db/base.py — SQLAlchemy 2.0 엔진/세션 (PRD §7.4: SQLite → PostgreSQL 전환 대비)
# 규율: ORM만 사용(raw SQL 금지), SQLite 전용 기능 회피 → connection string 교체만으로 PG 전환
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = get_settings().database_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
