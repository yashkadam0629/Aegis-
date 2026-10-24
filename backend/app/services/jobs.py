"""Tiny in-process background job runner for long operations (training, pipeline, synthetic generation)."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

_pool = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def submit(kind: str, fn, *args, **kwargs) -> str:
    jid = "JOB-" + uuid.uuid4().hex[:8]
    rec = {"job_id": jid, "kind": kind, "status": "queued", "submitted_at": time.time(), "log": [], "result": None, "error": None}
    with _lock:
        _jobs[jid] = rec

    def run():
        rec["status"] = "running"
        rec["started_at"] = time.time()
        try:
            rec["result"] = fn(*args, log=lambda *a: rec["log"].append(" ".join(str(x) for x in a)[:300]), **kwargs)
            rec["status"] = "completed"
        except Exception as e:  # noqa: BLE001
            rec["status"], rec["error"] = "failed", f"{type(e).__name__}: {e}"
            rec["log"].append(traceback.format_exc()[-800:])
        rec["finished_at"] = time.time()

    _pool.submit(run)
    return jid


def get(jid: str) -> dict | None:
    return _jobs.get(jid)


def list_jobs(limit: int = 20) -> list[dict]:
    return sorted(_jobs.values(), key=lambda j: -j["submitted_at"])[:limit]
