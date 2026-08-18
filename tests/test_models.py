"""Тесты модели данных — пункты «Как проверим» заметки
«Модель данных»: автомобиль со счётчиком, правило «оба сразу»,
история выполнений, фактура, ежегодная дата без предмета.
"""
import datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from core.models import (
    Completion,
    Fact,
    Item,
    Meter,
    MeterReading,
    NotificationProfile,
    Obligation,
    Person,
)


@pytest.fixture
def антон(db):
    return User.objects.create_user("anton")


@pytest.fixture
def автомобиль(db):
    return Item.objects.create(name="Автомобиль")


@pytest.fixture
def пробег(автомобиль):
    return Meter.objects.create(item=автомобиль, name="Пробег", unit="км")


@pytest.fixture
def масло(автомобиль, пробег, антон):
    """«Масло ДВС»: 8000 км или 12 месяцев — что раньше."""
    обязательство = Obligation(
        name="Масло ДВС",
        item=автомобиль,
        rule_kind=Obligation.RuleKind.BOTH,
        time_kind=Obligation.TimeKind.INTERVAL,
        interval_value=12,
        interval_unit=Obligation.IntervalUnit.MONTHS,
        meter=пробег,
        meter_interval=Decimal(8000),
        owner=антон,
    )
    обязательство.full_clean()
    обязательство.save()
    return обязательство


def test_predmet_schetchik_pokazanie(пробег):
    показание = MeterReading.objects.create(
        meter=пробег, value=Decimal(118_000)
    )
    assert показание.date == datetime.date.today()
    assert "км" in str(показание)
    assert list(пробег.readings.all()) == [показание]


def test_pravilo_oba_srazu(масло):
    # умолчания напоминаний — из согласованной заметки
    assert масло.soon_threshold_percent == 70
    assert масло.overdue_repeat_days == 7
    assert масло.meter.reading_reminder_days == 90


def test_pravilo_bez_intervala_ne_prohodit(автомобиль, пробег, антон):
    обязательство = Obligation(
        name="Масло ДВС",
        item=автомобиль,
        rule_kind=Obligation.RuleKind.BOTH,
        time_kind=Obligation.TimeKind.INTERVAL,
        interval_value=12,
        interval_unit=Obligation.IntervalUnit.MONTHS,
        meter=пробег,
        owner=антон,
    )
    with pytest.raises(ValidationError) as ошибка:
        обязательство.full_clean()
    assert "meter_interval" in ошибка.value.error_dict


def test_schetchik_chuzhogo_predmeta_ne_prohodit(пробег, антон):
    чужой = Item.objects.create(name="Котёл")
    обязательство = Obligation(
        name="Чистка",
        item=чужой,
        rule_kind=Obligation.RuleKind.METER,
        meter=пробег,
        meter_interval=Decimal(100),
        owner=антон,
    )
    with pytest.raises(ValidationError) as ошибка:
        обязательство.full_clean()
    assert "meter" in ошибка.value.error_dict


def test_vypolnenie_v_istorii(масло, антон):
    выполнение = Completion.objects.create(
        obligation=масло,
        date=datetime.date(2026, 5, 10),
        meter_value=Decimal(112_000),
        cost=Decimal(4500),
        done_by=антон,
    )
    assert list(масло.completions.all()) == [выполнение]


def test_faktura_k_obyazatelstvu(масло):
    строка = масло.facts.create(name="артикул", value="152089E42A")
    assert строка in масло.facts.all()
    assert str(строка) == "артикул: 152089E42A"


def test_ezhegodnoe_bez_predmeta(db, антон):
    сестра = Person.objects.create(
        name="Сестра", birth_date=datetime.date(1990, 9, 14)
    )
    поздравить = Obligation(
        name="Поздравить сестру",
        person=сестра,
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.ANNUAL,
        annual_month=9,
        annual_day=14,
        owner=антон,
    )
    поздравить.full_clean()
    поздравить.save()
    assert поздравить.item is None and поздравить.meter is None


def test_nesushchestvuyushchaya_data_ne_prohodit(db, антон):
    обязательство = Obligation(
        name="Поздравить",
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.ANNUAL,
        annual_month=2,
        annual_day=30,
        owner=антон,
    )
    with pytest.raises(ValidationError) as ошибка:
        обязательство.full_clean()
    assert "annual_day" in ошибка.value.error_dict


def test_nastroyki_uvedomleniy(антон):
    настройки = NotificationProfile.objects.create(user=антон)
    assert настройки.quiet_hours_start == datetime.time(22, 0)
    assert настройки.quiet_hours_end == datetime.time(9, 0)


def test_admin_spiski_otkryvayutsya(db, client):
    """Списки всех сущностей в админке отвечают — интерфейс v1."""
    админ = User.objects.create_superuser("admin-test")
    client.force_login(админ)
    for адрес in (
        "/admin/core/item/",
        "/admin/core/meter/",
        "/admin/core/person/",
        "/admin/core/obligation/",
        "/admin/core/notificationprofile/",
        "/admin/core/obligation/add/",
    ):
        assert client.get(адрес).status_code == 200, адрес
