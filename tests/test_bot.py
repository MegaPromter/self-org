"""Тесты Telegram-бота — пункты «Как проверим» заметки
«Telegram-бот»: сообщение с фактурой и кнопками, «Сделано»
и стоимость ответом, отложка, ввод показания, устаревание
залежавшихся, отказ постороннему чату, привязка чата кодом.

Сети здесь нет: проверяются действия кнопок (`core/bot_actions.py`)
и сборка сообщения (`core/telegram.py`), а сама отправка
подменяется.
"""
import datetime
from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.utils import timezone

from core import bot_actions, planner, telegram
from core.models import (
    Completion,
    Fact,
    Item,
    Meter,
    MeterReading,
    Notification,
    NotificationProfile,
    Obligation,
    PendingInput,
)

СЕГОДНЯ = datetime.date(2026, 8, 22)
ЧАТ = "555000111"
ЧУЖОЙ_ЧАТ = "999999999"


def дней_назад(n):
    return СЕГОДНЯ - datetime.timedelta(days=n)


def момент(день=СЕГОДНЯ, час=12, минута=0):
    return timezone.make_aware(
        datetime.datetime.combine(день, datetime.time(час, минута))
    )


@pytest.fixture(autouse=True)
def канал_telegram():
    """Канал Telegram в планировщике — независимо от порядка тестов.

    Обычно его подключает старт приложения (`core/apps.py`), но
    соседние тесты подменяют список каналов своими.
    """
    было = list(planner._channels)
    planner.register_channel(telegram.send)
    yield
    planner._channels[:] = было


@pytest.fixture
def антон(db):
    пользователь = User.objects.create_user("anton")
    NotificationProfile.objects.create(
        user=пользователь, telegram_chat_id=ЧАТ
    )
    return пользователь


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
    Fact.objects.create(
        content_object=обязательство, name="артикул", value="152089E42A"
    )
    return обязательство


# --- Сообщение: текст, фактура, кнопки (правило 1) -------------------------


def test_soobshchenie_s_fakturoy_i_knopkami(масло, антон):
    planner.run(момент())
    уведомление = Notification.objects.get()

    текст = telegram.build_text(уведомление)
    assert "Масло ДВС" in текст
    assert "артикул: 152089E42A" in текст

    кнопки = telegram.build_keyboard(уведомление).inline_keyboard
    подписи = [к.text for ряд in кнопки for к in ряд]
    assert подписи == ["Сделано", "Отложить", "Ввести показание"]


def test_u_napominaniya_o_pokazaniyah_odna_knopka(антон):
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
    planner.run(момент())

    уведомление = Notification.objects.get(kind=Notification.Kind.READING)
    подписи = [
        к.text
        for ряд in telegram.build_keyboard(уведомление).inline_keyboard
        for к in ряд
    ]
    assert подписи == ["Ввести показание"]


def test_otpravka_uhodit_v_privyazannyy_chat(масло, антон, settings, monkeypatch):
    """Канал отдаёт сообщение в чат получателя, журнал — «отправлено»."""
    settings.TELEGRAM_BOT_TOKEN = "тестовый-токен"
    ушло = []
    monkeypatch.setattr(
        telegram,
        "_отправить",
        lambda чат, текст, клавиатура: ушло.append((чат, текст)),
    )
    monkeypatch.setattr(telegram.asyncio, "run", lambda корутина: корутина)

    итог = planner.run(момент())

    assert итог.sent == 1
    assert ушло[0][0] == ЧАТ
    assert "Масло ДВС" in ушло[0][1]
    assert Notification.objects.get().status == Notification.Status.SENT


def test_bez_privyazki_uvedomlenie_zhdet(масло, антон, settings):
    settings.TELEGRAM_BOT_TOKEN = "тестовый-токен"
    NotificationProfile.objects.update(telegram_chat_id="")

    итог = planner.run(момент())

    assert итог.sent == 0
    assert Notification.objects.get().status == Notification.Status.PENDING


# --- Кнопка «Сделано» и стоимость ответом (правило 4) ----------------------


