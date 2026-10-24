from __future__ import annotations

import hmac

import numpy as np
import pandas as pd
from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models as m
from app.db.session import get_session


def require_key(x_api_key: str | None = Header(default=None)):
    """Local-development auth: if AEGIS_API_KEY is set, requests must send it as X-API-Key. NOT a production auth scheme."""
    key = get_settings().api_key
    if key and not (x_api_key and hmac.compare_digest(x_api_key, key)):
        raise HTTPException(status_code=401, detail="missing or invalid API key")


def actor(x_analyst: str | None = Header(default=None)) -> str:
    a = (x_analyst or "analyst@local").strip()[:100]
    return a or "analyst@local"


def page_params(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500)):
    return page, min(page_size, get_settings().max_page_size)


def resolve_dataset(s: Session, dataset_id: str | None) -> m.Dataset:
    ds = s.get(m.Dataset, dataset_id) if dataset_id else s.query(m.Dataset).filter(m.Dataset.status == "ready").order_by(m.Dataset.created_at.desc()).first()
    if ds is None:
        raise HTTPException(404, "no such dataset" if dataset_id else "no datasets loaded; seed or import one first")
    return ds


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if (np.isnan(o) or np.isinf(o)) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    return o


SessionDep = Depends(get_session)
