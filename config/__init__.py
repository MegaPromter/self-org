# Подключение Celery при старте Django — стандартный приём,
# чтобы задачи регистрировались автоматически.
from .celery import app as celery_app

__all__ = ("celery_app",)