def test_sdelano_pishet_vypolnenie_s_raschetnym_probegom(масло):
    ответ = bot_actions.mark_done(ЧАТ, масло.pk, today=СЕГОДНЯ)

    выполнение = масло.completions.order_by("-id").first()
    assert выполнение.date == СЕГОДНЯ
    assert выполнение.meter_value == Decimal(119_600)  # оценка на сегодня
    assert "отмечено 22.08.2026" in ответ
    assert "Сколько стоило" in ответ


def test_stoimost_otvetom_dopisyvaetsya_v_vypolnenie(масло, антон):
    bot_actions.mark_done(ЧАТ, масло.pk, today=СЕГОДНЯ, now=момент())

    ответ = bot_actions.handle_text(ЧАТ, "3500", now=момент(час=13))

    выполнение = масло.completions.order_by("-id").first()
    assert выполнение.cost == Decimal(3500)
    assert "3500" in ответ
    assert not PendingInput.objects.exists()  # вопрос закрыт


def test_vopros_o_stoimosti_ustarevaet_za_sutki(масло, антон):
    bot_actions.mark_done(ЧАТ, масло.pk, today=СЕГОДНЯ, now=момент())

    поздно = момент(день=СЕГОДНЯ + datetime.timedelta(days=2))
    ответ = bot_actions.handle_text(ЧАТ, "3500", now=поздно)

    assert ответ == bot_actions.ПОДСКАЗКА
    assert масло.completions.order_by("-id").first().cost is None


# --- Кнопка «Отложить» (правило 5) -----------------------------------------


def test_otlozhit_na_nedelyu_glushit_napominaniya(масло):
    planner.run(момент())
    Notification.objects.all().delete()

    ответ = bot_actions.snooze(ЧАТ, масло.pk, 7, today=СЕГОДНЯ)
    assert "29.08.2026" in ответ

    через_три_дня = момент(день=СЕГОДНЯ + datetime.timedelta(days=3))
    assert planner.run(через_три_дня).created == 0

    через_неделю = момент(день=СЕГОДНЯ + datetime.timedelta(days=7))
    assert planner.run(через_неделю).created == 1


def test_otmetka_vypolneniya_snimaet_otlozhku(масло):
    bot_actions.snooze(ЧАТ, масло.pk, 7, today=СЕГОДНЯ)
    bot_actions.mark_done(ЧАТ, масло.pk, today=СЕГОДНЯ)

    масло.refresh_from_db()
    assert масло.snoozed_until is None


# --- Кнопка «Ввести показание» (правило 6) ---------------------------------


def test_vvod_pokazaniya_zapisyvaetsya(масло, антон):
    bot_actions.ask_reading(ЧАТ, масло.meter_id, now=момент())

    ответ = bot_actions.handle_text(
        ЧАТ, "118 500", now=момент(час=13), today=СЕГОДНЯ
    )

    показание = масло.meter.readings.order_by("-id").first()
    assert показание.value == Decimal(118_500)
    assert показание.date == СЕГОДНЯ
    assert "118500" in ответ.replace(" ", "")


def test_pokazanie_menshe_proshlogo_prinimaetsya_s_preduprezhdeniem(
    масло, антон
):
    bot_actions.ask_reading(ЧАТ, масло.meter_id, now=момент())

    ответ = bot_actions.handle_text(
        ЧАТ, "11800", now=момент(час=13), today=СЕГОДНЯ
    )

    assert масло.meter.readings.order_by("-id").first().value == Decimal(11_800)
    assert "опечатка" in ответ


def test_ne_chislo_prosyat_povtorit(масло, антон):
    bot_actions.ask_reading(ЧАТ, масло.meter_id, now=момент())

    ответ = bot_actions.handle_text(ЧАТ, "около ста тысяч", now=момент(час=13))

    assert "Нужно число" in ответ
    assert PendingInput.objects.exists()  # вопрос остаётся открытым


# --- Устаревание залежавшихся (правило 3) ----------------------------------


