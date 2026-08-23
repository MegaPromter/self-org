"""Тесты заведения и правки — пункты «Как проверим» заметки
«Заведение данных без админки»: новое дело с созданием предмета
и счётчика, отказ без правила срока, правка, удаление, отметка
задним числом.

Даты считаются от сегодняшнего дня.
"""
import datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import (
    Category,
    Completion,
    Item,
    Meter,
    MeterReading,
    Obligation,
)
from core.status import State, compute_status

СЕГОДНЯ = timezone.localdate()


def дней_назад(n):
    return СЕГОДНЯ - datetime.timedelta(days=n)


@pytest.fixture
def хозяин(db):
    return User.objects.create_user("хозяин", password="секрет")


@pytest.fixture
def вошедший(client, хозяин):
    client.force_login(хозяин)
    return client


@pytest.mark.django_db
def test_novoe_delo_sozdayot_predmet_schetchik_i_schitaet_srok(
    вошедший, хозяин
):
    транспорт = Category.objects.get(name="Транспорт")
    ответ = вошедший.post(
        "/new/",
        {
            "name": "Замена масла",
            "category": транспорт.pk,
            "новый_предмет": "Kia Rio",
            "rule_kind": Obligation.RuleKind.METER,
            "новый_счётчик": "Пробег",
            "единица_счётчика": "км",
            "meter_interval": "10000",
            "последний_раз": дней_назад(100).isoformat(),
            "показание_тогда": "182000",
            "soon_threshold_percent": "70",
            "overdue_repeat_days": "7",
        },
    )
    assert ответ.status_code == 302

    дело = Obligation.objects.get(name="Замена масла")
    assert дело.owner == хозяин
    assert дело.item.name == "Kia Rio"
    assert дело.item.category == транспорт  # раздел достался предмету
    assert дело.meter.name == "Пробег" and дело.meter.unit == "км"
    assert дело.completions.count() == 1  # «когда делали последний раз»
    assert MeterReading.objects.filter(meter=дело.meter).count() == 1

    статус = compute_status(дело, СЕГОДНЯ)
    assert статус.state is not State.NO_DATA  # срок посчитан, а не «нет данных»
    assert статус.due_meter_value == Decimal("192000.00")


@pytest.mark.django_db
def test_bez_pravila_sroka_forma_obyasnyaet_i_nichego_ne_sozdayot(вошедший):
    ответ = вошедший.post(
        "/new/",
        {
            "name": "Что-то важное",
            "новый_предмет": "Котёл",
            "soon_threshold_percent": "70",
            "overdue_repeat_days": "7",
        },
    )
    текст = ответ.content.decode()
    assert ответ.status_code == 200  # остались на форме
    assert "Выберите, когда напоминать" in текст
    assert "Что-то важное" in текст  # введённое не потерялось
    assert not Obligation.objects.exists()
    assert not Item.objects.filter(name="Котёл").exists()  # мусора нет


@pytest.mark.django_db
def test_ezhegodnoe_delo_iz_dnya_i_mesyaca(вошедший):
    вошедший.post(
        "/new/",
        {
            "name": "Поздравить сестру",
            "rule_kind": Obligation.RuleKind.TIME,
            "time_kind": Obligation.TimeKind.ANNUAL,
            "годовщина": "14.09",
            "soon_threshold_percent": "70",
            "overdue_repeat_days": "7",
        },
    )
    дело = Obligation.objects.get(name="Поздравить сестру")
    assert (дело.annual_day, дело.annual_month) == (14, 9)


