"""Планировщик — заметка «Планировщик».

Один прогон: проверить все обязательства и счётчики, записать
новые напоминания в журнал уведомлений, отправить готовые через
каналы. Каналов в этой задаче ещё нет (Telegram — следующая),
поэтому уведомления копятся со статусом «ожидает» — их видно
в админке.

Все функции принимают «сейчас» параметром — ради повторяемых
тестов; по умолчанию берётся настоящее время.
"""
import datetime
from dataclasses import dataclass

from django.utils import timezone

from .models import Meter, Notification, NotificationProfile, Obligation
from .status import State, compute_status, needs_reading, short_number

# Повтор «введите показания» — не чаще раза в неделю, как
# и повтор о просроченном по умолчанию (правило 3 заметки).
READING_REPEAT_DAYS = 7

# Уведомление, не ушедшее за сутки после «не раньше», устарело:
# подключив бота, пользователь не должен получить пачку старых
# напоминаний (правило 3 заметки «Telegram-бот»).
STALE_AFTER = datetime.timedelta(days=1)

# Каналы отправки: функции «уведомление → принято?». Telegram
# регистрируется при старте приложения (`core/apps.py`).
_channels = []


def register_channel(channel):
    """Подключить канал отправки (функция: Notification → bool)."""
    if channel not in _channels:
        _channels.append(channel)


@dataclass
class RunResult:
    """Итог прогона: создано, отправлено, помечено устаревшими."""

    created: int
    sent: int
    stale: int = 0


def run(now: datetime.datetime | None = None) -> RunResult:
    """Один прогон планировщика: проверить сроки, отправить готовое."""
    now = now or timezone.now()
    # Сначала устаревание: дальше залежавшиеся уже не мешают
    # ни защите от дублей, ни отправке.
    устарело = _пометить_устаревшие(now)
    создано = _проверить_обязательства(now) + _проверить_счётчики(now)
    отправлено = _отправить_готовые(now)
    return RunResult(создано, отправлено, устарело)


def _пометить_устаревшие(now):
    """Пометить «устарело» всё, что не ушло за сутки после срока."""
    return Notification.objects.filter(
        status=Notification.Status.PENDING,
        not_before__lt=now - STALE_AFTER,
    ).update(status=Notification.Status.STALE)


# --- Тихие часы (правило 7 заметки) ----------------------------------------


def _конец_тихих_часов(user, now):
    """Конец тихих часов получателя, если «сейчас» в них; иначе None.

    Настройки не заведены — действуют умолчания модели
    (22:00–09:00). Совпадающие границы означают «тихих часов нет».
    """
    профиль = (
        getattr(user, "notification_profile", None) or NotificationProfile()
    )
    начало, конец = профиль.quiet_hours_start, профиль.quiet_hours_end
    if начало == конец:
        return None
    местное = timezone.localtime(now)
    сейчас = местное.time()
    if начало < конец:  # окно внутри одних суток
        тихо = начало <= сейчас < конец
        день_конца = местное
    else:  # окно через полночь: вечер сегодня — утро завтра
        тихо = сейчас >= начало or сейчас < конец
        день_конца = (
            местное + datetime.timedelta(days=1)
            if сейчас >= начало
            else местное
        )
    if not тихо:
        return None
    return день_конца.replace(
        hour=конец.hour, minute=конец.minute, second=0, microsecond=0
    )


def _не_раньше(user, now):
    """Когда уведомление можно отправлять: сразу или после тихих часов."""
    return _конец_тихих_часов(user, now) or now


# --- Проверка сроков (правила 1–6 заметки) ---------------------------------


def _получатель(obligation):
    """Кому напоминать: исполнителю, без него — владельцу (правило 5)."""
    return obligation.assignee or obligation.owner


def _создать(now, user, kind, text, obligation=None, meter=None):
    Notification.objects.create(
        user=user,
        kind=kind,
        text=text,
        obligation=obligation,
        meter=meter,
        created_at=now,
        not_before=_не_раньше(user, now),
    )


