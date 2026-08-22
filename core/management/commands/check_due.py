"""Ручной прогон планировщика — для простого запуска без Docker.

`python manage.py check_due` — то же, что Celery Beat делает
по расписанию, но один раз и сейчас.
"""
from django.core.management.base import BaseCommand

from core import planner


class Command(BaseCommand):
    help = (
        "Один прогон планировщика: проверить сроки, "
        "отправить готовые уведомления."
    )

    def handle(self, *args, **options):
        итог = planner.run()
        self.stdout.write(
            f"создано уведомлений: {итог.created}, "
            f"отправлено: {итог.sent}, "
            f"устарело: {итог.stale}"
        )
