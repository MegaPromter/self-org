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
def test_novoe_delo_so_schetchikom_iz_spravochnika_schitaet_srok(
    вошедший, хозяин
):
    """Счётчик берётся из справочника (заметка «…без создания на лету»)."""
    транспорт = Category.objects.get(name="Транспорт")
    предмет = Item.objects.create(name="Kia Rio", category=транспорт)
    счётчик = Meter.objects.create(item=предмет, name="Пробег", unit="км")
    ответ = вошедший.post(
        "/new/",
        {
            "name": "Замена масла",
            "category": транспорт.pk,
            "item": предмет.pk,
            "способ": "счётчик",
            "meter": счётчик.pk,
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
    assert дело.item == предмет and дело.meter == счётчик
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
            "способ": "повторяется",
            "повтор_вид": "годовщина",
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
            "способ": "счётчик",
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


# --- Заметка «Порядок на экранах» -------------------------------------------


@pytest.mark.django_db
def test_povtoryaetsya_i_eshchyo_po_schetchiku_dayot_oba_pravila(
    вошедший, хозяин
):
    """Плитка «повторяется» с галочкой = прежнее «оба сразу»."""
    предмет = Item.objects.create(name="Hyundai")
    счётчик = Meter.objects.create(item=предмет, name="Пробег", unit="км")
    ответ = вошедший.post(
        "/new/",
        {
            "name": "Масло ДВС",
            "способ": "повторяется",
            "повтор_вид": "интервал",
            "interval_value": "6",
            "interval_unit": Obligation.IntervalUnit.MONTHS,
            "ещё_по_счётчику": "on",
            "item": предмет.pk,
            "meter": счётчик.pk,
            "meter_interval": "8000",
            "soon_threshold_percent": "70",
            "overdue_repeat_days": "7",
        },
    )
    assert ответ.status_code == 302
    дело = Obligation.objects.get(name="Масло ДВС")
    assert дело.rule_kind == Obligation.RuleKind.BOTH
    assert дело.time_kind == Obligation.TimeKind.INTERVAL
    assert дело.meter_interval == Decimal("8000")


@pytest.mark.django_db
def test_delo_kogda_poluchitsya_zavoditsya_plitkoy(вошедший):
    ответ = вошедший.post(
        "/new/",
        {
            "name": "Разобрать гараж",
            "способ": "когда_получится",
            "soon_threshold_percent": "70",
            "overdue_repeat_days": "7",
        },
    )
    assert ответ.status_code == 302
    дело = Obligation.objects.get(name="Разобрать гараж")
    assert дело.rule_kind == Obligation.RuleKind.TIME
    assert дело.time_kind == Obligation.TimeKind.SOMEDAY


@pytest.mark.django_db
def test_zavedyonnoe_delo_otkryvaetsya_v_svoyom_sposobe(вошедший, хозяин):
    """Правка показывает то же правило, каким дело заведено."""
    предмет = Item.objects.create(name="Kia")
    счётчик = Meter.objects.create(item=предмет, name="Пробег", unit="км")
    дело = Obligation.objects.create(
        name="Масло",
        item=предмет,
        rule_kind=Obligation.RuleKind.BOTH,
        time_kind=Obligation.TimeKind.INTERVAL,
        interval_value=6,
        interval_unit=Obligation.IntervalUnit.MONTHS,
        meter=счётчик,
        meter_interval=Decimal("8000"),
        owner=хозяин,
    )
    текст = вошедший.get(f"/obligation/{дело.pk}/edit/").content.decode()

    def отмечено(кусок):
        """Стоит ли «checked» у поля — до закрывающей скобки тега."""
        начало = текст.index(кусок)
        return "checked" in текст[начало : текст.index(">", начало)]

    assert отмечено('value="повторяется"')
    assert отмечено('value="интервал"')
    assert отмечено('name="ещё_по_счётчику"')


@pytest.mark.django_db
def test_na_forme_net_menyu_i_svodki(вошедший):
    """Правило 2: заполняющему форму меню и сводка не нужны."""
    текст = вошедший.get("/new/").content.decode()
    assert "ближайший срок" not in текст
    assert "Самое срочное" not in текст


# --- «Начало напоминаний»: поле «когда напомнить первый раз» ----------------


def через_дней(n):
    return СЕГОДНЯ + datetime.timedelta(days=n)


@pytest.mark.django_db
def test_pervoe_napominanie_zadayot_srok_bez_otmetki(вошедший):
    """Дата в форме становится первым сроком, выполнения не заводится."""
    ответ = вошедший.post(
        "/new/",
        {
            "name": "Ящики для хранения еды",
            "способ": "повторяется",
            "повтор_вид": "интервал",
            "interval_value": "3",
            "interval_unit": "days",
            "start_date": через_дней(8).isoformat(),
            "soon_threshold_percent": "70",
            "overdue_repeat_days": "7",
        },
    )
    assert ответ.status_code == 302

    дело = Obligation.objects.get(name="Ящики для хранения еды")
    assert дело.start_date == через_дней(8)
    assert дело.completions.count() == 0  # отметки о выдуманном деле нет

    статус = compute_status(дело, СЕГОДНЯ)
    assert статус.due_date == через_дней(8)
    assert статус.state is State.OK


@pytest.mark.django_db
def test_dve_tochki_otschyota_srazu_ne_prinimayutsya(вошедший):
    """Правило 4: «первый раз» и «последний раз» вместе — ошибка."""
    ответ = вошедший.post(
        "/new/",
        {
            "name": "Ящики для хранения еды",
            "способ": "повторяется",
            "повтор_вид": "интервал",
            "interval_value": "3",
            "interval_unit": "days",
            "start_date": через_дней(8).isoformat(),
            "последний_раз": дней_назад(2).isoformat(),
            "soon_threshold_percent": "70",
            "overdue_repeat_days": "7",
        },
    )
    assert ответ.status_code == 200  # форма вернулась с ошибкой
    assert "одно" in ответ.content.decode()
    assert not Obligation.objects.filter(
        name="Ящики для хранения еды"
    ).exists()


@pytest.mark.django_db
def test_u_razovogo_dela_pervyy_srok_ne_zapisyvaetsya(вошедший):
    """Правило 7: у «один раз к дате» поле спрятано и не сохраняется."""
    вошедший.post(
        "/new/",
        {
            "name": "Забрать посылку",
            "способ": "дата",
            "due_date": через_дней(3).isoformat(),
            "start_date": через_дней(8).isoformat(),
            "soon_threshold_percent": "70",
            "overdue_repeat_days": "7",
        },
    )
    дело = Obligation.objects.get(name="Забрать посылку")
    assert дело.start_date is None


# --- Заметка «Счётчик в форме дела: подписи и текущее показание» -----------


@pytest.mark.django_db
def test_forma_ne_sozdayot_schetchik_i_vedyot_v_spravochniki(вошедший):
    """Заметка «…без создания на лету»: полей нового счётчика нет,
    без выбранного счётчика — ошибка со ссылкой, дело не создано."""
    текст = вошедший.get("/new/").content.decode()
    assert 'name="новый_счётчик"' not in текст
    assert 'name="единица_счётчика"' not in текст
    assert "Создайте его в справочниках" in текст and "/meter/new/" in текст

    ответ = вошедший.post(
        "/new/",
        {
            "name": "Замена ДВС",
            "новый_предмет": "Hyundai Tucson",
            "способ": "счётчик",
            "meter_interval": "6000",
            "soon_threshold_percent": "70",
            "overdue_repeat_days": "7",
        },
    )
    assert ответ.status_code == 200
    текст = ответ.content.decode()
    assert "Выберите счётчик" in текст and "/meter/new/" in текст
    assert not Obligation.objects.exists()
    assert not Item.objects.filter(name="Hyundai Tucson").exists()


@pytest.mark.django_db
def test_tekushchee_pokazanie_zapisyvaetsya_segodnyashnim_chislom(
    вошедший,
):
    """Правило 1: текущее показание — запись в истории датой сегодня."""
    предмет = Item.objects.create(name="Hyundai Tucson")
    счётчик = Meter.objects.create(item=предмет, name="пробег", unit="км")
    ответ = вошедший.post(
        "/new/",
        {
            "name": "Замена ДВС",
            "item": предмет.pk,
            "способ": "счётчик",
            "meter": счётчик.pk,
            "meter_interval": "6000",
            "текущее_показание": "118400",
            "soon_threshold_percent": "70",
            "overdue_repeat_days": "7",
        },
    )
    assert ответ.status_code == 302
    [показание] = счётчик.readings.all()
    assert (показание.value, показание.date) == (Decimal("118400"), СЕГОДНЯ)
    # Правило 6: где счётчик сейчас — известно, когда делали — нет.
    дело = Obligation.objects.get(name="Замена ДВС")
    assert compute_status(дело, СЕГОДНЯ).state is State.NO_DATA


@pytest.fixture
def дело_со_счётчиком(хозяин):
    предмет = Item.objects.create(name="Hyundai Tucson")
    счётчик = Meter.objects.create(item=предмет, name="пробег", unit="км")
    MeterReading.objects.create(
        meter=счётчик, value=Decimal("118400"), date=дней_назад(3)
    )
    return Obligation.objects.create(
        name="Замена ДВС",
        item=предмет,
        rule_kind=Obligation.RuleKind.METER,
        meter=счётчик,
        meter_interval=Decimal("6000"),
        owner=хозяин,
    )


def _правка(client, дело, **поля):
    данные = {
        "name": дело.name,
        "способ": "счётчик",
        "item": дело.item.pk,
        "meter": дело.meter.pk,
        "meter_interval": "6000",
        "soon_threshold_percent": "70",
        "overdue_repeat_days": "7",
    }
    данные.update(поля)
    return client.post(f"/obligation/{дело.pk}/edit/", данные, follow=True)


@pytest.mark.django_db
def test_forma_pravki_pokazyvaet_edinicu_v_spiske_i_poslednee_pokazanie(
    вошедший, дело_со_счётчиком
):
    """Пункты 2–3: единица в списке выбора, последнее показание в поле."""
    текст = вошедший.get(f"/obligation/{дело_со_счётчиком.pk}/edit/").content.decode()
    assert "пробег (км) — Hyundai Tucson" in текст
    assert 'name="текущее_показание"' in текст and 'value="118400.00"' in текст


@pytest.mark.django_db
def test_sohranenie_bez_pravok_ne_plodit_pokazaniya(вошедший, дело_со_счётчиком):
    """Правило 1: равное последнему не пишется, изменённое — пишется."""
    счётчик = дело_со_счётчиком.meter
    _правка(вошедший, дело_со_счётчиком, текущее_показание="118400")
    assert счётчик.readings.count() == 1
    _правка(вошедший, дело_со_счётчиком, текущее_показание="118900")
    assert счётчик.readings.count() == 2
    assert счётчик.readings.first().value == Decimal("118900")


@pytest.mark.django_db
def test_pokazanie_menshe_predydushchego_zapisyvaetsya_s_preduprezhdeniem(
    вошедший, дело_со_счётчиком
):
    """Правило 2: как у кнопки «Ввести показание» — пишем и предупреждаем."""
    ответ = _правка(вошедший, дело_со_счётчиком, текущее_показание="100000")
    assert дело_со_счётчиком.meter.readings.count() == 2
    текст = ответ.content.decode()
    # тысячи разделяются неразрывным пробелом (short_number)
    assert "меньше предыдущего" in текст and "118 400 км" in текст
