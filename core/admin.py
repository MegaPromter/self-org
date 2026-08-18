"""Админка — интерфейс заведения данных в первой версии.

Показания и выполнения заполняются прямо на странице счётчика
и обязательства; фактура — на странице любой сущности.
"""
from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline

from .models import (
    Completion,
    Fact,
    Item,
    Meter,
    MeterReading,
    NotificationProfile,
    Obligation,
    Person,
)

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


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["name"]
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
    list_display = ["name", "rule_kind", "item", "person", "owner"]
    list_filter = ["rule_kind", "is_private", "owner"]
    search_fields = ["name", "item__name", "person__name"]
    inlines = [CompletionInline, FactInline]
    fieldsets = [
        (None, {"fields": ["name", "item", "person", "notes"]}),
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
            {"fields": ["soon_threshold_percent", "overdue_repeat_days"]},
        ),
        ("Семья", {"fields": ["owner", "assignee", "is_private"]}),
    ]


@admin.register(NotificationProfile)
class NotificationProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "telegram_chat_id",
        "quiet_hours_start",
        "quiet_hours_end",
    ]
