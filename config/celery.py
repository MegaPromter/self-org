"""Celery — планировщик фоновых задач (напоминания по расписанию).

В простом запуске (без Docker) не используется: сайт и тесты
работают без него. В полном запуске воркер и beat стартуют
отдельными контейнерами.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("selforg")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
