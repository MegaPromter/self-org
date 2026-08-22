"""Экраны — заметки «Главный экран» и «Разделы на главной».

Три страницы: главная (плитки разделов + самое срочное), полный
список по срочности и страница раздела. Экран ничего не считает
сам: состояния и сроки берёт из `core/status.py`, записи делает
через `core/actions.py` — теми же правилами, что кнопки
в Telegram.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.http import require_POST

from . import actions
from .forms import (
    CategoryForm,
    CompletionForm,
    FactForm,
    ItemForm,
    MeterForm,
    MeterReadingForm,
    ObligationForm,
    PersonForm,
)
from .models import (
    Category,
    Completion,
    Fact,
    Item,
    Meter,
    MeterReading,
    Obligation,
    Person,
)
from .status import (
    State,
    compute_status,
    estimate_meter,
    needs_reading,
    short_number,
)

# Насколько можно отложить дело кнопкой — как в Telegram.
ВАРИАНТЫ_ОТКЛАДЫВАНИЯ = [1, 3, 7]

# Ключ в адресе для дел, у которых раздела нет.
БЕЗ_РАЗДЕЛА = "none"

# Сколько спокойных дел показывать на главной вдобавок к горящим —
# чтобы видеть, что надвигается (правило 3 заметки «Разделы»).
БЛИЖАЙШИХ_НА_ГЛАВНОЙ = 3

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
        return self.status.state is State.OVERDUE and not self.отложено_до

    @property
    def скоро(self):
        return self.status.state is State.SOON and not self.отложено_до

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

    def подпись(self, с_разделом=True):
        """Вторая строка: раздел, к чему относится, сколько осталось.

        На странице раздела название раздела не повторяем — оно
        и так в заголовке.
        """
        части = []
        if с_разделом and self.раздел:
            части.append(str(self.раздел))
        предмет = self.obligation.item or self.obligation.person
        if предмет:
            части.append(str(предмет))
        части += _осталось(self.status)
        if self.отложено_до:
            части.append(f"молчу до {self.отложено_до:%d.%m.%Y}")
        return " · ".join(части)

    @property
    def подпись_полная(self):
        return self.подпись()

    @property
    def подпись_без_раздела(self):
        return self.подпись(с_разделом=False)


@dataclass
class Плитка:
    """Пункт меню (он же плитка на телефоне): раздел и его счёт."""

    ключ: str
    название: str
    просрочено: int
    скоро: int
    всего: int
    адрес: str = ""
    активен: bool = False

    @property
    def пусто(self):
        return self.всего == 0

    @property
    def горит(self):
        return self.просрочено + self.скоро

    @property
    def цвет(self):
        if self.просрочено:
            return "overdue"
        if self.скоро:
            return "soon"
        return "ok" if self.всего else "empty"

    @property
    def счёт(self):
        """«1 просрочено · 1 скоро» — или «всё спокойно»."""
        части = []
        if self.просрочено:
            части.append(f"{self.просрочено} просрочено")
        if self.скоро:
            части.append(f"{self.скоро} скоро")
        if части:
            return " · ".join(части)
        return "всё спокойно" if self.всего else "дел нет"

    @property
    def счёт_коротко(self):
        """Для узкой колонки меню: «1 · 6» или «6», пусто — прочерк."""
        if not self.всего:
            return "—"
        return f"{self.горит} · {self.всего}" if self.горит else str(self.всего)


@dataclass
class Сводка:
    """Полоса чисел над содержимым: что горит и когда ближайший срок."""

    просрочено: int
    скоро: int
    всего: int
    ближайший: object = None
    сегодня: object = None

    @property
    def ближайший_текст(self):
        """«14 сентября», а для другого года — с годом: «22 августа 2027».

        Без года дата из будущего года читается как завтрашняя.
        """
        if self.ближайший is None:
            return "—"
        тот_же_год = (
            self.сегодня is not None
            and self.ближайший.year == self.сегодня.year
        )
        return date_format(self.ближайший, "j E" if тот_же_год else "j E Y")


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


def _ключ(раздел):
    """Как раздел выглядит в адресе страницы."""
    return str(раздел.pk) if раздел else БЕЗ_РАЗДЕЛА


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
                ключ_раздела=_ключ(раздел),
                группа=группа,
                процент=_процент(статус),
                отложено_до=отложено,
                фактура=list(обязательство.facts.all()),
            )
        )
    # Сверху самое горящее: больше пройдено — выше; при равенстве
    # выше тот, кому меньше дней осталось.
    дела.sort(
        key=lambda д: (
            д.группа,
            -(д.status.fraction or Decimal(0)),
            д.status.days_left if д.status.days_left is not None else 10**6,
            д.obligation.name,
        )
    )
    return дела


def _плитки(дела):
    """Плитки всех разделов справочника плюс «Прочее», если нужно.

    Пустые разделы показываются серыми: пользователь видит всю
    свою структуру, даже пока дел в ней нет (правило 1).
    """
    счёт = {}
    for дело in дела:
        запись = счёт.setdefault(дело.ключ_раздела, [0, 0, 0])
        запись[2] += 1
        if дело.просрочено:
            запись[0] += 1
        elif дело.скоро:
            запись[1] += 1

    плитки = []
    for раздел in Category.objects.all():
        просрочено, скоро, всего = счёт.get(str(раздел.pk), (0, 0, 0))
        плитки.append(
            Плитка(str(раздел.pk), раздел.name, просрочено, скоро, всего)
        )
    if БЕЗ_РАЗДЕЛА in счёт:
        просрочено, скоро, всего = счёт[БЕЗ_РАЗДЕЛА]
        плитки.append(
            Плитка(БЕЗ_РАЗДЕЛА, "Прочее", просрочено, скоро, всего)
        )
    return плитки


def _сводка(дела, today):
    """Числа над содержимым: сколько горит и ближайший срок."""
    сроки = [
        д.status.due_date
        for д in дела
        if д.status.due_date and д.status.due_date >= today
    ]
    return Сводка(
        просрочено=sum(1 for д in дела if д.просрочено),
        скоро=sum(1 for д in дела if д.скоро),
        всего=len(дела),
        ближайший=min(сроки) if сроки else None,
        сегодня=today,
    )


def _меню(все_дела, активный):
    """Пункты меню: срочное, все дела и разделы со счётом.

    То же меню на телефоне показывается плитками сверху —
    разметка одна, вид разный (заметка «Десктопный макет»).
    """
    пункты = [
        Плитка(
            ключ="срочное",
            название="Самое срочное",
            просрочено=0,
            скоро=0,
            всего=0,
            адрес=reverse("home"),
            активен=активный == "срочное",
        ),
        Плитка(
            ключ="все",
            название="Все дела",
            просрочено=sum(1 for д in все_дела if д.просрочено),
            скоро=sum(1 for д in все_дела if д.скоро),
            всего=len(все_дела),
            адрес=reverse("all_tasks"),
            активен=активный == "все",
        ),
    ]
    пункты += [
        Плитка(
            п.ключ,
            п.название,
            п.просрочено,
            п.скоро,
            п.всего,
            адрес=reverse("section", args=[п.ключ]),
            активен=активный == п.ключ,
        )
        for п in _плитки(все_дела)
    ]
    return пункты


def _счётчики(user, today, ключ=None):
    """Блок счётчиков: где сейчас, когда вводили, пора ли вводить.

    Задан раздел — только его счётчики: в «Транспорте» счётчику
    воды делать нечего.
    """
    строки = []
    запрос = (
        actions.visible_meters(user)
        .select_related("item__category")
        .order_by("item__name", "name")
    )
    for счётчик in запрос:
        if ключ is not None and _ключ(счётчик.item.category) != ключ:
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


def _стоимость_у(request):
    """Выполнение, которому ещё предлагаем дописать стоимость."""
    номер = request.GET.get("стоимость")
    if not (номер and номер.isdigit()):
        return None
    return Completion.objects.filter(
        pk=номер, done_by=request.user, cost__isnull=True
    ).first()


def _назад(request, **хвост):
    """Вернуться туда, где нажали кнопку (правило 6).

    Адрес приходит скрытым полем формы; чужие адреса не берём —
    возвращаемся на главную.
    """
    адрес = request.POST.get("назад") or ""
    if not url_has_allowed_host_and_scheme(
        адрес, allowed_hosts={request.get_host()}, require_https=False
    ):
        адрес = reverse("home")
    if хвост:
        разделитель = "&" if "?" in адрес else "?"
        адрес = f"{адрес}{разделитель}{urlencode(хвост)}"
    return redirect(адрес)


def _каркас(request, все_дела, показанные, today, активный, ключ=None):
    """Общая обвязка страницы: меню, сводка, счётчики, адрес возврата."""
    return {
        "меню": _меню(все_дела, активный),
        "сводка": _сводка(показанные, today),
        "счётчики": _счётчики(request.user, today, ключ),
        "варианты_откладывания": ВАРИАНТЫ_ОТКЛАДЫВАНИЯ,
        "стоимость_у": _стоимость_у(request),
        "назад": request.get_full_path(),
    }


@login_required
def home(request):
    """Главная: самое срочное плюс несколько ближайших дел."""
    today = timezone.localdate()
    все_дела = _дела(request.user, today)
    горящие = [д for д in все_дела if д.просрочено or д.скоро]
    спокойные = [
        д
        for д in все_дела
        if д.группа == ГРУППА_ОБЫЧНЫЕ and not (д.просрочено or д.скоро)
    ]
    контекст = _каркас(request, все_дела, все_дела, today, "срочное")
    контекст.update(
        {
            "дела": горящие + спокойные[:БЛИЖАЙШИХ_НА_ГЛАВНОЙ],
            "всего_дел": len(все_дела),
            "пусто": not все_дела,
        }
    )
    return render(request, "core/home.html", контекст)


@login_required
def all_tasks(request):
    """Полный список по срочности — со счётчиками, как было."""
    today = timezone.localdate()
    дела = _дела(request.user, today)
    контекст = _каркас(request, дела, дела, today, "все")
    контекст.update({"дела": дела, "пусто": not дела})
    return render(request, "core/all.html", контекст)


@login_required
def section(request, key):
    """Страница раздела: все его дела по срочности и его счётчики."""
    today = timezone.localdate()
    if key == БЕЗ_РАЗДЕЛА:
        название = "Прочее"
    else:
        if not key.isdigit():
            raise Http404
        название = get_object_or_404(Category, pk=key).name
    все_дела = _дела(request.user, today)
    дела = [д for д in все_дела if д.ключ_раздела == key]
    контекст = _каркас(request, все_дела, дела, today, key, ключ=key)
    контекст.update({"название": название, "дела": дела})
    return render(request, "core/section.html", контекст)


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


# --- Заведение и правка (заметка «Заведение данных без админки») ------------


def _вернуться(request, по_умолчанию=None):
    """Куда уйти после сохранения: откуда пришли, иначе — на главную."""
    адрес = request.POST.get("назад") or request.GET.get("назад") or ""
    if not url_has_allowed_host_and_scheme(
        адрес, allowed_hosts={request.get_host()}, require_https=False
    ):
        адрес = по_умолчанию or reverse("home")
    return redirect(адрес)


def _страница_формы(request, форма, заголовок, назад, удаление=None):
    """Общая обвязка страницы с формой: меню, сводка, кнопки."""
    today = timezone.localdate()
    все_дела = _дела(request.user, today)
    контекст = _каркас(request, все_дела, все_дела, today, активный=None)
    контекст.update(
        {
            "форма": форма,
            "заголовок": заголовок,
            "назад": назад,
            "адрес_удаления": удаление,
        }
    )
    return render(request, "core/form.html", контекст)


@login_required
def obligation_new(request):
    """Новое дело: форма с созданием предмета и счётчика на лету."""
    назад = request.GET.get("назад") or reverse("home")
    начальные = {}
    раздел = request.GET.get("раздел")
    if раздел and раздел.isdigit():
        начальные["category"] = раздел
    if request.method == "POST":
        форма = ObligationForm(request.POST, user=request.user)
        if форма.is_valid():
            дело = форма.save()
            messages.success(request, f"«{дело.name}» — дело заведено.")
            return _вернуться(request, reverse("obligation", args=[дело.pk]))
    else:
        форма = ObligationForm(initial=начальные, user=request.user)
    return _страница_формы(request, форма, "Новое дело", назад)


@login_required
def obligation_edit(request, pk):
    """Правка дела."""
    дело = actions.obligation_for(request.user, pk)
    if дело is None:
        raise Http404
    назад = request.GET.get("назад") or reverse("obligation", args=[дело.pk])
    if request.method == "POST":
        форма = ObligationForm(request.POST, instance=дело, user=request.user)
        if форма.is_valid():
            форма.save()
            messages.success(request, f"«{дело.name}» — изменения сохранены.")
            return _вернуться(request, reverse("obligation", args=[дело.pk]))
    else:
        форма = ObligationForm(instance=дело, user=request.user)
    return _страница_формы(
        request,
        форма,
        f"Правка: {дело.name}",
        назад,
        удаление=reverse("obligation_delete", args=[дело.pk]),
    )


@login_required
def obligation_detail(request, pk):
    """Карточка дела: состояние, фактура, история выполнений."""
    обязательство = actions.obligation_for(request.user, pk)
    if обязательство is None:
        raise Http404
    today = timezone.localdate()
    все_дела = _дела(request.user, today)
    дело = next(
        (д for д in все_дела if д.obligation.pk == обязательство.pk), None
    )
    контекст = _каркас(
        request,
        все_дела,
        все_дела,
        today,
        активный=(дело.ключ_раздела if дело else None),
    )
    контекст.update(
        {
            "дело": дело,
            "обязательство": обязательство,
            "выполнения": обязательство.completions.select_related("done_by"),
            "форма_выполнения": CompletionForm(
                initial={"date": today}, prefix="в"
            ),
            "фактура": обязательство.facts.all(),
            "форма_фактуры": FactForm(prefix="ф"),
            "вид_хозяина": "obligation",
            "номер_хозяина": обязательство.pk,
            "статус": compute_status(обязательство, today),
        }
    )
    return render(request, "core/obligation.html", контекст)


@require_POST
@login_required
def obligation_delete(request, pk):
    """Удаление дела — только после подтверждения на карточке."""
    дело = actions.obligation_for(request.user, pk)
    if дело is None:
        raise Http404
    имя = дело.name
    дело.delete()
    messages.success(request, f"«{имя}» — удалено вместе с историей.")
    return redirect("home")


@require_POST
@login_required
def completion_add(request, pk):
    """Отметить выполнение задним числом (форма на карточке дела)."""
    дело = actions.obligation_for(request.user, pk)
    if дело is None:
        raise Http404
    форма = CompletionForm(request.POST, prefix="в")
    if not форма.is_valid():
        messages.error(request, "Проверьте дату и числа — запись не сделана.")
        return redirect("obligation", pk=pk)
    выполнение = форма.save(commit=False)
    выполнение.obligation = дело
    выполнение.done_by = request.user
    выполнение.save()
    messages.success(
        request, f"Записано: сделано {выполнение.date:%d.%m.%Y}."
    )
    return redirect("obligation", pk=pk)


@require_POST
@login_required
def completion_delete(request, pk):
    """Удалить ошибочную отметку выполнения."""
    выполнение = get_object_or_404(Completion, pk=pk)
    дело = actions.obligation_for(request.user, выполнение.obligation_id)
    if дело is None:
        raise Http404
    выполнение.delete()
    messages.success(request, "Отметка удалена, сроки пересчитаны.")
    return redirect("obligation", pk=дело.pk)


# --- Справочники: предметы, люди, счётчики, разделы, фактура ----------------
#
# Заход 2 заметки «Заведение данных без админки»: всё, что раньше
# заводилось только в админке, теперь имеет свой экран.


@login_required
def catalog(request):
    """Справочники одной страницей: предметы, люди, счётчики, разделы."""
    today = timezone.localdate()
    все_дела = _дела(request.user, today)
    контекст = _каркас(request, все_дела, все_дела, today, активный=None)
    контекст.update(
        {
            "предметы": Item.objects.select_related("category").prefetch_related(
                "meters"
            ),
            "люди": Person.objects.all(),
            "счётчики_справочника": Meter.objects.select_related("item"),
            "разделы": Category.objects.all(),
        }
    )
    return render(request, "core/catalog.html", контекст)


def _страница_справочника(request, шаблон, данные):
    """Страница справочника в общем каркасе (меню, сводка, счётчики)."""
    today = timezone.localdate()
    все_дела = _дела(request.user, today)
    контекст = _каркас(request, все_дела, все_дела, today, активный=None)
    контекст.update(данные)
    return render(request, шаблон, контекст)


def _создать_или_поправить(request, модель, класс_формы, заголовок, pk=None):
    """Общая страница «создать/править» для простых справочников."""
    объект = get_object_or_404(модель, pk=pk) if pk else None
    назад = request.GET.get("назад") or reverse("catalog")
    if request.method == "POST":
        форма = класс_формы(request.POST, instance=объект)
        if форма.is_valid():
            запись = форма.save()
            messages.success(request, f"«{запись}» — сохранено.")
            return _вернуться(request, назад)
    else:
        форма = класс_формы(instance=объект, initial=dict(request.GET.items()))
    удаление = None
    if объект is not None:
        удаление = reverse(
            модель._meta.model_name + "_delete", args=[объект.pk]
        )
    return _страница_справочника(
        request,
        "core/form_simple.html",
        {
            "форма": форма,
            "заголовок": заголовок if объект is None else f"Правка: {объект}",
            "назад": назад,
            "адрес_удаления": удаление,
        },
    )


@login_required
def item_new(request):
    return _создать_или_поправить(request, Item, ItemForm, "Новый предмет")


@login_required
def item_edit(request, pk):
    return _создать_или_поправить(request, Item, ItemForm, "", pk)


@login_required
def person_new(request):
    return _создать_или_поправить(request, Person, PersonForm, "Новый человек")


@login_required
def person_edit(request, pk):
    return _создать_или_поправить(request, Person, PersonForm, "", pk)


@login_required
def meter_new(request):
    return _создать_или_поправить(request, Meter, MeterForm, "Новый счётчик")


@login_required
def meter_edit(request, pk):
    return _создать_или_поправить(request, Meter, MeterForm, "", pk)


@login_required
def category_new(request):
    return _создать_или_поправить(
        request, Category, CategoryForm, "Новый раздел"
    )


@login_required
def category_edit(request, pk):
    return _создать_или_поправить(request, Category, CategoryForm, "", pk)


@login_required
def item_detail(request, pk):
    """Предмет: его счётчики, дела и фактура."""
    предмет = get_object_or_404(Item, pk=pk)
    return _страница_справочника(
        request,
        "core/item.html",
        {
            "предмет": предмет,
            "счётчики_предмета": предмет.meters.all(),
            "дела_предмета": actions.visible_obligations(request.user).filter(
                item=предмет
            ),
            "фактура": предмет.facts.all(),
            "форма_фактуры": FactForm(prefix="ф"),
            "вид_хозяина": "item",
            "номер_хозяина": предмет.pk,
        },
    )


@login_required
def person_detail(request, pk):
    """Человек: его дела и фактура."""
    человек = get_object_or_404(Person, pk=pk)
    return _страница_справочника(
        request,
        "core/person.html",
        {
            "человек": человек,
            "дела_человека": actions.visible_obligations(request.user).filter(
                person=человек
            ),
            "фактура": человек.facts.all(),
            "форма_фактуры": FactForm(prefix="ф"),
            "вид_хозяина": "person",
            "номер_хозяина": человек.pk,
        },
    )


@login_required
def meter_detail(request, pk):
    """Счётчик: история показаний и что от него зависит."""
    счётчик = get_object_or_404(Meter.objects.select_related("item"), pk=pk)
    today = timezone.localdate()
    оценка = estimate_meter(счётчик, today)
    return _страница_справочника(
        request,
        "core/meter.html",
        {
            "счётчик": счётчик,
            "показания": счётчик.readings.all(),
            "оценка_значение": (
                f"{short_number(оценка.value)} {счётчик.unit}"
                if оценка
                else "показаний нет"
            ),
            "по_оценке": оценка.is_estimate if оценка else False,
            "дела_счётчика": actions.visible_obligations(request.user).filter(
                meter=счётчик
            ),
            "форма_показания": MeterReadingForm(
                initial={"date": today}, prefix="п"
            ),
        },
    )


@require_POST
@login_required
def reading_add_dated(request, pk):
    """Добавить показание с датой — в том числе задним числом."""
    счётчик = get_object_or_404(Meter, pk=pk)
    форма = MeterReadingForm(request.POST, prefix="п")
    if форма.is_valid():
        показание = форма.save(commit=False)
        показание.meter = счётчик
        показание.save()
        messages.success(request, f"Записано: {показание}.")
    else:
        messages.error(request, "Проверьте число и дату — не записано.")
    return redirect("meter", pk=счётчик.pk)


@require_POST
@login_required
def reading_delete(request, pk):
    """Удалить ошибочное показание."""
    показание = get_object_or_404(MeterReading, pk=pk)
    номер_счётчика = показание.meter_id
    показание.delete()
    messages.success(request, "Показание удалено, расчёты пересчитаны.")
    return redirect("meter", pk=номер_счётчика)


def _удалить(request, модель, pk, куда, что_мешает=""):
    """Удаление записи справочника с понятным отказом при защите."""
    объект = get_object_or_404(модель, pk=pk)
    имя = str(объект)
    try:
        объект.delete()
    except ProtectedError:
        messages.error(
            request,
            f"«{имя}» не удалить: {что_мешает}. Сначала уберите их "
            f"или переведите на другую запись.",
        )
        return redirect(куда)
    messages.success(request, f"«{имя}» — удалено.")
    return redirect(куда)


@require_POST
@login_required
def item_delete(request, pk):
    return _удалить(request, Item, pk, reverse("catalog"))


@require_POST
@login_required
def person_delete(request, pk):
    return _удалить(request, Person, pk, reverse("catalog"))


@require_POST
@login_required
def meter_delete(request, pk):
    return _удалить(
        request,
        Meter,
        pk,
        reverse("catalog"),
        что_мешает="на него смотрят правила срока",
    )


@require_POST
@login_required
def category_delete(request, pk):
    return _удалить(request, Category, pk, reverse("catalog"))


# --- Фактура: «где лежит, номер, цена» у предмета, человека, дела -----------

ХОЗЯЕВА_ФАКТУРЫ = {
    "item": (Item, "item"),
    "person": (Person, "person"),
    "obligation": (Obligation, "obligation"),
}


@require_POST
@login_required
def fact_add(request, вид, pk):
    """Добавить строку фактуры к предмету, человеку или делу."""
    if вид not in ХОЗЯЕВА_ФАКТУРЫ:
        raise Http404
    модель, имя_страницы = ХОЗЯЕВА_ФАКТУРЫ[вид]
    хозяин = get_object_or_404(модель, pk=pk)
    форма = FactForm(request.POST, request.FILES, prefix="ф")
    if форма.is_valid():
        строка = форма.save(commit=False)
        строка.content_type = ContentType.objects.get_for_model(модель)
        строка.object_id = хозяин.pk
        строка.save()
        messages.success(request, f"Фактура дополнена: {строка}.")
    else:
        messages.error(request, "Название обязательно — строка не добавлена.")
    return redirect(имя_страницы, pk=pk)


@require_POST
@login_required
def fact_delete(request, pk):
    """Удалить строку фактуры."""
    строка = get_object_or_404(Fact, pk=pk)
    строка.delete()
    messages.success(request, "Строка фактуры удалена.")
    return _вернуться(request, reverse("catalog"))