@pytest.mark.django_db
def test_pravka_menyaet_interval_i_ostavlyaet_istoriyu(вошедший, хозяин):
    предмет = Item.objects.create(name="Kia")
    счётчик = Meter.objects.create(item=предмет, name="Пробег", unit="км")
    дело = Obligation.objects.create(
        name="Масло",
        item=предмет,
        rule_kind=Obligation.RuleKind.METER,
        meter=счётчик,
        meter_interval=Decimal("10000"),
        owner=хозяин,
    )
    Completion.objects.create(
        obligation=дело, date=дней_назад(30), meter_value=Decimal("100000")
    )

    ответ = вошедший.post(
        f"/obligation/{дело.pk}/edit/",
        {
            "name": "Масло ДВС",
            "rule_kind": Obligation.RuleKind.METER,
            "item": предмет.pk,
            "meter": счётчик.pk,
            "meter_interval": "8000",
            "soon_threshold_percent": "70",
            "overdue_repeat_days": "7",
        },
    )
    assert ответ.status_code == 302
    дело.refresh_from_db()
    assert дело.name == "Масло ДВС"
    assert дело.meter_interval == Decimal("8000")
    assert дело.completions.count() == 1  # история на месте


@pytest.mark.django_db
def test_udalenie_dela_ne_trogaet_predmet_i_schetchik(вошедший, хозяин):
    предмет = Item.objects.create(name="Квартира")
    счётчик = Meter.objects.create(item=предмет, name="Вода", unit="м³")
    дело = Obligation.objects.create(
        name="Подать показания",
        item=предмет,
        rule_kind=Obligation.RuleKind.METER,
        meter=счётчик,
        meter_interval=Decimal("10"),
        owner=хозяин,
    )
    Completion.objects.create(obligation=дело, date=дней_назад(5))

    ответ = вошедший.post(f"/obligation/{дело.pk}/delete/")
    assert ответ.status_code == 302
    assert not Obligation.objects.filter(pk=дело.pk).exists()
    assert not Completion.objects.filter(obligation_id=дело.pk).exists()
    assert Item.objects.filter(pk=предмет.pk).exists()
    assert Meter.objects.filter(pk=счётчик.pk).exists()


@pytest.mark.django_db
def test_otmetka_zadnim_chislom_i_udalenie_otmetki(вошедший, хозяин):
    предмет = Item.objects.create(name="Квартира")
    счётчик = Meter.objects.create(item=предмет, name="Вода", unit="м³")
    дело = Obligation.objects.create(
        name="Промывка котла",
        item=предмет,
        rule_kind=Obligation.RuleKind.METER,
        meter=счётчик,
        meter_interval=Decimal("50"),
        owner=хозяин,
    )
    assert compute_status(дело, СЕГОДНЯ).state is State.NO_DATA

    MeterReading.objects.create(
        meter=счётчик, value=Decimal("100"), date=дней_назад(50)
    )
    вошедший.post(
        f"/obligation/{дело.pk}/completion/",
        {
            "в-date": дней_назад(40).isoformat(),
            "в-meter_value": "110",
            "в-cost": "3500",
            "в-note": "делал сам",
        },
    )
    выполнение = дело.completions.get()
    assert выполнение.date == дней_назад(40)
    assert выполнение.cost == Decimal("3500")
    assert выполнение.done_by == хозяин
    assert compute_status(дело, СЕГОДНЯ).state is State.OK

    вошедший.post(f"/completion/{выполнение.pk}/delete/")
    assert not дело.completions.exists()
    assert compute_status(дело, СЕГОДНЯ).state is State.NO_DATA


@pytest.mark.django_db
def test_chuzhoe_lichnoe_delo_ne_otkryvaetsya(вошедший, db):
    сосед = User.objects.create_user("сосед")
    чужое = Obligation.objects.create(
        name="Личное соседа",
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.ONCE,
        due_date=дней_назад(1),
        owner=сосед,
        is_private=True,
    )
    assert вошедший.get(f"/obligation/{чужое.pk}/").status_code == 404
    assert вошедший.get(f"/obligation/{чужое.pk}/edit/").status_code == 404
    assert вошедший.post(f"/obligation/{чужое.pk}/delete/").status_code == 404


@pytest.mark.django_db
def test_forma_otkryvaetsya_s_podstavlennym_razdelom(вошедший):
    дом = Category.objects.get(name="Дом")
    текст = вошедший.get(f"/new/?раздел={дом.pk}").content.decode()
    assert "Новое дело" in текст
    assert f'value="{дом.pk}" selected' in текст
