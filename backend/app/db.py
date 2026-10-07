from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import BACKUP_DIR, DATA_DIR, DATABASE_URL


class Base(DeclarativeBase):
    pass


def _connect_args(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def create_db_engine(database_url: str = DATABASE_URL):
    return create_engine(database_url, future=True, connect_args=_connect_args(database_url))


engine = create_db_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_paths() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    from . import models  # noqa: F401
    from .v2 import models as v2_models  # noqa: F401

    init_paths()
    Base.metadata.create_all(bind=engine)
    v2_models.V2Base.metadata.create_all(bind=engine)


def reset_engine(database_url: str) -> None:
    global engine, SessionLocal
    engine.dispose()
    new_engine = create_db_engine(database_url)
    SessionLocal.configure(bind=new_engine)
    engine = new_engine


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
