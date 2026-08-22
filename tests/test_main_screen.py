"""Тесты экранов — пункты «Как проверим» заметок «Главный экран»
и «Разделы на главной»: плитки разделов, страница раздела,
короткий список на главной, полный список, порядок по срочности,
«Сделано» со стоимостью, «Отложить» и возврат, счётчики,
чужое личное, разделитель тысяч.

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
ПРОБЕЛ = " "  # неразрывный пробел между тысячами


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
    """Просроченное, скорое, спокойное — и одно без данных."""
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


# --- Плитки разделов и страница раздела ------------------------------------


@pytest.mark.django_db
def test_plitki_pokazyvayut_vse_razdely_i_schet(вошедший, дела):
    текст = вошедший.get("/").content.decode()
    # Все семь разделов справочника, включая пустые.
    for раздел in Category.objects.all():
        assert раздел.name in текст
    assert "1 просрочено" in текст  # Тормозная жидкость
    assert "всего 2" in текст  # Дом: фильтр и котёл
    assert "дел нет" in текст  # Хозяйство и другие пустые


@pytest.mark.django_db
def test_plitka_otkryvaet_stranicu_razdela(вошедший, дела):
    транспорт = Category.objects.get(name="Транспорт")
    страница = вошедший.get(f"/section/{транспорт.pk}/")
    текст = страница.content.decode()
    assert страница.status_code == 200
    assert "Тормозная жидкость" in текст
    assert "Фильтр воды на кухне" not in текст
    # Счётчики раздела — здесь же.
    assert "Пробег" in текст


@pytest.mark.django_db
def test_stranica_razdela_pokazyvaet_otlozhennye_i_bez_dannyh(вошедший, дела):
    дом = Category.objects.get(name="Дом")
    фильтр = дела[2]
    вошедший.post(
        f"/snooze/{фильтр.pk}/", {"дней": "3", "назад": f"/section/{дом.pk}/"}
    )
    текст = вошедший.get(f"/section/{дом.pk}/").content.decode()
    assert "молчу до" in текст  # отложенное осталось на виду
    assert "Промывка котла" in текст  # «нет данных» тоже здесь
    assert "отметьте, когда делалось в последний раз" in текст


@pytest.mark.django_db
def test_pustoy_razdel_otkryvaetsya_i_obyasnyaet(вошедший, дела):
    хозяйство = Category.objects.get(name="Хозяйство")
    текст = вошедший.get(f"/section/{хозяйство.pk}/").content.decode()
    assert "В этом разделе дел нет" in текст


@pytest.mark.django_db
def test_dela_bez_razdela_popadayut_v_prochee(вошедший, хозяин):
    Obligation.objects.create(
        name="Сдать анализы",
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.ONCE,
        due_date=дней_назад(1),
        owner=хозяин,
    )
    текст = вошедший.get("/").content.decode()
    assert "Прочее" in текст
    assert "Сдать анализы" in вошедший.get("/section/none/").content.decode()


# --- Главная и полный список -----------------------------------------------


@pytest.mark.django_db
def test_na_glavnoy_goryashchee_i_tri_blizhayshih(вошедший, дела, хозяин):
    # Ещё пять спокойных дел: на главной они целиком не поместятся.
    for номер in range(5):
        спокойное = Obligation.objects.create(
            name=f"Спокойное дело {номер}",
            rule_kind=Obligation.RuleKind.TIME,
            time_kind=Obligation.TimeKind.INTERVAL,
            interval_value=12,
            interval_unit=Obligation.IntervalUnit.MONTHS,
            owner=хозяин,
        )
        Completion.objects.create(obligation=спокойное, date=дней_назад(номер))

    текст = вошедший.get("/").content.decode()
    показано = sum(текст.count(имя) for имя in ["Спокойное дело"])
    assert показано == 0 or показано <= 3
    assert "Тормозная жидкость" in текст  # просроченное — всегда
    assert "Поздравить сестру Олю" in текст  # скорое — всегда
    assert "Промывка котла" not in текст  # «нет данных» — только в полном
    assert "показать все дела (9)" in текст


@pytest.mark.django_db
def test_polnyy_spisok_pokazyvaet_vsyo_po_srochnosti(вошедший, дела):
    текст = вошедший.get("/all/").content.decode()
    места = порядок(
        текст,
        "Тормозная жидкость",
        "Поздравить сестру Олю",
        "Фильтр воды на кухне",
        "Промывка котла",
    )
    assert места == sorted(места)
    assert "просрочено" in текст and "скоро" in текст and "в норме" in текст
    assert "width: 100%" in текст  # полоска пройденной доли интервала
    assert "Счётчики" in текст


@pytest.mark.django_db
def test_ocenka_pomechena(вошедший, дела):
    """Пробег посчитан по среднему темпу — рядом «≈»."""
    assert "≈" in вошедший.get("/").content.decode()


@pytest.mark.django_db
def test_tysyachi_razdelyayutsya_probelom(вошедший, дела):
    текст = вошедший.get("/all/").content.decode()
    assert f"119{ПРОБЕЛ}600" in текст  # оценка пробега в блоке счётчиков


# --- Действия ---------------------------------------------------------------


@pytest.mark.django_db
def test_sdelano_zapisyvaet_vypolnenie_i_prosit_stoimost(вошедший, дела):
    жидкость = дела[0]
    ответ = вошедший.post(f"/done/{жидкость.pk}/", {"назад": "/all/"})
    assert ответ.status_code == 302
    # Адрес закодирован (кириллица в переходе) — сверяем расшифровку.
    assert "стоимость=" in unquote(ответ["Location"])
    assert ответ["Location"].startswith("/all/")

    выполнение = жидкость.completions.order_by("-id").first()
    assert выполнение.date == СЕГОДНЯ
    # Показание — расчётное на сегодня: 118 000 + 160 × 10.
    assert выполнение.meter_value == Decimal("119600.00")

    экран = вошедший.get(ответ["Location"]).content.decode()
    assert "Сколько стоило" in экран
    assert "просрочено" not in экран  # дело вернулось в норму

    вошедший.post(f"/cost/{выполнение.pk}/", {"значение": "3 500"})
    выполнение.refresh_from_db()
    assert выполнение.cost == Decimal("3500")


@pytest.mark.django_db
def test_deystvie_vozvrashchaet_na_tu_zhe_stranicu(вошедший, дела):
    транспорт = Category.objects.get(name="Транспорт")
    адрес = f"/section/{транспорт.pk}/"
    ответ = вошедший.post(f"/done/{дела[0].pk}/", {"назад": адрес})
    assert ответ["Location"].startswith(адрес)


@pytest.mark.django_db
def test_chuzhoy_adres_vozvrata_ne_ispolzuetsya(вошедший, дела):
    ответ = вошедший.post(
        f"/done/{дела[0].pk}/", {"назад": "https://example.com/"}
    )
    assert ответ["Location"].startswith("/")


@pytest.mark.django_db
def test_otlozhit_i_vernut_v_spisok(вошедший, дела, хозяин):
    жидкость = дела[0]
    вошедший.post(f"/snooze/{жидкость.pk}/", {"дней": "3", "назад": "/all/"})
    жидкость.refresh_from_db()
    assert жидкость.snoozed_until == СЕГОДНЯ + datetime.timedelta(days=3)

    текст = вошедший.get("/all/").content.decode()
    assert "молчу до" in текст
    # Отложенное ушло вниз — под спокойное дело. Смотрим на чистой
    # странице: зелёная плашка с сообщением показывается только раз.
    текст = вошедший.get("/all/").content.decode()
    места = порядок(текст, "Фильтр воды на кухне", "Тормозная жидкость")
    assert места == sorted(места)
    # На главной отложенного нет — оно уже не горит.
    assert "Тормозная жидкость" not in вошедший.get("/").content.decode()

    # Пока отложено — планировщик о нём молчит.
    planner.run()
    assert not Notification.objects.filter(obligation=жидкость).exists()

    вошедший.post(f"/snooze/{жидкость.pk}/", {"дней": "0", "назад": "/all/"})
    жидкость.refresh_from_db()
    assert жидкость.snoozed_until is None


@pytest.mark.django_db
def test_schetchik_prosit_pokazaniya_i_prinimaet_ih(вошедший, дела, машина):
    _, счётчик = машина
    счётчик.readings.all().delete()
    MeterReading.objects.create(
        meter=счётчик, value=Decimal("118000"), date=дней_назад(120)
    )

    текст = вошедший.get("/all/").content.decode()
    assert "пора ввести" in текст

    вошедший.post(
        f"/reading/{счётчик.pk}/", {"значение": "118 500", "назад": "/all/"}
    )
    последнее = счётчик.readings.order_by("-date", "-id").first()
    assert последнее.value == Decimal("118500")
    assert последнее.date == СЕГОДНЯ
    assert "пора ввести" not in вошедший.get("/all/").content.decode()


@pytest.mark.django_db
def test_pokazanie_menshe_proshlogo_preduprezhdaet(вошедший, машина, дела):
    _, счётчик = машина
    ответ = вошедший.post(
        f"/reading/{счётчик.pk}/",
        {"значение": "100000", "назад": "/all/"},
        follow=True,
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
    assert "Личное дело соседа" not in вошедший.get("/all/").content.decode()

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
    assert "Оплатить страховку" in вошедший.get("/all/").content.decode()
    Completion.objects.create(obligation=разовое, date=дней_назад(1))
    assert "Оплатить страховку" not in вошедший.get("/all/").content.decode()


@pytest.mark.django_db
def test_pustaya_baza_obyasnyaet_chto_delat(вошедший):
    текст = вошедший.get("/").content.decode()
    assert "Дел пока нет" in текст
