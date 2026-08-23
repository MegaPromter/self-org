"""Админка — интерфейс заведения данных в первой версии.

Показания и выполнения заполняются прямо на странице счётчика
и обязательства; фактура — на странице любой сущности.
"""
from django.conf import settings
from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.utils.html import format_html

from .bot_actions import make_link_code
from .models import (
    Category,
    Completion,
    Fact,
    Item,
    Meter,
    MeterReading,
    Notification,
    NotificationProfile,
    Obligation,
    Person,
)
from .status import compute_status, short_number

admin.site.site_header = "Self-org"
admin.site.site_title = "Self-org"
admin.site.index_title = "Справочники"


class FactInline(GenericTabularInline):
    model = Fact
    extra = 0


class MeterInline(admin.TabularInline):
    model = Meter
    extra = 0
    show_change_link = True  # показания вводятся на странице счётчика


class MeterReadingInline(admin.TabularInline):
    model = MeterReading
    extra = 1


class CompletionInline(admin.TabularInline):
    model = Completion
    extra = 0


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "order", "icon"]
    list_editable = ["order", "icon"]  # порядок плашек и значок


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["name", "category"]
    list_filter = ["category"]
    search_fields = ["name"]
    inlines = [MeterInline, FactInline]


@admin.register(Meter)
class MeterAdmin(admin.ModelAdmin):
    list_display = ["name", "item", "unit", "reading_reminder_days"]
    list_filter = ["item"]
    search_fields = ["name", "item__name"]
    inlines = [MeterReadingInline]


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ["name", "birth_date", "user"]
    search_fields = ["name"]
    inlines = [FactInline]


@admin.register(Obligation)
class ObligationAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "state",
        "left",
        "category_shown",
        "rule_kind",
        "item",
        "owner",
    ]
    list_filter = ["category", "rule_kind", "is_private", "owner"]
    search_fields = ["name", "item__name", "person__name"]
    inlines = [CompletionInline, FactInline]
    fieldsets = [
        (None, {"fields": ["name", "category", "item", "person", "notes"]}),
        (
            "Правило срока",
            {
                "description": (
                    "Заполняются только части выбранного вида: "
                    "по времени, по счётчику или обе сразу."
                ),
                "fields": [
                    "rule_kind",
                    "time_kind",
                    "due_date",
                    ("interval_value", "interval_unit"),
                    ("annual_month", "annual_day"),
                    ("meter", "meter_interval"),
                ],
            },
        ),
        (
            "Напоминания",
            {
                "fields": [
                    "soon_threshold_percent",
                    "overdue_repeat_days",
                    "snoozed_until",
                ]
            },
        ),
        ("Семья", {"fields": ["owner", "assignee", "is_private"]}),
    ]

    @admin.display(description="раздел")
    def category_shown(self, obj):
        """Раздел с учётом наследования от предмета."""
        return obj.effective_category or "—"

    @admin.display(description="состояние")
    def state(self, obj):
        статус = compute_status(obj)
        текст = str(статус.state)
        if статус.is_estimate:
            текст += " (по оценке)"
        if статус.message:
            текст += f" — {статус.message}"
        return текст

    @admin.display(description="осталось")
    def left(self, obj):
        статус = compute_status(obj)
        части = []
        if статус.meter_left is not None:
            префикс = "≈ " if статус.is_estimate else ""
            части.append(
                f"{префикс}{short_number(статус.meter_left)}"
                f" {статус.meter_unit}"
            )
        if статус.days_left is not None:
            части.append(f"{статус.days_left} дн.")
        return " · ".join(части) or "—"


@admin.register(NotificationProfile)
class NotificationProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "telegram_chat_id",
        "quiet_hours_start",
        "quiet_hours_end",
    ]
    readonly_fields = ["подключение"]
    fields = [
        "user",
        "подключение",
        "telegram_chat_id",
        "quiet_hours_start",
        "quiet_hours_end",
    ]

    @admin.display(description="подключение Telegram")
    def подключение(self, obj):
        """Ссылка привязки чата — или подсказка, если бот не назван."""
        if obj is None or obj.pk is None:
            return "Сохраните настройки — появится ссылка привязки."
        if obj.telegram_chat_id:
            return (
                "Подключено. Чтобы отвязать — очистите поле "
                "«Telegram chat id» ниже и сохраните."
            )
        код = make_link_code(obj.user_id)
        if settings.TELEGRAM_BOT_USERNAME:
            return format_html(
                '<a href="https://t.me/{}?start={}" target="_blank">'
                "Подключить Telegram</a> — ссылка действует 7 дней.",
                settings.TELEGRAM_BOT_USERNAME,
                код,
            )
        return format_html(
            "Отправьте боту сообщение: <code>/start {}</code> "
            "(код действует 7 дней).",
            код,
        )


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """Журнал уведомлений: записи создаёт планировщик, не человек."""

    list_display = [
        "created_at",
        "user",
        "kind",
        "text",
        "status",
        "sent_at",
    ]
    list_filter = ["kind", "status", "user"]
    search_fields = ["text"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False  # журнал пишет планировщик — руками не добавляют
