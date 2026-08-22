from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Учёт обязательств"

    def ready(self):
        # Telegram втыкается в разъём планировщика: дальше тот сам
        # решает, что и когда отправлять (заметка «Telegram-бот»).
        from . import planner, telegram

        planner.register_channel(telegram.send)
