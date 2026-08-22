"""Расчёт сроков и состояний — заметка «Расчёт сроков и состояний».

Ничего не сохраняет в базе: состояние, сроки и расчётные
показания вычисляются на лету, поэтому всегда свежие. Модуль —
фундамент планировщика, Telegram-бота и главного экрана.
"""
import calendar
import datetime
import enum
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .models import Meter, Obligation


class State(enum.Enum):
    """Состояние обязательства."""

    OK = "в норме"
    SOON = "скоро"
    OVERDUE = "просрочено"
    CLOSED = "закрыто"  # разовое с отмеченным выполнением
    NO_DATA = "нет данных"  # не от чего отсчитывать срок

    def __str__(self):
        return self.value


# Пробел между тысячами — неразрывный: «119 600 км» не должно
# переноситься по строкам ни на экране, ни в Telegram.
ПРОБЕЛ_ТЫСЯЧ = " "


def short_number(value) -> str:
    """Число без хвостовых нулей и с пробелом между тысячами.

    400.00 → «400», 12.50 → «12.5», 119600 → «119 600». Общий вид
    для экрана, админки, напоминаний и ответов бота — чтобы
    пользователь везде видел числа одинаково.
    """
    текст = f"{value:.2f}".rstrip("0").rstrip(".")
    знак = "-" if текст.startswith("-") else ""
    целая, _, дробная = текст.lstrip("-").partition(".")
    целая = f"{int(целая):,}".replace(",", ПРОБЕЛ_ТЫСЯЧ)
    return знак + целая + (f".{дробная}" if дробная else "")


# --- Календарная арифметика ------------------------------------------------


def _add_months(day: datetime.date, months: int) -> datetime.date:
    """Сдвиг на месяцы: 31 января + месяц = 28/29 февраля."""
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return datetime.date(
        year, month, min(day.day, calendar.monthrange(year, month)[1])
    )


def _add_interval(
    start: datetime.date, value: int, unit: str
) -> datetime.date:
    """Дата «через N дней/недель/месяцев/лет» от точки отсчёта."""
    единицы = Obligation.IntervalUnit
    if unit == единицы.DAYS:
        return start + datetime.timedelta(days=value)
    if unit == единицы.WEEKS:
        return start + datetime.timedelta(weeks=value)
    if unit == единицы.MONTHS:
        return _add_months(start, value)
    if unit == единицы.YEARS:
        return _add_months(start, value * 12)
    raise ValueError(f"Неизвестная единица интервала: {unit}")


def _anniversary(year: int, month: int, day: int) -> datetime.date:
    """Годовщина в заданном году; 29 февраля в невисокосный год —
    28 февраля (правило 12)."""
    return datetime.date(
        year, month, min(day, calendar.monthrange(year, month)[1])
    )


# --- Расчётное значение счётчика (правила 1–5) -----------------------------


@dataclass
class MeterEstimate:
    """Значение счётчика на дату: точное или расчётная оценка."""

    value: Decimal
    is_estimate: bool  # расчёт, а не ввод этого дня (правило 4)
    insufficient_data: bool  # темп посчитать не из чего (правило 3)


def estimate_meter(
    meter: Meter, on_date: datetime.date | None = None
) -> MeterEstimate | None:
    """Значение счётчика на дату (по умолчанию — сегодня).

    Темп — по двум последним показаниям; одно показание — по
    ожидаемому годовому расходу; нет и его — последнее показание
    как есть, с пометкой «мало данных». Показаний нет вообще —
    None. Оценка не бывает меньше последнего ввода (правило 5):
    отрицательный темп не экстраполируется.
    """
    on_date = on_date or datetime.date.today()
    readings = list(
        meter.readings.filter(date__lte=on_date).order_by("-date", "-id")[:2]
    )
    if not readings:
        # До этой даты вводов не было; для оценки задним числом
        # (правило 6) берём самый ранний ввод как есть.
        earliest = meter.readings.order_by("date", "id").first()
        if earliest is None:
            return None
        return MeterEstimate(earliest.value, True, True)

    last = readings[0]
    days_passed = (on_date - last.date).days
    if days_passed == 0:
        return MeterEstimate(last.value, False, False)

    rate = None
    if len(readings) == 2:
        span = (last.date - readings[1].date).days
        diff = last.value - readings[1].value
        if span > 0 and diff >= 0:
            rate = diff / span
    if rate is None and meter.expected_yearly_usage:
        rate = meter.expected_yearly_usage / Decimal(365)
    if rate is None:
        return MeterEstimate(last.value, True, True)

    value = (last.value + rate * days_passed).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return MeterEstimate(value, True, False)


def needs_reading(
    meter: Meter, on_date: datetime.date | None = None
) -> bool:
    """Пора ли напоминать ввести показания (правило 13)."""
    on_date = on_date or datetime.date.today()
    last = meter.readings.order_by("-date", "-id").first()
    if last is None:
        return True
    return (on_date - last.date).days > meter.reading_reminder_days


