"""Действия кнопок Telegram — заметка «Telegram-бот».

Здесь только работа с данными: кто нажал, что это меняет и что
ответить. Телеграмная обвязка (сеть, кнопки, опрос сообщений) —
в `core/telegram.py`. Разделение нужно, чтобы правила проверялись
тестами без сети и без бота.

Все функции возвращают готовый текст ответа пользователю.
"""
import base64
import datetime
import hashlib
import hmac
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from .models import (
    Completion,
    Meter,
    MeterReading,
    NotificationProfile,
    Obligation,
    PendingInput,
)
from .status import estimate_meter, short_number

# Сколько ждём ответ на вопрос бота: не ответили за сутки — вопрос
# снят, случайное число позже не запишется как показание.
ANSWER_TIMEOUT = datetime.timedelta(days=1)

# Код привязки живёт неделю: успеть открыть ссылку из админки.
LINK_CODE_TTL = datetime.timedelta(days=7)
_ДЛИНА_ПОДПИСИ = 20

ОТКАЗ = (
    "Этот чат не подключён к системе. Откройте ссылку привязки "
    "в админке — там же лежит код."
)
ЧУЖОЕ = "Это обязательство не ваше — ничего не изменено."
ПОДСКАЗКА = (
    "Не жду от вас числа. Нажмите кнопку в напоминании — "
    "«Сделано», «Отложить» или «Ввести показание»."
)


# --- Код привязки чата (правило 8) -----------------------------------------
#
# Код уходит в ссылку «t.me/бот?start=КОД», а Telegram допускает
# там только буквы, цифры, «-» и «_». Поэтому не готовый механизм
# подписи Django (он ставит двоеточия), а свой компактный:
# «u<номер>t<время>» плюс подпись фиксированной длины в конце.


def _подпись(основа: str) -> str:
    дайджест = hmac.new(
        settings.SECRET_KEY.encode(), основа.encode(), hashlib.sha256
    ).digest()
    return (
        base64.urlsafe_b64encode(дайджест).decode().rstrip("=")[
            :_ДЛИНА_ПОДПИСИ
        ]
    )


def make_link_code(user_id: int, now: datetime.datetime | None = None) -> str:
    """Одноразовая метка для ссылки привязки: кто и когда её получил."""
    now = now or timezone.now()
    основа = f"u{user_id}t{int(now.timestamp())}"
    return основа + _подпись(основа)


def parse_link_code(
    code: str, now: datetime.datetime | None = None
) -> int | None:
    """Номер учётки из кода; подделан или просрочен — None."""
    now = now or timezone.now()
    code = (code or "").strip()
    if len(code) <= _ДЛИНА_ПОДПИСИ:
        return None
    основа, подпись = code[:-_ДЛИНА_ПОДПИСИ], code[-_ДЛИНА_ПОДПИСИ:]
    if not hmac.compare_digest(подпись, _подпись(основа)):
        return None
    разбор = re.fullmatch(r"u(\d+)t(\d+)", основа)
    if not разбор:
        return None
    выдан = datetime.datetime.fromtimestamp(
        int(разбор.group(2)), datetime.timezone.utc
    )
    if now - выдан > LINK_CODE_TTL:
        return None
    return int(разбор.group(1))


# --- Кто нажал (правило 7) -------------------------------------------------


def _пользователь(chat_id):
    """Владелец привязанного чата; чужой чат — None."""
    профиль = (
        NotificationProfile.objects.filter(telegram_chat_id=str(chat_id))
        .select_related("user")
        .first()
    )
    return профиль.user if профиль else None


def _обязательство(пользователь, obligation_id):
    """Обязательство пользователя: своё — владельцу или исполнителю."""
    return (
        Obligation.objects.filter(pk=obligation_id)
        .filter(Q(owner=пользователь) | Q(assignee=пользователь))
        .select_related("meter")
        .first()
    )


def _счётчик(пользователь, meter_id):
    """Счётчик, к которому у пользователя есть обязательство."""
    свои = Obligation.objects.filter(
        Q(owner=пользователь) | Q(assignee=пользователь)
    )
    return (
        Meter.objects.filter(pk=meter_id, obligations__in=свои)
        .select_related("item")
        .distinct()
        .first()
    )


# --- Ожидание ответа на вопрос бота ----------------------------------------


def _спросить(пользователь, вид, completion=None, meter=None, now=None):
    PendingInput.objects.update_or_create(
        user=пользователь,
        defaults={
            "kind": вид,
            "completion": completion,
            "meter": meter,
            "created_at": now or timezone.now(),
        },
    )


def _ожидание(пользователь, now):
    """Незакрытый вопрос бота; просроченный снимается молча."""
    запись = PendingInput.objects.filter(user=пользователь).first()
    if запись is None:
        return None
    if now - запись.created_at > ANSWER_TIMEOUT:
        запись.delete()
        return None
    return запись


def _число(текст: str) -> Decimal | None:
    """Число из ответа: «118 500», «3500,50», «3 500.5» — всё годится."""
    очищено = re.sub(r"[\s ]", "", текст or "").replace(",", ".")
    try:
        значение = Decimal(очищено)
    except (InvalidOperation, ValueError):
        return None
    return значение if значение >= 0 else None


# --- Привязка чата (правило 8) ---------------------------------------------


