"""Запуск Telegram-бота — отдельный процесс (правило 10 заметки).

`python manage.py run_bot` — бот слушает Telegram и обрабатывает
кнопки, пока команда не остановлена (Ctrl+C).
"""
from django.core.management.base import BaseCommand, CommandError

from core import telegram


class Command(BaseCommand):
    help = "Запустить Telegram-бота (опрос Telegram, до остановки)."

    def handle(self, *args, **options):
        self.stdout.write("Бот запускается. Остановить — Ctrl+C.")
        try:
            telegram.run_bot()
        except RuntimeError as ошибка:
            raise CommandError(str(ошибка))
