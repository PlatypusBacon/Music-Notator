"""
config.py
─────────
Single source of truth for all runtime configuration.
Override any value via environment variables (dotenv or shell export).

Usage
─────
    from config import settings
    path = settings.upload_dir / "file.wav"
    url  = settings.output_url("job-id", "score.mid")
"""

from __future__ import annotations
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Storage ──────────────────────────────────────────────────────────────
    upload_dir: Path = Path("/tmp/scorescribe/uploads")
    output_dir: Path = Path("/tmp/scorescribe/outputs")

    # ── Public base URL (used to build download URLs returned to Flutter) ────
    # Change to your deployed domain / CDN in production.
    base_url: str = "http://localhost:8000"

    # ── Redis / Celery ───────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_result_expires: int = 3600   # seconds job results stay in Redis

    # ── Demucs ───────────────────────────────────────────────────────────────
    demucs_model: str = "htdemucs"      # htdemucs | htdemucs_6s | mdx_extra

    # ── Basic Pitch defaults (overridable per-request) ───────────────────────
    onset_threshold: float = 0.5
    frame_threshold: float = 0.3
    min_note_length_ms: int = 58

    # ── File upload limits ───────────────────────────────────────────────────
    max_upload_mb: int = 200

    def output_url(self, job_id: str, filename: str) -> str:
        """Build a full URL to a job output file."""
        return f"{self.base_url}/outputs/{job_id}/{filename}"

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()