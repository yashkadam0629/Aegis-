import os
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="aegis_test_")
os.environ["AEGIS_DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["AEGIS_DATA_DIR"] = str(Path(_tmp, "data"))
os.environ["AEGIS_ARTIFACT_DIR"] = str(Path(_tmp, "artifacts"))

from app.synthetic.generator import GeneratorConfig, generate


@pytest.fixture(scope="session")
def small_ds():
    cfg = GeneratorConfig.quick()
    return generate(cfg)


@pytest.fixture(scope="session")
def seeded(small_ds):
    """Quick synthetic dataset ingested, model trained, pipeline run (shared by ML / API / investigation tests)."""
    from app.db.session import init_db, session_scope
    from app.ingestion.pipeline import load_synthetic
    from app.ml.training import train_and_evaluate
    from app.services.pipeline import run_pipeline
    init_db()
    with session_scope() as s:
        rec = load_synthetic(s, small_ds, "pytest quick")
        ds_id = rec.dataset_id
    with session_scope() as s:
        tr = train_and_evaluate(s, ds_id, log=lambda *a: None)
    with session_scope() as s:
        pr = run_pipeline(s, ds_id)
    return {"dataset_id": ds_id, "train": tr, "pipeline": pr}
