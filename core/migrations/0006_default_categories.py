"""Стартовый набор разделов — заметка «Главный экран», правило 3.

Пустой справочник означал бы пустые плашки на главном экране,
поэтому набор заводится сразу. Пользователь переименовывает
и дополняет его в админке; при откате миграции удаляются только
разделы, которых никто не занял.
"""
from django.db import migrations

РАЗДЕЛЫ = [
    (10, "Транспорт"),
    (20, "Дом"),
    (30, "Хозяйство"),
    (40, "Семья"),
    (50, "Финансы и документы"),
    (60, "Здоровье"),
    (70, "Общественное"),
]


def завести(apps, schema_editor):
    Category = apps.get_model("core", "Category")
    for порядок, название in РАЗДЕЛЫ:
        Category.objects.get_or_create(
            name=название, defaults={"order": порядок}
        )


def убрать(apps, schema_editor):
    Category = apps.get_model("core", "Category")
    Category.objects.filter(
        name__in=[название for _, название in РАЗДЕЛЫ],
        items__isnull=True,
        obligations__isnull=True,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_category"),
    ]

    operations = [migrations.RunPython(завести, убрать)]
