"""Тесты главного экрана — пункты «Как проверим» заметки
«Главный экран»: порядок дел, плашки разделов, пометка оценки,
«Сделано» со стоимостью, «Отложить» и возврат, счётчики,
чужое личное.

Даты считаются от сегодняшнего дня: экран берёт «сегодня» сам.
"""
import datetime
from decimal import Decimal
from urllib.parse import unquote

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core import planner
from core.models import (
    Category,
    Completion,
    Item,
    Meter,
    MeterReading,
    Notification,
    Obligation,
)

СЕГОДНЯ = timezone.localdate()


def дней_назад(n):
    return СЕГОДНЯ - datetime.timedelta(days=n)


@pytest.fixture
def хозяин(db):
    return User.objects.create_user("хозяин", password="секрет")


@pytest.fixture
def сосед(db):
    return User.objects.create_user("сосед", password="секрет")


@pytest.fixture
def вошедший(client, хозяин):
    client.force_login(хозяин)
    return client


@pytest.fixture
def машина(db):
    """Kia в разделе «Транспорт» с пробегом: 160 км в день.

    Показания 110 000 (60 дней назад) и 118 000 (10 дней назад) —
    расчётная оценка на сегодня 119 600 км.
    """
    предмет = Item.objects.create(
        name="Kia", category=Category.objects.get(name="Транспорт")
    )
    счётчик = Meter.objects.create(item=предмет, name="Пробег", unit="км")
    MeterReading.objects.create(
        meter=счётчик, value=Decimal("110000"), date=дней_назад(60)
    )
    MeterReading.objects.create(
        meter=счётчик, value=Decimal("118000"), date=дней_назад(10)
    )
    return предмет, счётчик


@pytest.fixture
def дела(хозяин, машина):
    """Три дела: просроченное, скорое и спокойное — плюс без данных."""
    предмет, счётчик = машина
    жидкость = Obligation.objects.create(
        name="Тормозная жидкость",
        item=предмет,
        rule_kind=Obligation.RuleKind.METER,
        meter=счётчик,
        meter_interval=Decimal("6000"),
        owner=хозяин,
    )
    Completion.objects.create(
        obligation=жидкость,
        date=дней_назад(300),
        meter_value=Decimal("112000"),
    )

    сестра = Obligation.objects.create(
        name="Поздравить сестру Олю",
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.ANNUAL,
        annual_month=(СЕГОДНЯ + datetime.timedelta(days=23)).month,
        annual_day=(СЕГОДНЯ + datetime.timedelta(days=23)).day,
        created=дней_назад(30),
        owner=хозяин,
        category=Category.objects.get(name="Семья"),
    )

    квартира = Item.objects.create(
        name="Квартира", category=Category.objects.get(name="Дом")
    )
    фильтр = Obligation.objects.create(
        name="Фильтр воды на кухне",
        item=квартира,
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.INTERVAL,
        interval_value=12,
        interval_unit=Obligation.IntervalUnit.MONTHS,
        owner=хозяин,
    )
    Completion.objects.create(obligation=фильтр, date=дней_назад(60))

    котёл = Obligation.objects.create(
        name="Промывка котла",
        item=квартира,
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.INTERVAL,
        interval_value=12,
        interval_unit=Obligation.IntervalUnit.MONTHS,
        owner=хозяин,
    )
    return жидкость, сестра, фильтр, котёл


def порядок(текст, *имена):
    """Позиции названий в теле страницы — для проверки очерёдности."""
    места = [текст.find(имя) for имя in имена]
    assert all(место >= 0 for место in места), места
    return места


@pytest.mark.django_db
def test_dela_idut_ot_samogo_goryashchego(вошедший, дела):
    жидкость, сестра, фильтр, котёл = дела
    текст = вошедший.get("/").content.decode()
    места = порядок(
        текст,
        "Тормозная жидкость",
        "Поздравить сестру Олю",
        "Фильтр воды на кухне",
        "Промывка котла",
    )
    assert места == sorted(места)
    assert "просрочено" in текст and "скоро" in текст and "в норме" in текст
    # Полоска пройденной доли интервала — с процентом в ширине.
    assert "width: 100%" in текст


@pytest.mark.django_db
def test_ocenka_pomechena(вошедший, дела):
    """Пробег посчитан по среднему темпу — рядом «≈»."""
    текст = вошедший.get("/").content.decode()
    assert "≈" in текст


@pytest.mark.django_db
def test_plashki_razdelov_filtruyut(вошедший, дела):
    транспорт = Category.objects.get(name="Транспорт")
    текст = вошедший.get("/").content.decode()
    assert "Транспорт 1" in текст  # число дел раздела
    assert "•" in текст  # красная точка: в разделе есть просроченное

    только_транспорт = вошедший.get(
        "/", {"раздел": str(транспорт.pk)}
    ).content.decode()
    assert "Тормозная жидкость" in только_транспорт
    assert "Фильтр воды на кухне" not in только_транспорт
    # Счётчики тоже по разделу: пробег машины остаётся, чужие уходят.
    assert "Пробег" in только_транспорт


