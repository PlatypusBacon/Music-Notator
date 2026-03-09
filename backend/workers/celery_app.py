"""
workers/celery_app.py
─────────────────────
Celery application instance. Imported by both tasks.py (worker side)
and main.py (API side, for AsyncResult polling).
"""

from celery import Celery
from config import settings

celery_app = Celery(
    "scorescribe",
    broker=settings.redis_url,
    backend=settings.celery_result_backend,
    include=["workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,               # re-queue on worker crash
    worker_prefetch_multiplier=1,      # one heavy ML task per worker at a time
    result_expires=settings.celery_result_expires,
    worker_hijack_root_logger=False,   # keep our logging config intact
)