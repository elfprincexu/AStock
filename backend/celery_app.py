import os

from celery import Celery
from celery.schedules import crontab

_redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_celery_backend = _redis_url.rstrip("/0123456789") + "/1"

celery = Celery(
    "astock",
    broker=_redis_url,
    backend=_celery_backend,
    include=["app.tasks"],
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    beat_schedule={
        # Fetch all active stocks every 30 minutes during trading hours (9:30-15:00 CST)
        "fetch-realtime-trading-hours": {
            "task": "app.tasks.fetch_all_stocks_task",
            "schedule": crontab(minute="*/30", hour="1-7", day_of_week="1-5"),  # UTC hours for CST 9:30-15:00
            "args": ("realtime",),
        },
        # Scheduler check — runs every 10 minutes on weekdays.
        # Reads the configured update time from DB (default 16:00 Beijing)
        # and dispatches daily_update_task when due.
        "check-scheduled-update": {
            "task": "app.tasks.check_scheduled_update",
            "schedule": crontab(minute="*/10", day_of_week="1-5"),
        },
        # Trade condition monitor — runs every 5 seconds during trading hours
        # CST 9:25-11:35, 12:55-15:05 → UTC 1:25-3:35, 4:55-7:05
        "trade-monitor": {
            "task": "app.tasks.trade_monitor_tick",
            "schedule": 5.0,  # every 5 seconds
        },
        # Quant auto-iteration check — daily at 17:00 CST (09:00 UTC) on weekdays
        "check-quant-iterations": {
            "task": "app.tasks.check_quant_iterations",
            "schedule": crontab(minute=0, hour=9, day_of_week="1-5"),  # 17:00 CST
        },
    },
)
