"""Тесты справочников — второй заход заметки «Заведение данных
без админки»: предметы, люди, счётчики, разделы, история показаний
и фактура — всё без админки.
"""
import datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import (
    Category,
    Fact,
    Item,
    Meter,
    MeterReading,
    Obligation,
    Person,
)

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


@pytest.fixture
def машина(db):
    предмет = Item.objects.create(
        name="Kia Rio", category=Category.objects.get(name="Транспорт")
    )
    счётчик = Meter.objects.create(item=предмет, name="Пробег", unit="км")
    MeterReading.objects.create(
        meter=счётчик, value=Decimal("180000"), date=дней_назад(30)
    )
    return предмет, счётчик


@pytest.mark.django_db
def test_spravochniki_pokazyvayut_vsyo_zavedyonnoe(вошедший, машина):
    Person.objects.create(name="Оля", birth_date=datetime.date(1985, 9, 14))
    текст = вошедший.get("/catalog/").content.decode()
    assert "Kia Rio" in текст and "Пробег" in текст and "Оля" in текст
    assert "Транспорт" in текст  # разделы тоже здесь


@pytest.mark.django_db
def test_predmet_zavoditsya_i_pravitsya_bez_adminki(вошедший):
    дом = Category.objects.get(name="Дом")
    вошедший.post(
        "/item/new/", {"name": "Котёл", "category": дом.pk, "notes": ""}
    )
    предмет = Item.objects.get(name="Котёл")
    assert предмет.category == дом

    вошедший.post(
        f"/item/{предмет.pk}/edit/",
        {"name": "Котёл газовый", "category": дом.pk, "notes": "на кухне"},
    )
    предмет.refresh_from_db()
    assert предмет.name == "Котёл газовый" and предмет.notes == "на кухне"


@pytest.mark.django_db
def test_stranica_predmeta_sobiraet_schetchiki_i_dela(
    вошедший, машина, хозяин
):
    предмет, счётчик = машина
    Obligation.objects.create(
        name="Замена масла",
        item=предмет,
        rule_kind=Obligation.RuleKind.METER,
        meter=счётчик,
        meter_interval=Decimal("10000"),
        owner=хозяин,
    )
    текст = вошедший.get(f"/item/{предмет.pk}/").content.decode()
    assert "Пробег" in текст and "Замена масла" in текст
    assert "Фактура" in текст


@pytest.mark.django_db
def test_istoriya_pokazaniy_dobavlyaetsya_i_udalyaetsya(вошедший, машина):
    _, счётчик = машина
    вошедший.post(
        f"/meter/{счётчик.pk}/reading/",
        {"п-value": "185000", "п-date": дней_назад(2).isoformat()},
    )
    показания = счётчик.readings.order_by("-date")
    assert показания.count() == 2
    assert показания.first().value == Decimal("185000")

    ошибочное = показания.first()
    вошедший.post(f"/reading/{ошибочное.pk}/delete/")
    assert счётчик.readings.count() == 1


@pytest.mark.django_db
def test_schetchik_pod_pravilom_ne_udalyaetsya(вошедший, машина, хозяин):
    предмет, счётчик = машина
    Obligation.objects.create(
        name="Замена масла",
        item=предмет,
        rule_kind=Obligation.RuleKind.METER,
        meter=счётчик,
        meter_interval=Decimal("10000"),
        owner=хозяин,
    )
    ответ = вошедший.post(f"/meter/{счётчик.pk}/delete/", follow=True)
    assert Meter.objects.filter(pk=счётчик.pk).exists()
    assert "не удалить" in ответ.content.decode()


@pytest.mark.django_db
def test_faktura_dobavlyaetsya_i_udalyaetsya(вошедший, машина):
    предмет, _ = машина
    вошедший.post(
        f"/fact/item/{предмет.pk}/add/",
        {"ф-name": "Масляный фильтр", "ф-value": "W7015, в гараже, 900 ₽"},
    )
    строка = Fact.objects.get(name="Масляный фильтр")
    текст = вошедший.get(f"/item/{предмет.pk}/").content.decode()
    assert "W7015" in текст

    вошедший.post(f"/fact/{строка.pk}/delete/")
    assert not Fact.objects.filter(pk=строка.pk).exists()


@pytest.mark.django_db
def test_razdel_zavoditsya_i_menyaet_poryadok(вошедший):
    вошедший.post("/category/new/", {"name": "Дача", "order": "80"})
    раздел = Category.objects.get(name="Дача")
    вошедший.post(
        f"/category/{раздел.pk}/edit/", {"name": "Дача и сад", "order": "15"}
    )
    раздел.refresh_from_db()
    assert раздел.name == "Дача и сад" and раздел.order == 15


@pytest.mark.django_db
def test_chelovek_zavoditsya_s_dnyom_rozhdeniya(вошедший):
    вошедший.post(
        "/person/new/",
        {"name": "Оля", "birth_date": "1985-09-14", "notes": "сестра"},
    )
    человек = Person.objects.get(name="Оля")
    assert человек.birth_date == datetime.date(1985, 9, 14)
    assert "сестра" in вошедший.get(f"/person/{человек.pk}/").content.decode()


@pytest.mark.django_db
def test_spravochniki_trebuyut_vhoda(client):
    ответ = client.get("/catalog/")
    assert ответ.status_code == 302 and "/login/" in ответ["Location"]