@pytest.mark.django_db
def test_sdelano_zapisyvaet_vypolnenie_i_prosit_stoimost(вошедший, дела):
    жидкость = дела[0]
    ответ = вошедший.post(f"/done/{жидкость.pk}/")
    assert ответ.status_code == 302
    # Адрес закодирован (кириллица в переходе) — сверяем расшифровку.
    assert "стоимость=" in unquote(ответ["Location"])

    выполнение = жидкость.completions.order_by("-id").first()
    assert выполнение.date == СЕГОДНЯ
    # Показание — расчётное на сегодня: 118 000 + 160 × 10.
    assert выполнение.meter_value == Decimal("119600.00")

    экран = вошедший.get(ответ["Location"]).content.decode()
    assert "Сколько стоило" in экран
    # Отмеченное дело вернулось в норму — просрочки больше нет.
    assert "просрочено" not in экран

    вошедший.post(f"/cost/{выполнение.pk}/", {"значение": "3 500"})
    выполнение.refresh_from_db()
    assert выполнение.cost == Decimal("3500")


@pytest.mark.django_db
def test_otlozhit_i_vernut_v_spisok(вошедший, дела, хозяин):
    жидкость = дела[0]
    вошедший.post(f"/snooze/{жидкость.pk}/", {"дней": "3"})
    жидкость.refresh_from_db()
    assert жидкость.snoozed_until == СЕГОДНЯ + datetime.timedelta(days=3)

    текст = вошедший.get("/").content.decode()
    assert "молчу до" in текст
    # Отложенное ушло вниз — под спокойное дело. Смотрим на чистой
    # странице: зелёная плашка с сообщением показывается только раз.
    текст = вошедший.get("/").content.decode()
    места = порядок(текст, "Фильтр воды на кухне", "Тормозная жидкость")
    assert места == sorted(места)

    # Пока отложено — планировщик о нём молчит.
    planner.run()
    assert not Notification.objects.filter(obligation=жидкость).exists()

    вошедший.post(f"/snooze/{жидкость.pk}/", {"дней": "0"})
    жидкость.refresh_from_db()
    assert жидкость.snoozed_until is None


@pytest.mark.django_db
def test_schetchik_prosit_pokazaniya_i_prinimaet_ih(вошедший, дела, машина):
    _, счётчик = машина
    счётчик.readings.all().delete()
    MeterReading.objects.create(
        meter=счётчик, value=Decimal("118000"), date=дней_назад(120)
    )

    текст = вошедший.get("/").content.decode()
    assert "пора ввести" in текст

    вошедший.post(f"/reading/{счётчик.pk}/", {"значение": "118 500"})
    последнее = счётчик.readings.order_by("-date", "-id").first()
    assert последнее.value == Decimal("118500")
    assert последнее.date == СЕГОДНЯ
    assert "пора ввести" not in вошедший.get("/").content.decode()


@pytest.mark.django_db
def test_pokazanie_menshe_proshlogo_preduprezhdaet(вошедший, машина, дела):
    _, счётчик = машина
    ответ = вошедший.post(
        f"/reading/{счётчик.pk}/", {"значение": "100000"}, follow=True
    )
    assert "не опечатка ли" in ответ.content.decode()


@pytest.mark.django_db
def test_chuzhoe_lichnoe_ne_vidno_i_ne_menyaetsya(вошедший, сосед, дела):
    чужое = Obligation.objects.create(
        name="Личное дело соседа",
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.ONCE,
        due_date=дней_назад(1),
        owner=сосед,
        is_private=True,
    )
    текст = вошедший.get("/").content.decode()
    assert "Личное дело соседа" not in текст

    ответ = вошедший.post(f"/done/{чужое.pk}/", follow=True)
    assert "не ваше" in ответ.content.decode()
    assert not чужое.completions.exists()


@pytest.mark.django_db
def test_zakrytoe_razovoe_ne_pokazyvaetsya(вошедший, хозяин):
    разовое = Obligation.objects.create(
        name="Оплатить страховку",
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.ONCE,
        due_date=дней_назад(10),
        owner=хозяин,
    )
    assert "Оплатить страховку" in вошедший.get("/").content.decode()
    Completion.objects.create(obligation=разовое, date=дней_назад(1))
    assert "Оплатить страховку" not in вошедший.get("/").content.decode()


@pytest.mark.django_db
def test_pustaya_baza_obyasnyaet_chto_delat(вошедший):
    текст = вошедший.get("/").content.decode()
    assert "Дел пока нет" in текст
