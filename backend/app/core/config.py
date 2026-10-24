"""Environment-driven configuration. No secrets are hardcoded; see .env.example."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # aegis/


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _env("AEGIS_DATABASE_URL", f"sqlite:///{(ROOT / 'data' / 'aegis.db').as_posix()}"))
    artifact_dir: Path = field(default_factory=lambda: Path(_env("AEGIS_ARTIFACT_DIR", str(ROOT / "artifacts"))))
    data_dir: Path = field(default_factory=lambda: Path(_env("AEGIS_DATA_DIR", str(ROOT / "data"))))
    api_key: str | None = field(default_factory=lambda: os.environ.get("AEGIS_API_KEY") or None)
    cors_origins: list[str] = field(default_factory=lambda: _env("AEGIS_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","))
    max_upload_mb: int = field(default_factory=lambda: int(_env("AEGIS_MAX_UPLOAD_MB", "50")))
    max_page_size: int = 500
    graph_max_nodes: int = field(default_factory=lambda: int(_env("AEGIS_GRAPH_MAX_NODES", "400")))
    graph_max_edges: int = field(default_factory=lambda: int(_env("AEGIS_GRAPH_MAX_EDGES", "1500")))
    query_timeout_s: float = field(default_factory=lambda: float(_env("AEGIS_QUERY_TIMEOUT_S", "10")))
    max_hops: int = 6
    max_paths: int = 200
    structuring_threshold: float = field(default_factory=lambda: float(_env("AEGIS_STRUCTURING_THRESHOLD", "50000")))

    def ensure_dirs(self) -> None:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
