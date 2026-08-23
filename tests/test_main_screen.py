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

    # «Нет данных» остаётся у правил по счётчику без отметок:
    # от какого показания считать — система не знает.
    вода = Meter.objects.create(item=квартира, name="Вода", unit="м³")
    # Свежий ввод: иначе «пора ввести» появится и у воды, а проверки
    # ниже говорят про пробег.
    MeterReading.objects.create(
        meter=вода, value=Decimal("100"), date=СЕГОДНЯ
    )
    котёл = Obligation.objects.create(
        name="Промывка котла",
        item=квартира,
        rule_kind=Obligation.RuleKind.METER,
        meter=вода,
        meter_interval=Decimal("50"),
        owner=хозяин,
    )
    return жидкость, сестра, фильтр, котёл


def порядок(текст, *имена):
    """Позиции названий в теле страницы — для проверки очерёдности."""
    места = [текст.find(имя) for имя in имена]
    assert all(место >= 0 for место in места), места
    return места


def карточка(текст, имя):
    """Кусок страницы от названия дела до его кнопки «Сделано».

    Нужен, чтобы отличать содержимое карточки от ленты разделов
    сверху: слово «Транспорт» на странице есть всегда, вопрос —
    осталось ли оно ещё и в подписи дела.
    """
    начало = текст.index(имя)
    return текст[начало : текст.index("Сделано", начало)]


# --- Плитки разделов и страница раздела ------------------------------------


@pytest.mark.django_db
def test_plitki_pokazyvayut_vse_razdely_i_schet(вошедший, дела):
    текст = вошедший.get("/").content.decode()
    # Все семь разделов справочника, включая пустые.
    for раздел in Category.objects.all():
        assert раздел.name in текст
    транспорт = Category.objects.get(name="Транспорт")
    # Счёт в меню — «горит · всего»: у транспорта одно дело, и оно
    # просрочено; у дома два спокойных — просто «2».
    assert f'/section/{транспорт.pk}/">' in текст
    assert "1 · 1" in текст
    # Пустые разделы собраны в свою группу и места почти не занимают
    # (правила 3 и 4 заметки «Порядок на экранах»).
    assert "пока пусто" in текст


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
    assert "все дела (9)" in текст


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
    # Срок словами вместо пилюли состояния (правило 5 заметки
    # «Порядок на экранах»).
    assert "просрочено на" in текст and "через" in текст
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


@pytest.mark.django_db
def test_svodka_schitaet_i_pokazyvaet_blizhayshiy_srok(вошедший, дела):
    """Сводка сверху: числа по странице и ближайшая будущая дата."""
    текст = вошедший.get("/").content.decode()
    assert "просрочено" in текст and "ближайший срок" in текст
    ближайший = СЕГОДНЯ + datetime.timedelta(days=23)
    месяцы = [
        "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
        "августа", "сентября", "октября", "ноября", "декабря",
    ]
    ожидаемое = f"{ближайший.day} {месяцы[ближайший.month - 1]}"
    if ближайший.year != СЕГОДНЯ.year:
        ожидаемое += f" {ближайший.year}"  # другой год пишем явно
    assert ожидаемое in текст


@pytest.mark.django_db
def test_menyu_est_na_kazhdoy_stranice_i_podsvechivaet_razdel(вошедший, дела):
    транспорт = Category.objects.get(name="Транспорт")
    для_раздела = вошедший.get(f"/section/{транспорт.pk}/").content.decode()
    assert "Самое срочное" in для_раздела  # меню на месте
    assert "Все дела" in для_раздела
    # Текущий раздел отмечен: класс «активен» стоит у его ссылки
    # в меню (а не у ссылки «+ дело» с адресом возврата).
    начало = для_раздела.index(f'href="/section/{транспорт.pk}/"')
    кусок = для_раздела[начало - 120 : начало]
    assert "активен" in кусок


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
    # Дело вернулось в норму: красной метки на карточке больше нет.
    assert ">просрочено</span>" not in экран

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


# --- Заметка «Порядок на экранах» -------------------------------------------


@pytest.mark.django_db
def test_kartochka_spiska_pokazyvaet_srok_i_pravilo_povtora(вошедший, дела):
    """Правило 5 и 3: справа — когда, под названием — как часто."""
    текст = вошедший.get("/all/").content.decode()
    # Фильтр воды: интервал 12 месяцев, отметка 60 дней назад.
    начало = текст.index("Фильтр воды на кухне")
    карточка = текст[начало : начало + 400]
    assert "через" in карточка
    assert "каждые 12 месяцев" in карточка
    # Ежегодное дело сестры называет свой день.
    начало = текст.index("Поздравить сестру")
    assert "раз в год" in текст[начало : начало + 400]


@pytest.mark.django_db
def test_otlozhit_svyornuto_pod_odnu_knopku(вошедший, дела):
    """Правило 7: сроки откладывания раскрываются нажатием."""
    текст = вошедший.get("/all/").content.decode()
    assert '<details class="отложить">' in текст
    assert "<summary>отложить</summary>" in текст
    # Сами сроки на месте — просто спрятаны под кнопку.
    assert "1 дн" in текст and "3 дн" in текст and "7 дн" in текст


