"""Задачи Celery — тонкие обёртки над `core/planner.py`.

Расписание прогонов — CELERY_BEAT_SCHEDULE в config/settings.py.
"""
from celery import shared_task

from . import planner


@shared_task
def check_due():
    """Регулярный прогон планировщика (раз в 4 часа)."""
    итог = planner.run()
    return {"создано": итог.created, "отправлено": итог.sent}