# --- Состояние обязательства (правила 6–12) --------------------------------


@dataclass
class ObligationStatus:
    """Ответ на три вопроса: когда срок, сколько осталось, что с ним."""

    state: State
    due_date: datetime.date | None = None  # ближайший срок по времени
    days_left: int | None = None
    due_meter_value: Decimal | None = None  # порог по счётчику
    meter_left: Decimal | None = None
    meter_unit: str | None = None
    fraction: Decimal | None = None  # пройденная доля интервала
    is_estimate: bool = False  # счётчиковая часть — по оценке
    needs_reading: bool = False
    # Начало текущего цикла (последнее выполнение, годовщина…) —
    # по нему планировщик понимает, напоминал ли уже в этом цикле.
    cycle_start: datetime.date | None = None
    message: str = ""


def _time_part(obligation, last_completion, today):
    """Точки отсчёта и срока временно́й части правила.

    Возвращает (start, due) либо State, если считать нечего.
    """
    вид = Obligation.TimeKind
    if obligation.time_kind == вид.ONCE:
        if last_completion is not None:
            return State.CLOSED  # правило 10
        return obligation.created, obligation.due_date

    if obligation.time_kind == вид.INTERVAL:
        if last_completion is None:
            return State.NO_DATA  # правило 7: гадать не будем
        start = last_completion.date
        return start, _add_interval(
            start, obligation.interval_value, obligation.interval_unit
        )

    # Ежегодная дата. prev — последняя годовщина не позже сегодня.
    месяц, день = obligation.annual_month, obligation.annual_day
    prev = _anniversary(today.year, месяц, день)
    if prev > today:
        prev = _anniversary(today.year - 1, месяц, день)
    следующая = _anniversary(prev.year + 1, месяц, день)

    выполнено_в_цикле = (
        last_completion is not None and last_completion.date >= prev
    )
    # Заведено после прошедшей годовщины — отметки и не могло быть,
    # цикл начинается с ближайшей будущей даты (правило 7).
    if выполнено_в_цикле or obligation.created > prev:
        return prev, следующая
    return _anniversary(prev.year - 1, месяц, день), prev


def _fraction(start, due, today) -> Decimal:
    """Пройденная доля интервала времени, 0…∞."""
    total = (due - start).days
    if total <= 0:
        return Decimal(1) if today >= due else Decimal(0)
    return Decimal((today - start).days) / Decimal(total)


def compute_status(
    obligation: Obligation, today: datetime.date | None = None
) -> ObligationStatus:
    """Состояние обязательства на дату (по умолчанию — сегодня)."""
    today = today or datetime.date.today()
    вид = Obligation.RuleKind
    нужно_время = obligation.rule_kind in (вид.TIME, вид.BOTH)
    нужен_счётчик = obligation.rule_kind in (вид.METER, вид.BOTH)

    last = obligation.completions.order_by("-date", "-id").first()
    итог = ObligationStatus(state=State.NO_DATA)
    доли = []
    заметки = []

    if нужно_время:
        часть = _time_part(obligation, last, today)
        if часть is State.CLOSED:
            return ObligationStatus(state=State.CLOSED)
        if часть is State.NO_DATA:
            заметки.append("отметьте, когда делалось в последний раз")
        else:
            start, due = часть
            итог.cycle_start = start
            итог.due_date = due
            итог.days_left = (due - today).days
            доли.append(_fraction(start, due, today))

    if нужен_счётчик:
        итог.meter_unit = obligation.meter.unit
        итог.needs_reading = needs_reading(obligation.meter, today)
        if last is None:
            заметки.append("отметьте, когда делалось в последний раз")
        else:
            if итог.cycle_start is None:
                итог.cycle_start = last.date
            # Точка отсчёта — показание при выполнении; не записано —
            # оценка на ту дату (правило 6).
            основа = last.meter_value
            if основа is None:
                оценка_тогда = estimate_meter(obligation.meter, last.date)
                основа = оценка_тогда.value if оценка_тогда else None
            текущее = estimate_meter(obligation.meter, today)
            if основа is None or текущее is None:
                заметки.append("введите показания счётчика")
            else:
                итог.due_meter_value = основа + obligation.meter_interval
                итог.meter_left = итог.due_meter_value - текущее.value
                итог.is_estimate = текущее.is_estimate
                if текущее.insufficient_data:
                    заметки.append("мало данных — введите показания")
                доли.append(
                    (текущее.value - основа) / obligation.meter_interval
                )

    итог.message = "; ".join(dict.fromkeys(заметки))
    if not доли:
        return итог  # ни одну часть посчитать не из чего

    # Правило 9: действует наибольшая доля — срок, наступающий раньше.
    итог.fraction = max(доли)
    порог = Decimal(obligation.soon_threshold_percent) / 100
    if итог.fraction >= 1:
        итог.state = State.OVERDUE
    elif итог.fraction >= порог:
        итог.state = State.SOON
    else:
        итог.state = State.OK
    return итог