def _проверить_обязательства(now):
    """Уведомления о «скоро» и «просрочено» — правила 1–2."""
    today = timezone.localdate(now)
    создано = 0
    for обязательство in Obligation.objects.select_related(
        "meter", "assignee", "owner"
    ):
        # Отложено кнопкой в Telegram — молчим до выбранной даты
        # (правило 5 заметки «Telegram-бот»).
        if (
            обязательство.snoozed_until
            and today < обязательство.snoozed_until
        ):
            continue
        статус = compute_status(обязательство, today)
        if статус.state not in (State.SOON, State.OVERDUE):
            continue
        получатель = _получатель(обязательство)
        вид = (
            Notification.Kind.SOON
            if статус.state is State.SOON
            else Notification.Kind.OVERDUE
        )
        # Устаревшие в расчёт не идут: их никто не увидел, значит
        # напомнить надо заново.
        последнее = (
            обязательство.notifications.filter(user=получатель, kind=вид)
            .exclude(status=Notification.Status.STALE)
            .order_by("-created_at")
            .first()
        )
        в_цикле = (
            последнее is not None
            and статус.cycle_start is not None
            and timezone.localdate(последнее.created_at) > статус.cycle_start
        )
        if вид == Notification.Kind.SOON:
            if в_цикле:  # одно «скоро» на цикл (правило 1)
                continue
        else:  # повтор о просроченном не чаще заданного (правило 2)
            if (
                в_цикле
                and (today - timezone.localdate(последнее.created_at)).days
                < обязательство.overdue_repeat_days
            ):
                continue
        _создать(
            now,
            получатель,
            вид,
            _текст(обязательство, статус),
            obligation=обязательство,
        )
        создано += 1
    return создано


def _проверить_счётчики(now):
    """Напоминания «введите показания» — правила 3 и 6."""
    today = timezone.localdate(now)
    создано = 0
    # Счётчик без обязательств не напоминает — незачем (правило 3).
    for счётчик in Meter.objects.filter(obligations__isnull=False).distinct():
        if not needs_reading(счётчик, today):
            continue
        получатели = {
            _получатель(о)
            for о in счётчик.obligations.select_related("assignee", "owner")
        }
        for получатель in получатели:
            последнее = (
                счётчик.notifications.filter(
                    user=получатель, kind=Notification.Kind.READING
                )
                .exclude(status=Notification.Status.STALE)
                .order_by("-created_at")
                .first()
            )
            if (
                последнее is not None
                and (today - timezone.localdate(последнее.created_at)).days
                < READING_REPEAT_DAYS
            ):
                continue
            _создать(
                now,
                получатель,
                Notification.Kind.READING,
                _текст_показаний(счётчик, today),
                meter=счётчик,
            )
            создано += 1
    return создано


# --- Тексты напоминаний ----------------------------------------------------


def _текст(обязательство, статус):
    """Текст напоминания; по оценке — просьба сверить (правило 4)."""
    скоро = статус.state is State.SOON
    if статус.is_estimate and обязательство.meter:
        голова = (
            f"{обязательство.name}: по расчёту "
            + ("подходит срок" if скоро else "срок прошёл")
            + f" — проверьте {обязательство.meter.name.lower()}"
        )
    else:
        голова = обязательство.name + (
            ": скоро срок" if скоро else ": срок прошёл"
        )
    части = []
    if статус.meter_left is not None:
        значение = (
            f"{short_number(abs(статус.meter_left))} {статус.meter_unit}"
        )
        части.append(
            f"осталось {значение}"
            if статус.meter_left >= 0
            else f"просрочено на {значение}"
        )
    if статус.days_left is not None:
        части.append(
            f"осталось {статус.days_left} дн."
            if статус.days_left >= 0
            else f"просрочено на {-статус.days_left} дн."
        )
    return голова + (f" ({'; '.join(части)})" if части else "")


def _текст_показаний(счётчик, today):
    последнее = счётчик.readings.order_by("-date", "-id").first()
    давность = (
        f"последний ввод {(today - последнее.date).days} дн. назад"
        if последнее
        else "вводов ещё не было"
    )
    return f"{счётчик.name} ({счётчик.item}): введите показания — {давность}"


# --- Отправка (правила 7 и 9 заметки) --------------------------------------


def _отправить_готовые(now):
    """Отдать каналам уведомления, которым пришло время."""
    отправлено = 0
    ожидающие = Notification.objects.filter(
        status=Notification.Status.PENDING, not_before__lte=now
    ).select_related("user")
    for уведомление in ожидающие:
        # Планового прогона в тихие часы не бывает, но ручной
        # (manage.py check_due) возможен — отправку держим до утра.
        if _конец_тихих_часов(уведомление.user, now):
            continue
        for канал in _channels:
            try:
                принято = канал(уведомление)
            except Exception:
                # Сбой канала не роняет прогон: уведомление
                # останется «ожидает» и уйдёт следующим прогоном.
                принято = False
            if принято:
                уведомление.status = Notification.Status.SENT
                уведомление.sent_at = now
                уведомление.save(update_fields=["status", "sent_at"])
                отправлено += 1
                break
    return отправлено
