from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.models import Base

_engine = None
_Session = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        url = get_settings().database_url
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _pragma(dbapi_con, _):
                cur = dbapi_con.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    Base.metadata.create_all(get_engine())


def reset_engine() -> None:
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine, _Session = None, None


@contextmanager
def session_scope():
    get_engine()
    s: Session = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session():
    """FastAPI dependency."""
    get_engine()
    s: Session = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
