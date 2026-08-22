"""Главный экран — заметка «Главный экран».

Экран ничего не считает сам: состояния и сроки берёт
из `core/status.py`, записи делает через `core/actions.py` —
теми же правилами, что кнопки в Telegram.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from . import actions
from .models import Completion
from .status import (
    State,
    compute_status,
    estimate_meter,
    needs_reading,
    short_number,
)

# Насколько можно отложить дело кнопкой — как в Telegram.
ВАРИАНТЫ_ОТКЛАДЫВАНИЯ = [1, 3, 7]

# Ключ раздела в адресе для дел, у которых раздела нет.
БЕЗ_РАЗДЕЛА = "нет"

ГРУППА_ОБЫЧНЫЕ, ГРУППА_ОТЛОЖЕННЫЕ, ГРУППА_БЕЗ_ДАННЫХ = 0, 1, 2


@dataclass
class Дело:
    """Строка списка: обязательство и всё, что о нём показываем."""

    obligation: object
    status: object
    раздел: object
    ключ_раздела: str
    группа: int
    процент: int
    отложено_до: object = None
    фактура: list = field(default_factory=list)

    @property
    def просрочено(self):
        return self.status.state is State.OVERDUE

    @property
    def метка(self):
        """Что написано на цветной плашке состояния."""
        if self.отложено_до:
            return "отложено"
        return str(self.status.state)

    @property
    def цвет(self):
        """Класс оформления: цвет плашки и полоски."""
        if self.отложено_до:
            return "snoozed"
        return {
            State.OVERDUE: "overdue",
            State.SOON: "soon",
            State.OK: "ok",
        }.get(self.status.state, "nodata")

    @property
    def подпись(self):
        """Вторая строка: раздел, к чему относится, сколько осталось."""
        части = []
        if self.раздел:
            части.append(str(self.раздел))
        предмет = self.obligation.item or self.obligation.person
        if предмет:
            части.append(str(предмет))
        части += _осталось(self.status)
        if self.отложено_до:
            части.append(f"молчу до {self.отложено_до:%d.%m.%Y}")
        return " · ".join(части)


@dataclass
class Плашка:
    """Плашка раздела над списком."""

    ключ: str
    название: str
    количество: int
    просрочено: bool


@dataclass
class СчётчикЭкрана:
    """Строка блока счётчиков: где сейчас и когда вводили."""

    meter: object
    значение: str
    подпись: str
    пора: bool


def _осталось(статус):
    """«осталось 400 км», «просрочено на 3 дн.» — как в напоминаниях."""
    части = []
    if статус.meter_left is not None:
        префикс = "≈ " if статус.is_estimate else ""
        значение = f"{short_number(abs(статус.meter_left))} {статус.meter_unit}"
        части.append(
            f"осталось {префикс}{значение}"
            if статус.meter_left >= 0
            else f"просрочено на {префикс}{значение}"
        )
    if статус.days_left is not None:
        части.append(
            f"осталось {статус.days_left} дн."
            if статус.days_left >= 0
            else f"просрочено на {-статус.days_left} дн."
        )
    return части


def _процент(статус):
    """Пройденная доля интервала в процентах — для полоски."""
    if статус.fraction is None:
        return 0
    return int(min(статус.fraction, Decimal(1)) * 100)


def _дела(user, today):
    """Видимые дела, кроме закрытых, сразу в порядке показа."""
    запрос = (
        actions.visible_obligations(user)
        .select_related(
            "item", "person", "meter", "category", "item__category"
        )
        .prefetch_related("facts")
    )
    дела = []
    for обязательство in запрос:
        статус = compute_status(обязательство, today)
        if статус.state is State.CLOSED:
            continue  # разовое сделано — на экране ему делать нечего
        отложено = (
            обязательство.snoozed_until
            if обязательство.snoozed_until
            and обязательство.snoozed_until > today
            else None
        )
        if статус.state is State.NO_DATA:
            группа = ГРУППА_БЕЗ_ДАННЫХ
        elif отложено:
            группа = ГРУППА_ОТЛОЖЕННЫЕ
        else:
            группа = ГРУППА_ОБЫЧНЫЕ
        раздел = обязательство.effective_category
        дела.append(
            Дело(
                obligation=обязательство,
                status=статус,
                раздел=раздел,
                ключ_раздела=str(раздел.pk) if раздел else БЕЗ_РАЗДЕЛА,
                группа=группа,
                процент=_процент(статус),
                отложено_до=отложено,
                фактура=list(обязательство.facts.all()),
            )
        )
    # Сверху самое горящее: больше пройдено — выше; при равенстве
    # выше тот, кому меньше дней осталось (правило 4).
    дела.sort(
        key=lambda д: (
            д.группа,
            -(д.status.fraction or Decimal(0)),
            д.status.days_left if д.status.days_left is not None else 10**6,
            д.obligation.name,
        )
    )
    return дела


def _плашки(дела):
    """Разделы, в которых что-то есть, плюс «Всё» первой плашкой."""
    счёт = {}
    for дело in дела:
        ключ = дело.ключ_раздела
        всего, просрочено, имя, порядок = счёт.get(
            ключ,
            (
                0,
                False,
                str(дело.раздел) if дело.раздел else "Прочее",
                дело.раздел.order if дело.раздел else 10**6,
            ),
        )
        счёт[ключ] = (всего + 1, просрочено or дело.просрочено, имя, порядок)
    плашки = [
        Плашка(
            ключ="",
            название="Всё",
            количество=len(дела),
            просрочено=any(д.просрочено for д in дела),
        )
    ]
    for ключ, (всего, просрочено, имя, _) in sorted(
        счёт.items(), key=lambda пара: (пара[1][3], пара[1][2])
    ):
        плашки.append(Плашка(ключ, имя, всего, просрочено))
    return плашки


def _счётчики(user, today, выбранный=""):
    """Блок счётчиков: где сейчас, когда вводили, пора ли вводить.

    Выбран раздел — показываем только его счётчики: в «Транспорте»
    счётчику воды делать нечего.
    """
    строки = []
    запрос = actions.visible_meters(user).select_related(
        "item__category"
    ).order_by("item__name", "name")
    for счётчик in запрос:
        if выбранный:
            раздел = счётчик.item.category
            ключ = str(раздел.pk) if раздел else БЕЗ_РАЗДЕЛА
            if ключ != выбранный:
                continue
        оценка = estimate_meter(счётчик, today)
        if оценка is None:
            значение, давность = "показаний нет", "вводов ещё не было"
        else:
            префикс = "≈ " if оценка.is_estimate else ""
            значение = f"{префикс}{short_number(оценка.value)} {счётчик.unit}"
            последнее = счётчик.readings.order_by("-date", "-id").first()
            дней = (today - последнее.date).days
            когда = "сегодня" if дней == 0 else f"{дней} дн. назад"
            давность = f"последний ввод {когда}"
            # Введённое показание дописываем, только если сегодняшняя
            # оценка от него ушла, — иначе одно и то же число дважды.
            if последнее.value != оценка.value:
                давность += (
                    f" · {short_number(последнее.value)} {счётчик.unit}"
                )
        строки.append(
            СчётчикЭкрана(
                meter=счётчик,
                значение=значение,
                подпись=давность,
                пора=needs_reading(счётчик, today),
            )
        )
    return строки


def _назад(request, **хвост):
    """Вернуться на экран, сохранив выбранный раздел.

    Адрес собирается целиком и кодируется: русские названия
    параметров в заголовке перехода браузер иначе не поймёт.
    """
    параметры = {}
    раздел = request.POST.get("раздел") or ""
    if раздел:
        параметры["раздел"] = раздел
    параметры.update(хвост)
    адрес = reverse("home")
    if параметры:
        адрес = f"{адрес}?{urlencode(параметры)}"
    return redirect(адрес)


@login_required
def home(request):
    """Главный экран: дела по срочности, разделы, счётчики."""
    today = timezone.localdate()
    все_дела = _дела(request.user, today)
    плашки = _плашки(все_дела)
    выбранный = request.GET.get("раздел") or ""
    if выбранный and not any(п.ключ == выбранный for п in плашки):
        выбранный = ""  # раздел опустел — показываем всё
    дела = [
        д for д in все_дела if not выбранный or д.ключ_раздела == выбранный
    ]

    # Выполнение, которому ещё можно дописать стоимость (правило 6).
    стоимость_у = None
    номер = request.GET.get("стоимость")
    if номер and номер.isdigit():
        стоимость_у = Completion.objects.filter(
            pk=номер, done_by=request.user, cost__isnull=True
        ).first()

    return render(
        request,
        "core/home.html",
        {
            "дела": дела,
            "плашки": плашки,
            "выбранный": выбранный,
            "счётчики": _счётчики(request.user, today, выбранный),
            "варианты_откладывания": ВАРИАНТЫ_ОТКЛАДЫВАНИЯ,
            "стоимость_у": стоимость_у,
            "пусто": not все_дела,
        },
    )


@require_POST
@login_required
def mark_done(request, pk):
    """Кнопка «Сделано»."""
    обязательство = actions.obligation_for(request.user, pk)
    if обязательство is None:
        messages.error(request, "Это дело не ваше — ничего не изменено.")
        return _назад(request)
    today = timezone.localdate()
    выполнение = actions.complete(обязательство, request.user, today)
    текст = f"«{обязательство.name}» — отмечено {today:%d.%m.%Y}"
    if выполнение.meter_value is not None:
        текст += (
            f", {short_number(выполнение.meter_value)} "
            f"{обязательство.meter.unit} (по расчёту)"
        )
    messages.success(request, текст + ".")
    # Хвост «стоимость» открывает поле «сколько стоило»: вписать
    # можно, а можно и пропустить.
    return _назад(request, стоимость=выполнение.pk)


@require_POST
@login_required
def set_cost(request, pk):
    """Поле «сколько стоило» под зелёной плашкой."""
    выполнение = get_object_or_404(Completion, pk=pk, done_by=request.user)
    значение = actions.parse_number(request.POST.get("значение"))
    if значение is None:
        messages.error(request, "Нужно число — например, 3500.")
    else:
        actions.set_cost(выполнение, значение)
        messages.success(
            request, f"Стоимость записана: {short_number(значение)}."
        )
    return _назад(request)


@require_POST
@login_required
def snooze(request, pk):
    """Кнопки «Отложить» и «Вернуть в список»."""
    обязательство = actions.obligation_for(request.user, pk)
    if обязательство is None:
        messages.error(request, "Это дело не ваше — ничего не изменено.")
        return _назад(request)
    дней = request.POST.get("дней")
    if дней == "0":
        actions.unsnooze(обязательство)
        messages.success(request, f"«{обязательство.name}» — снова в списке.")
        return _назад(request)
    if дней not in [str(в) for в in ВАРИАНТЫ_ОТКЛАДЫВАНИЯ]:
        messages.error(request, "Отложить можно на 1, 3 или 7 дней.")
        return _назад(request)
    до = actions.snooze(обязательство, int(дней), timezone.localdate())
    messages.success(
        request, f"«{обязательство.name}» — отложено, напомню {до:%d.%m.%Y}."
    )
    return _назад(request)


@require_POST
@login_required
def add_reading(request, pk):
    """Ввод показания счётчика в блоке «Счётчики»."""
    счётчик = actions.meter_for(request.user, pk)
    if счётчик is None:
        messages.error(request, "Этот счётчик не ваш — ничего не изменено.")
        return _назад(request)
    значение = actions.parse_number(request.POST.get("значение"))
    if значение is None:
        messages.error(request, "Нужно число — например, 118500.")
        return _назад(request)
    today = timezone.localdate()
    _, подозрительное = actions.add_reading(счётчик, значение, today)
    текст = (
        f"Записал: {счётчик.name} — {short_number(значение)} "
        f"{счётчик.unit} на {today:%d.%m.%Y}."
    )
    if подозрительное:
        текст += (
            f" Прошлое показание было больше "
            f"({short_number(подозрительное.value)} {счётчик.unit}) — "
            f"проверьте, не опечатка ли."
        )
    messages.success(request, текст)
    return _назад(request)
