"""Тесты расчёта сроков и состояний — пункты «Как проверим»
заметки «Расчёт сроков и состояний»: оценка пробега по темпу,
«скоро» у масла и у ежегодной даты, просрочка разового,
флаг «пора ввести показания», колонки админки.
"""
import datetime
from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.auth.models import User

from core.models import Completion, Item, Meter, MeterReading, Obligation
from core.status import State, compute_status, estimate_meter, needs_reading

# Дата настоящая, а не выдуманная: колонки админки считают
# состояние от сегодняшнего дня и с фиксированной датой разошлись
# бы через несколько суток после написания теста.
СЕГОДНЯ = datetime.date.today()


def дней_назад(n):
    return СЕГОДНЯ - datetime.timedelta(days=n)


@pytest.fixture
def антон(db):
    return User.objects.create_user("anton")


@pytest.fixture
def пробег(db):
    авто = Item.objects.create(name="Автомобиль")
    return Meter.objects.create(item=авто, name="Пробег", unit="км")


@pytest.fixture
def пробег_с_историей(пробег):
    """110 000 шестьдесят дней назад и 118 000 десять дней назад —
    темп 160 км/день."""
    MeterReading.objects.create(
        meter=пробег, value=Decimal(110_000), date=дней_назад(60)
    )
    MeterReading.objects.create(
        meter=пробег, value=Decimal(118_000), date=дней_назад(10)
    )
    return пробег


@pytest.fixture
def масло(пробег_с_историей, антон):
    """«Масло ДВС»: 8 000 км или 12 месяцев, замена при 112 000."""
    обязательство = Obligation.objects.create(
        name="Масло ДВС",
        item=пробег_с_историей.item,
        rule_kind=Obligation.RuleKind.BOTH,
        time_kind=Obligation.TimeKind.INTERVAL,
        interval_value=12,
        interval_unit=Obligation.IntervalUnit.MONTHS,
        meter=пробег_с_историей,
        meter_interval=Decimal(8000),
        owner=антон,
    )
    Completion.objects.create(
        obligation=обязательство,
        date=дней_назад(40),
        meter_value=Decimal(112_000),
    )
    return обязательство


def test_ocenka_probega_po_tempu(пробег_с_историей):
    оценка = estimate_meter(пробег_с_историей, СЕГОДНЯ)
    assert оценка.value == Decimal(119_600)
    assert оценка.is_estimate
    assert not оценка.insufficient_data


def test_maslo_skoro_po_ocenke(масло):
    статус = compute_status(масло, СЕГОДНЯ)
    assert статус.state is State.SOON
    assert статус.meter_left == Decimal(400)
    assert статус.due_meter_value == Decimal(120_000)
    assert статус.is_estimate


def test_ezhegodnoe_skoro(антон):
    день_рождения = СЕГОДНЯ + datetime.timedelta(days=26)
    сестра = Obligation.objects.create(
        name="Поздравить сестру",
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.ANNUAL,
        annual_month=день_рождения.month,
        annual_day=день_рождения.day,
        owner=антон,
        created=СЕГОДНЯ,
    )
    статус = compute_status(сестра, СЕГОДНЯ)
    assert статус.state is State.SOON  # пройдено ~93 % года
    assert статус.due_date == день_рождения
    assert статус.days_left == 26


def test_razovoe_prosrocheno_i_zakryto(антон):
    страховка = Obligation.objects.create(
        name="Оплатить страховку",
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.ONCE,
        due_date=дней_назад(5),
        owner=антон,
        created=дней_назад(30),
    )
    assert compute_status(страховка, СЕГОДНЯ).state is State.OVERDUE

    Completion.objects.create(obligation=страховка, date=дней_назад(1))
    assert compute_status(страховка, СЕГОДНЯ).state is State.CLOSED


def test_interval_bez_vypolneniya_net_dannyh(антон):
    фильтр = Obligation.objects.create(
        name="Фильтр воды",
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.INTERVAL,
        interval_value=6,
        interval_unit=Obligation.IntervalUnit.MONTHS,
        owner=антон,
    )
    статус = compute_status(фильтр, СЕГОДНЯ)
    assert статус.state is State.NO_DATA
    assert "отметьте" in статус.message


def test_pora_vvesti_pokazaniya(пробег):
    MeterReading.objects.create(
        meter=пробег, value=Decimal(100_000), date=дней_назад(120)
    )
    assert needs_reading(пробег, СЕГОДНЯ)  # старше 90 дней

    MeterReading.objects.create(
        meter=пробег, value=Decimal(101_000), date=дней_назад(10)
    )
    assert not needs_reading(пробег, СЕГОДНЯ)


def test_odno_pokazanie_bez_rashoda_malo_dannyh(пробег):
    MeterReading.objects.create(
        meter=пробег, value=Decimal(100_000), date=дней_назад(30)
    )
    оценка = estimate_meter(пробег, СЕГОДНЯ)
    assert оценка.value == Decimal(100_000)  # не гадаем
    assert оценка.insufficient_data


def test_odno_pokazanie_s_godovym_rashodom(пробег):
    пробег.expected_yearly_usage = Decimal(14_600)  # 40 км/день
    пробег.save()
    MeterReading.objects.create(
        meter=пробег, value=Decimal(100_000), date=дней_назад(30)
    )
    оценка = estimate_meter(пробег, СЕГОДНЯ)
    assert оценка.value == Decimal(101_200)
    assert оценка.is_estimate


def test_kolonki_adminki(масло):
    админка = admin.site._registry[Obligation]
    assert "скоро" in админка.state(масло)
    осталось = админка.left(масло)
    assert "400 км" in осталось
    assert "дн." in осталось
