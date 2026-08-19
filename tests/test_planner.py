"""Тесты планировщика — пункты «Как проверим» заметки
«Планировщик»: уведомление о «скоро» по оценке, отсутствие дублей,
повтор о просроченном, тихие часы, напоминание о показаниях,
новый цикл после выполнения, журнал в админке.
"""
import datetime
from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.utils import timezone

from core import planner
from core.models import (
    Completion,
    Item,
    Meter,
    MeterReading,
    Notification,
    Obligation,
)

СЕГОДНЯ = datetime.date(2026, 8, 19)


def дней_назад(n):
    return СЕГОДНЯ - datetime.timedelta(days=n)


def момент(день=СЕГОДНЯ, час=12, минута=0):
    """Aware-время местного пояса — «сейчас» для прогона."""
    return timezone.make_aware(
        datetime.datetime.combine(день, datetime.time(час, минута))
    )


@pytest.fixture
def антон(db):
    return User.objects.create_user("anton")


@pytest.fixture
def канал():
    """Фейковый канал отправки: принимает всё, запоминает."""
    принятые = []
    planner.register_channel(
        lambda уведомление: принятые.append(уведомление) or True
    )
    yield принятые
    planner._channels.clear()


@pytest.fixture
def масло(антон):
    """«Масло ДВС» в состоянии «скоро» по оценке пробега.

    Темп 160 км/день, оценка 119 600, замена при 112 000 —
    пройдено 95 % интервала 8 000 км.
    """
    авто = Item.objects.create(name="Автомобиль")
    пробег = Meter.objects.create(item=авто, name="Пробег", unit="км")
    MeterReading.objects.create(
        meter=пробег, value=Decimal(110_000), date=дней_назад(60)
    )
    MeterReading.objects.create(
        meter=пробег, value=Decimal(118_000), date=дней_назад(10)
    )
    обязательство = Obligation.objects.create(
        name="Масло ДВС",
        created=дней_назад(60),
        item=авто,
        rule_kind=Obligation.RuleKind.BOTH,
        time_kind=Obligation.TimeKind.INTERVAL,
        interval_value=12,
        interval_unit=Obligation.IntervalUnit.MONTHS,
        meter=пробег,
        meter_interval=Decimal(8000),
        owner=антон,
    )
    Completion.objects.create(
        obligation=обязательство,
        date=дней_назад(47),
        meter_value=Decimal(112_000),
    )
    return обязательство


@pytest.fixture
def страховка(антон):
    """Разовое «Оплатить страховку» к прошедшей дате — просрочено."""
    return Obligation.objects.create(
        name="Оплатить страховку",
        created=дней_назад(30),
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.ONCE,
        due_date=дней_назад(10),
        owner=антон,
    )


def test_скоро_по_оценке_попадает_в_журнал(масло, антон):
    итог = planner.run(момент())

    assert итог.created == 1
    уведомление = Notification.objects.get()
    assert уведомление.user == антон
    assert уведомление.kind == Notification.Kind.SOON
    assert "по расчёту подходит срок — проверьте пробег" in уведомление.text
    assert уведомление.status == Notification.Status.PENDING


def test_дубля_нет_при_следующем_прогоне(масло):
    planner.run(момент(час=13))
    итог = planner.run(момент(час=17))

    assert итог.created == 0
    assert Notification.objects.count() == 1


def test_повтор_о_просроченном_не_чаще_недели(страховка):
    planner.run(момент(день=дней_назад(3)))
    итог = planner.run(момент())
    assert итог.created == 0  # напоминали 3 дня назад — рано

    Notification.objects.update(created_at=момент(день=дней_назад(8)))
    итог = planner.run(момент())
    assert итог.created == 1  # напоминали 8 дней назад — пора снова


def test_тихие_часы_держат_отправку_до_утра(масло, канал):
    итог = planner.run(момент(час=23))

    assert итог.created == 1
    assert итог.sent == 0
    уведомление = Notification.objects.get()
    assert timezone.localtime(уведомление.not_before).hour == 9

    утро = момент(день=СЕГОДНЯ + datetime.timedelta(days=1), час=9)
    итог = planner.run(утро)

    assert итог.sent == 1
    assert канал == [уведомление]
    уведомление.refresh_from_db()
    assert уведомление.status == Notification.Status.SENT


def test_напоминание_ввести_показания(антон):
    """Показаниям больше 90 дней — «введите показания» получателю
    обязательства счётчика; повтор не чаще раза в неделю."""
    авто = Item.objects.create(name="Автомобиль")
    пробег = Meter.objects.create(item=авто, name="Пробег", unit="км")
    MeterReading.objects.create(
        meter=пробег, value=Decimal(110_000), date=дней_назад(100)
    )
    Obligation.objects.create(
        name="Масло ДВС",
        created=дней_назад(100),
        item=авто,
        rule_kind=Obligation.RuleKind.METER,
        meter=пробег,
        meter_interval=Decimal(8000),
        owner=антон,
    )

    итог = planner.run(момент())

    assert итог.created == 1
    уведомление = Notification.objects.get()
    assert уведомление.kind == Notification.Kind.READING
    assert уведомление.meter == пробег
    assert "введите показания" in уведомление.text

    итог = planner.run(момент(час=16))
    assert итог.created == 0


def test_счётчик_без_обязательств_не_напоминает(антон):
    авто = Item.objects.create(name="Автомобиль")
    Meter.objects.create(item=авто, name="Пробег", unit="км")

    итог = planner.run(момент())

    assert итог.created == 0


def test_новый_цикл_напоминает_снова(масло):
    planner.run(момент())
    Completion.objects.create(
        obligation=масло, date=СЕГОДНЯ, meter_value=Decimal(119_600)
    )

    # сразу после замены — «в норме», нового уведомления нет
    итог = planner.run(момент(час=16))
    assert итог.created == 0

    # через 45 дней оценка снова у порога — напоминание приходит
    позже = СЕГОДНЯ + datetime.timedelta(days=45)
    итог = planner.run(момент(день=позже))
    assert итог.created == 1
    assert (
        Notification.objects.filter(kind=Notification.Kind.SOON).count() == 2
    )


def test_получает_исполнитель_если_назначен(масло):
    маша = User.objects.create_user("masha")
    масло.assignee = маша
    масло.save()

    planner.run(момент())

    assert Notification.objects.get().user == маша


def test_журнал_виден_в_админке(db):
    assert Notification in admin.site._registry
    колонки = admin.site._registry[Notification].list_display
    for поле in ("user", "text", "status", "sent_at"):
        assert поле in колонки
