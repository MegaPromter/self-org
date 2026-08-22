"""Действия над данными — общие для главного экрана и бота.

Кнопки на экране и кнопки в Telegram делают одно и то же:
отмечают выполнение, откладывают, записывают показание. Правила
записи живут здесь, чтобы две кнопки не разъехались в поведении.
Телеграмная обвязка — в `core/telegram.py`, разбор нажатий —
в `core/bot_actions.py`, экран — в `core/views.py`.
"""
import datetime
import re
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from .models import Completion, Meter, MeterReading, Obligation
from .status import estimate_meter


# --- Кто что видит ---------------------------------------------------------


def visible_obligations(user):
    """Обязательства, доступные человеку: свои плюс общие семейные.

    Личное чужое не показывается и не меняется (правило 2 заметки
    «Главный экран»).
    """
    return Obligation.objects.filter(
        Q(is_private=False) | Q(owner=user) | Q(assignee=user)
    )


def obligation_for(user, obligation_id):
    """Обязательство, если человеку до него есть дело; иначе None."""
    return (
        visible_obligations(user)
        .filter(pk=obligation_id)
        .select_related("meter")
        .first()
    )


def visible_meters(user):
    """Счётчики, на которые смотрят доступные человеку обязательства."""
    return (
        Meter.objects.filter(obligations__in=visible_obligations(user))
        .select_related("item")
        .distinct()
    )


def meter_for(user, meter_id):
    """Счётчик, если человеку до него есть дело; иначе None."""
    return visible_meters(user).filter(pk=meter_id).first()


# --- Записи ----------------------------------------------------------------


def complete(obligation, user, today=None) -> Completion:
    """Отметить выполнение сегодняшней датой.

    Показание счётчика — расчётное на сегодня: пользователь
    не обязан лезть за точным. Отметили — начался новый цикл,
    отложка больше не нужна.
    """
    today = today or timezone.localdate()
    показание = None
    if obligation.meter:
        оценка = estimate_meter(obligation.meter, today)
        показание = оценка.value if оценка else None
    выполнение = Completion.objects.create(
        obligation=obligation,
        date=today,
        meter_value=показание,
        done_by=user,
    )
    if obligation.snoozed_until:
        obligation.snoozed_until = None
        obligation.save(update_fields=["snoozed_until"])
    return выполнение


def snooze(obligation, days, today=None) -> datetime.date:
    """Молчать об обязательстве выбранное число дней."""
    today = today or timezone.localdate()
    obligation.snoozed_until = today + datetime.timedelta(days=days)
    obligation.save(update_fields=["snoozed_until"])
    return obligation.snoozed_until


def unsnooze(obligation) -> None:
    """Вернуть отложенное в обычный порядок напоминаний."""
    obligation.snoozed_until = None
    obligation.save(update_fields=["snoozed_until"])


def set_cost(completion, value) -> None:
    """Дописать стоимость к уже записанному выполнению."""
    completion.cost = value
    completion.save(update_fields=["cost"])


def add_reading(meter, value, today=None):
    """Записать показание; вернуть (запись, предыдущее показание).

    Предыдущее возвращается только если оно больше нового:
    счётчик, «поехавший назад», — обычно опечатка, и об этом
    стоит предупредить.
    """
    today = today or timezone.localdate()
    предыдущее = meter.readings.order_by("-date", "-id").first()
    запись = MeterReading.objects.create(
        meter=meter, value=value, date=today
    )
    подозрительное = (
        предыдущее if предыдущее and value < предыдущее.value else None
    )
    return запись, подозрительное


def parse_number(текст) -> Decimal | None:
    """Число из ответа: «118 500», «3500,50», «3 500.5» — всё годится."""
    очищено = re.sub(r"[\s ]", "", текст or "").replace(",", ".")
    try:
        значение = Decimal(очищено)
    except (InvalidOperation, ValueError):
        return None
    return значение if значение >= 0 else None