def test_zalezhavshiesya_ne_vyvalivayutsya_pachkoy(масло, антон, settings):
    """Подключили бота — старое помечено «устарело», свежее уходит."""
    settings.TELEGRAM_BOT_TOKEN = ""  # канала ещё нет: копится «ожидает»
    planner.run(момент(день=дней_назад(3)))
    старое = Notification.objects.get()

    итог = planner.run(момент())

    старое.refresh_from_db()
    assert старое.status == Notification.Status.STALE
    assert итог.stale == 1
    # Тема актуальна — напоминание создано заново, уже свежее.
    свежее = Notification.objects.exclude(pk=старое.pk).get()
    assert свежее.status == Notification.Status.PENDING


def test_ustarevshee_ne_meshaet_napomnit_zanovo(масло, антон):
    planner.run(момент(день=дней_назад(3)))
    Notification.objects.update(status=Notification.Status.STALE)

    итог = planner.run(момент())

    assert итог.created == 1


# --- Посторонний чат (правило 7) -------------------------------------------


def test_postoronniy_chat_poluchaet_otkaz(масло):
    ответы = [
        bot_actions.mark_done(ЧУЖОЙ_ЧАТ, масло.pk, today=СЕГОДНЯ),
        bot_actions.snooze(ЧУЖОЙ_ЧАТ, масло.pk, 7, today=СЕГОДНЯ),
        bot_actions.ask_reading(ЧУЖОЙ_ЧАТ, масло.meter_id),
        bot_actions.handle_text(ЧУЖОЙ_ЧАТ, "3500"),
    ]

    assert ответы == [bot_actions.ОТКАЗ] * 4
    assert масло.completions.count() == 1  # только та, что из фикстуры
    масло.refresh_from_db()
    assert масло.snoozed_until is None


def test_chuzhoe_obyazatelstvo_ne_tronuto(масло, антон):
    маша = User.objects.create_user("masha")
    NotificationProfile.objects.create(
        user=маша, telegram_chat_id=ЧУЖОЙ_ЧАТ
    )

    ответ = bot_actions.mark_done(ЧУЖОЙ_ЧАТ, масло.pk, today=СЕГОДНЯ)

    assert ответ == bot_actions.ЧУЖОЕ
    assert масло.completions.count() == 1


# --- Привязка чата (правило 8) ---------------------------------------------


def test_privyazka_po_kodu_iz_adminki(db):
    пользователь = User.objects.create_user("anton")
    код = bot_actions.make_link_code(пользователь.pk)

    ответ = bot_actions.link_chat(код, ЧАТ)

    профиль = NotificationProfile.objects.get(user=пользователь)
    assert профиль.telegram_chat_id == ЧАТ
    assert "Готово" in ответ


def test_poddelannyy_kod_ne_privyazyvaet(db):
    пользователь = User.objects.create_user("anton")
    код = bot_actions.make_link_code(пользователь.pk)

    ответ = bot_actions.link_chat(код[:-1] + "x", ЧАТ)

    assert not NotificationProfile.objects.filter(
        telegram_chat_id=ЧАТ
    ).exists()
    assert "не подошёл" in ответ


def test_adminka_pokazyvaet_ssylku_privyazki(db, settings):
    """На странице настроек — рабочая ссылка, а после привязки —
    подсказка, как отвязать."""
    settings.TELEGRAM_BOT_USERNAME = "selforg_bot"
    пользователь = User.objects.create_user("anton")
    профиль = NotificationProfile.objects.create(user=пользователь)
    страница = admin.site._registry[NotificationProfile]

    ссылка = страница.подключение(профиль)
    assert "https://t.me/selforg_bot?start=" in ссылка
    код = ссылка.split("start=")[1].split('"')[0]
    assert bot_actions.parse_link_code(код) == пользователь.pk

    профиль.telegram_chat_id = ЧАТ
    assert "Подключено" in страница.подключение(профиль)


def test_prosrochennyy_kod_ne_privyazyvaet(db):
    пользователь = User.objects.create_user("anton")
    давно = timezone.now() - datetime.timedelta(days=8)
    код = bot_actions.make_link_code(пользователь.pk, now=давно)

    ответ = bot_actions.link_chat(код, ЧАТ)

    assert not NotificationProfile.objects.filter(
        telegram_chat_id=ЧАТ
    ).exists()
    assert "не подошёл" in ответ
