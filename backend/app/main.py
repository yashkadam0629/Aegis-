"""AEGIS API entry point.  uvicorn app.main:app --reload"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import case_routes, data_routes
from app.core.config import get_settings
from app.db.session import get_engine, init_db

log = logging.getLogger("aegis")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

DISCLAIMER = ("AEGIS is a research and investigation-support prototype. It surfaces potentially suspicious patterns and model-generated risk scores "
              "for analyst review. It is not a certified AML system and makes no legal determinations.")

app = FastAPI(title="AEGIS – Financial Crime Intelligence API", version="0.1.0", description=DISCLAIMER)
settings = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    init_db()
    log.info("AEGIS API started; database=%s", settings.database_url.split("///")[0])


@app.middleware("http")
async def timing(request: Request, call_next):
    t = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "internal server error"}, status_code=500)  # no internals leaked
    resp.headers["X-Process-Time-ms"] = f"{(time.perf_counter() - t) * 1000:.1f}"
    return resp


@app.get("/api/health", tags=["system"])
def health():
    from sqlalchemy import text
    ok = True
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        ok = False
    return {"status": "ok" if ok else "degraded", "database": ok, "version": app.version, "disclaimer": DISCLAIMER}


app.include_router(data_routes.router, prefix="/api")
app.include_router(case_routes.router, prefix="/api")
