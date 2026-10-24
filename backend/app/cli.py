"""Command line entry points:  python -m app.cli <command>

  init-db                    create tables
  generate  [--quick] [--out DIR] [--seed N]   write synthetic CSVs + hidden ground truth
  seed      [--quick] [--seed N]               generate + ingest into the database
  train     [--dataset ID]                     chronological training / evaluation, saves model run
  pipeline  [--dataset ID]                     features -> scoring -> detectors -> risk -> alerts
  all       [--quick]                          init-db, seed, train, pipeline
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from app.core.config import get_settings
from app.db import models as m
from app.db.session import init_db, session_scope


def cmd_generate(a):
    from app.synthetic.generator import GeneratorConfig, generate
    cfg = GeneratorConfig.quick() if a.quick else GeneratorConfig()
    cfg.seed = a.seed
    t = time.time()
    ds = generate(cfg)
    out = Path(a.out or get_settings().data_dir / ("synthetic_quick" if a.quick else "synthetic"))
    info = ds.save(out)
    print(json.dumps(info), f"({time.time() - t:.1f}s)")


def cmd_seed(a):
    from app.ingestion.pipeline import load_synthetic
    from app.synthetic.generator import GeneratorConfig, generate
    init_db()
    cfg = GeneratorConfig.quick() if a.quick else GeneratorConfig()
    cfg.seed = a.seed
    ds = generate(cfg)
    with session_scope() as s:
        rec = load_synthetic(s, ds, f"AEGIS synthetic ({'quick' if a.quick else 'standard'}, seed {a.seed})")
        r = rec.report
        print(f"dataset {rec.dataset_id}: accepted {r['rows_accepted']} / received {r['rows_received']} (rejected {r['rows_rejected']}, duplicates {r['duplicate_records']})")


def _latest_dataset(s) -> str:
    d = s.query(m.Dataset).order_by(m.Dataset.created_at.desc()).first()
    if d is None:
        sys.exit("no dataset; run `python -m app.cli seed` first")
    return d.dataset_id


def cmd_train(a):
    from app.ml.training import train_and_evaluate
    init_db()
    with session_scope() as s:
        res = train_and_evaluate(s, a.dataset or _latest_dataset(s))
        print("model run:", res["model_run_id"], "selected:", res["selected_model"])


def cmd_pipeline(a):
    from app.services.pipeline import run_pipeline
    init_db()
    with session_scope() as s:
        res = run_pipeline(s, a.dataset or _latest_dataset(s), log=print)
        print(json.dumps({k: v for k, v in res.items() if k != "timings"}, default=str)[:400])


def main(argv=None):
    p = argparse.ArgumentParser(prog="aegis")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(fn=lambda a: (init_db(), print("tables created")))
    for name, fn in (("generate", cmd_generate), ("seed", cmd_seed), ("train", cmd_train), ("pipeline", cmd_pipeline), ("all", None)):
        sp = sub.add_parser(name)
        sp.add_argument("--quick", action="store_true")
        sp.add_argument("--seed", type=int, default=42)
        sp.add_argument("--out")
        sp.add_argument("--dataset")
        sp.set_defaults(fn=fn)

    a = p.parse_args(argv)
    if a.cmd == "all":
        for f in (cmd_seed, cmd_train, cmd_pipeline):
            f(a)
        return
    a.fn(a)


if __name__ == "__main__":
    main()