@pytest.mark.django_db
def test_kartochka_dela_pokazyvaet_sut_i_rashody(вошедший, хозяин):
    """Правила 8 и 9: сверху суть, среди фактов — расходы."""
    квартира = Item.objects.create(
        name="Квартира", category=Category.objects.get(name="Дом")
    )
    дело = Obligation.objects.create(
        name="Промывка котла",
        item=квартира,
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.INTERVAL,
        interval_value=6,
        interval_unit=Obligation.IntervalUnit.MONTHS,
        owner=хозяин,
    )
    for дней, цена in ((400, "6500"), (200, "6000"), (30, "6000")):
        Completion.objects.create(
            obligation=дело, date=дней_назад(дней), cost=Decimal(цена)
        )

    текст = вошедший.get(f"/obligation/{дело.pk}/").content.decode()
    assert f"18{ПРОБЕЛ}500 ₽ за 3 раза" in текст
    assert "каждые 6 месяцев" in текст
    assert "Квартира · Дом" in текст  # к чему относится
    assert "Сделано сегодня" in текст
    # Суть идёт выше форм: расходы встречаются раньше фактуры.
    assert текст.index("потрачено") < текст.index("Фактура")


@pytest.mark.django_db
def test_delo_bez_cen_govorit_chto_ne_zapisano(вошедший, дела):
    текст = вошедший.get(f"/obligation/{дела[2].pk}/").content.decode()
    assert "не записано" in текст


@pytest.mark.django_db
def test_knopka_novogo_dela_odna_i_znaet_razdel(вошедший, дела):
    """Кнопка «+ дело» на странице одна — в сводке, и она умная."""
    for адрес in ("/", "/all/"):
        assert вошедший.get(адрес).content.decode().count("+ дело") == 1

    транспорт = Category.objects.get(name="Транспорт")
    текст = вошедший.get(f"/section/{транспорт.pk}/").content.decode()
    assert текст.count("+ дело") == 1
    # На странице раздела кнопка подставляет этот раздел
    # (в адресе имена параметров закодированы — раскрываем).
    assert f"раздел={транспорт.pk}" in unquote(текст)


# --- Значки разделов (заметка «Значки разделов») ---------------------------


@pytest.mark.django_db
def test_znachok_zamenyaet_slovo_razdela_v_kartochke(вошедший, дела):
    """В подписи карточки вместо слова раздела — его значок."""
    кусок = карточка(вошедший.get("/all/").content.decode(), "Тормозная")
    assert "#и-машина" in кусок  # значок «Транспорта»
    assert "Транспорт" not in кусок  # слово ушло, строка короче
    assert "Kia" in кусок  # остальная подпись на месте


@pytest.mark.django_db
def test_razdel_bez_znachka_ostavlyaet_slovo(вошедший, дела):
    """Значка нет — показываем слово, пустого кружка не рисуем."""
    дом = Category.objects.get(name="Дом")
    дом.icon = ""
    дом.save()
    кусок = карточка(вошедший.get("/all/").content.decode(), "Фильтр воды")
    assert "Дом" in кусок and "кружок" not in кусок


@pytest.mark.django_db
def test_znachok_v_lente_i_v_shapke_razdela(вошедший, дела):
    """Значок в ленте и в шапке раздела стоит рядом со словом."""
    главная = вошедший.get("/").content.decode()
    assert "#и-машина" in главная and "Транспорт" in главная

    транспорт = Category.objects.get(name="Транспорт")
    страница = вошедший.get(f"/section/{транспорт.pk}/").content.decode()
    # Кружок в шапке крупный, оттенок — тот же, что у карточек дел.
    assert "кружок к1 крупный" in страница and "Транспорт" in страница


@pytest.mark.django_db
def test_znachok_v_kartochke_dela(вошедший, дела):
    """На карточке дела значок стоит перед «к чему относится»."""
    текст = вошедший.get(f"/obligation/{дела[0].pk}/").content.decode()
    assert "#и-машина" in текст and "Kia · Транспорт" in текст


@pytest.mark.django_db
def test_zakrytoe_razovoe_delo_tozhe_so_znachkom(вошедший, хозяин):
    """Карточка выполненного разового дела не теряет значок раздела."""
    дело = Obligation.objects.create(
        name="Забрать посылку",
        rule_kind=Obligation.RuleKind.TIME,
        time_kind=Obligation.TimeKind.ONCE,
        due_date=дней_назад(10),
        owner=хозяин,
        category=Category.objects.get(name="Хозяйство"),
    )
    Completion.objects.create(obligation=дело, date=дней_назад(9))
    текст = вошедший.get(f"/obligation/{дело.pk}/").content.decode()
    assert "#и-участок" in текст and "Хозяйство" in текст


@pytest.mark.django_db
def test_pravka_dostupna_pryamo_iz_spiska(вошедший, дела):
    """Кнопка «править» стоит в карточке и возвращает на тот же экран.

    Раньше правка пряталась за нажатием на название дела —
    догадаться было нельзя, особенно на телефоне без наведения мыши.
    """
    главная = вошедший.get("/").content.decode()
    assert f"/obligation/{дела[0].pk}/edit/?назад=" in главная
    assert ">править</a>" in главная

    # Ссылка рабочая: форма правки открывается и знает дело.
    страница = вошедший.get(
        f"/obligation/{дела[0].pk}/edit/", {"назад": "/"}
    )
    assert страница.status_code == 200
    assert дела[0].name in страница.content.decode()