def link_chat(code, chat_id, now=None) -> str:
    """Привязать чат к учётке по коду из админки."""
    номер = parse_link_code(code, now)
    if номер is None:
        return (
            "Код привязки не подошёл — возможно, устарел. "
            "Откройте ссылку в админке заново."
        )
    пользователь = get_user_model().objects.filter(pk=номер).first()
    if пользователь is None:
        return "Учётка из кода не найдена."
    профиль, _ = NotificationProfile.objects.get_or_create(user=пользователь)
    профиль.telegram_chat_id = str(chat_id)
    профиль.save(update_fields=["telegram_chat_id"])
    return (
        f"Готово, {пользователь}. Напоминания придут сюда — "
        f"с кнопками «Сделано», «Отложить» и «Ввести показание»."
    )


# --- Кнопки (правила 4–6) --------------------------------------------------


def mark_done(chat_id, obligation_id, today=None, now=None) -> str:
    """«Сделано»: записать выполнение и спросить стоимость."""
    пользователь = _пользователь(chat_id)
    if пользователь is None:
        return ОТКАЗ
    обязательство = _обязательство(пользователь, obligation_id)
    if обязательство is None:
        return ЧУЖОЕ
    today = today or timezone.localdate()

    # Показание при выполнении — расчётное на сегодня: пользователь
    # не обязан лезть за точным (правило 4).
    показание = None
    if обязательство.meter:
        оценка = estimate_meter(обязательство.meter, today)
        показание = оценка.value if оценка else None

    выполнение = Completion.objects.create(
        obligation=обязательство,
        date=today,
        meter_value=показание,
        done_by=пользователь,
    )
    # Отметили — начался новый цикл, отложка больше не нужна.
    if обязательство.snoozed_until:
        обязательство.snoozed_until = None
        обязательство.save(update_fields=["snoozed_until"])

    _спросить(
        пользователь, PendingInput.Kind.COST, completion=выполнение, now=now
    )
    ответ = f"«{обязательство.name}» — отмечено {today:%d.%m.%Y}"
    if показание is not None:
        ответ += (
            f", {short_number(показание)} {обязательство.meter.unit} "
            f"(по расчёту)"
        )
    return ответ + ".\nСколько стоило? Пришлите число — или пропустите."


def snooze(chat_id, obligation_id, days, today=None) -> str:
    """«Отложить»: молчать об обязательстве выбранное число дней."""
    пользователь = _пользователь(chat_id)
    if пользователь is None:
        return ОТКАЗ
    обязательство = _обязательство(пользователь, obligation_id)
    if обязательство is None:
        return ЧУЖОЕ
    today = today or timezone.localdate()
    обязательство.snoozed_until = today + datetime.timedelta(days=days)
    обязательство.save(update_fields=["snoozed_until"])
    return (
        f"«{обязательство.name}» — отложено, "
        f"напомню {обязательство.snoozed_until:%d.%m.%Y}."
    )


def ask_reading(chat_id, meter_id, now=None) -> str:
    """«Ввести показание»: спросить число у пользователя."""
    пользователь = _пользователь(chat_id)
    if пользователь is None:
        return ОТКАЗ
    счётчик = _счётчик(пользователь, meter_id)
    if счётчик is None:
        return ЧУЖОЕ
    _спросить(
        пользователь, PendingInput.Kind.READING, meter=счётчик, now=now
    )
    return (
        f"{счётчик.name} ({счётчик.item}): пришлите текущее показание "
        f"числом, в единицах «{счётчик.unit}»."
    )


# --- Ответ сообщением ------------------------------------------------------


def handle_text(chat_id, text, now=None, today=None) -> str:
    """Свободный текст в чате: ответ на заданный ботом вопрос."""
    пользователь = _пользователь(chat_id)
    if пользователь is None:
        return ОТКАЗ
    now = now or timezone.now()
    today = today or timezone.localdate(now)

    ожидание = _ожидание(пользователь, now)
    if ожидание is None:
        return ПОДСКАЗКА
    значение = _число(text)
    if значение is None:
        return "Нужно число — например, 118500. Пришлите ещё раз."

    if ожидание.kind == PendingInput.Kind.READING:
        ответ = _записать_показание(ожидание.meter, значение, today)
    else:
        ответ = _записать_стоимость(ожидание.completion, значение)
    ожидание.delete()
    return ответ


def _записать_показание(счётчик, значение, today) -> str:
    предыдущее = счётчик.readings.order_by("-date", "-id").first()
    MeterReading.objects.create(meter=счётчик, value=значение, date=today)
    ответ = (
        f"Записал: {счётчик.name} — {short_number(значение)} "
        f"{счётчик.unit} на {today:%d.%m.%Y}."
    )
    # Счётчик, который «поехал назад», — обычно опечатка. Не спорим,
    # но предупреждаем: расчёты по нему станут неверными.
    if предыдущее and значение < предыдущее.value:
        ответ += (
            f"\nПрошлое показание было больше "
            f"({short_number(предыдущее.value)} {счётчик.unit}) — "
            f"проверьте, не опечатка ли."
        )
    return ответ


def _записать_стоимость(выполнение, значение) -> str:
    выполнение.cost = значение
    выполнение.save(update_fields=["cost"])
    return f"Стоимость записана: {short_number(значение)}."
