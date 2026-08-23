"""Формы заведения данных — заметка «Заведение данных без админки».

Главная здесь — форма дела: она одна закрывает всё, что раньше
делалось в админке, включая создание предмета, человека
и счётчика «на лету» и запись «когда делали в последний раз».

Проверки не дублируются: форма спрашивает только то, чего модель
знать не может (что создаём новое), а правила заполнения
проверяет сама модель (`Obligation.clean`).
"""
import datetime

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

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


class ObligationForm(forms.ModelForm):
    """Дело: что делаем, к чему относится и когда напоминать.

    Способ напоминания пользователь выбирает одной плиткой
    («повторяется», «один раз к дате», «по счётчику», «когда
    получится»), а модельные `rule_kind` и `time_kind` форма
    собирает сама — заметка «Порядок на экранах», правила 9–12.
    """

    ПОВТОРЯЕТСЯ = "повторяется"
    ДАТА = "дата"
    СЧЁТЧИК = "счётчик"
    КОГДА_ПОЛУЧИТСЯ = "когда_получится"
    ИНТЕРВАЛ = "интервал"
    ГОДОВЩИНА = "годовщина"

    способ = forms.ChoiceField(
        label="когда напоминать",
        choices=[
            (ПОВТОРЯЕТСЯ, "повторяется"),
            (ДАТА, "один раз к дате"),
            (СЧЁТЧИК, "по счётчику"),
            (КОГДА_ПОЛУЧИТСЯ, "когда получится"),
        ],
        widget=forms.RadioSelect,
        required=False,
    )
    повтор_вид = forms.ChoiceField(
        label="как именно",
        choices=[
            (ИНТЕРВАЛ, "каждые сколько-то дней, месяцев, лет"),
            (ГОДОВЩИНА, "раз в год в один и тот же день"),
        ],
        widget=forms.RadioSelect,
        required=False,
        initial=ИНТЕРВАЛ,
    )
    ещё_по_счётчику = forms.BooleanField(
        label="ещё и по счётчику",
        required=False,
        help_text="Например: раз в 6 месяцев или каждые 8000 км — "
        "что наступит раньше.",
    )

    новый_предмет = forms.CharField(
        label="…или новый предмет",
        max_length=200,
        required=False,
        help_text="Например: Kia Rio, котёл, квартира.",
    )
    новый_человек = forms.CharField(
        label="…или новый человек",
        max_length=200,
        required=False,
    )
    новый_счётчик = forms.CharField(
        label="…или новый счётчик",
        max_length=200,
        required=False,
        help_text="Например: пробег, вода холодная, моточасы.",
    )
    единица_счётчика = forms.CharField(
        label="в чём измеряется",
        max_length=50,
        required=False,
        help_text="км, м³, литры, часы…",
    )
    годовщина = forms.CharField(
        label="день и месяц",
        max_length=5,
        required=False,
        help_text="В виде 14.09 — для ежегодных дел.",
    )
    последний_раз = forms.DateField(
        label="когда делали последний раз",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=(
            "Необязательно. Заполните — и система сразу посчитает "
            "следующий срок, а не будет просить отметку."
        ),
    )
    показание_тогда = forms.DecimalField(
        label="показание счётчика тогда",
        max_digits=12,
        decimal_places=2,
        required=False,
    )

    class Meta:
        model = Obligation
        fields = [
            "name",
            "category",
            "item",
            "person",
            "due_date",
            "interval_value",
            "interval_unit",
            "meter",
            "meter_interval",
            "notes",
            "soon_threshold_percent",
            "overdue_repeat_days",
            "assignee",
            "is_private",
        ]
        labels = {
            "name": "что делаем",
            "category": "раздел",
            "item": "предмет",
            "person": "человек",
            "due_date": "дата",
            "interval_value": "каждые",
            "interval_unit": "чего",
            "meter": "счётчик",
            "meter_interval": "каждые (по счётчику)",
            "notes": "заметка",
        }
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        for имя in ("item", "person", "category", "meter", "assignee"):
            self.fields[имя].required = False
            self.fields[имя].empty_label = "— не выбрано —"
        self.fields["category"].queryset = Category.objects.all()
        self.fields["item"].queryset = Item.objects.all()
        self.fields["meter"].queryset = Meter.objects.select_related("item")
        # Ежегодные месяц и день собираем из одного поля «14.09».
        if self.instance.pk and self.instance.annual_month:
            self.fields["годовщина"].initial = (
                f"{self.instance.annual_day:02d}."
                f"{self.instance.annual_month:02d}"
            )
        if self.instance.pk:
            self._разобрать_правило(self.instance)

    def _разобрать_правило(self, дело):
        """Показать заведённое дело в новых терминах формы.

        Обратный перевод: в базе лежат `rule_kind` и `time_kind`,
        а пользователь видит плитку и галочку (правило 12).
        """
        если_время = {
            Obligation.TimeKind.ONCE: self.ДАТА,
            Obligation.TimeKind.SOMEDAY: self.КОГДА_ПОЛУЧИТСЯ,
        }
        if дело.rule_kind == Obligation.RuleKind.METER:
            self.fields["способ"].initial = self.СЧЁТЧИК
            return
        self.fields["способ"].initial = если_время.get(
            дело.time_kind, self.ПОВТОРЯЕТСЯ
        )
        self.fields["повтор_вид"].initial = (
            self.ГОДОВЩИНА
            if дело.time_kind == Obligation.TimeKind.ANNUAL
            else self.ИНТЕРВАЛ
        )
        self.fields["ещё_по_счётчику"].initial = (
            дело.rule_kind == Obligation.RuleKind.BOTH
        )

    # --- Проверки ----------------------------------------------------------

    def clean_годовщина(self):
        значение = (self.cleaned_data.get("годовщина") or "").strip()
        if not значение:
            return значение
        try:
            день, месяц = (int(ч) for ч in значение.replace(",", ".").split("."))
            datetime.date(2000, месяц, день)  # 2000-й високосный: 29.02 можно
        except (ValueError, TypeError):
            raise ValidationError("Нужен день и месяц в виде 14.09.")
        return значение

    def _собрать_правило(self, данные):
        """Из плитки и галочки — модельные «вид правила» и «по времени».

        Плитка «повторяется» с галочкой «ещё и по счётчику» даёт
        прежний вариант «оба сразу — что раньше» (правило 12).
        """
        способ = данные.get("способ")
        if способ == self.СЧЁТЧИК:
            return Obligation.RuleKind.METER, ""
        по_времени = {
            self.ДАТА: Obligation.TimeKind.ONCE,
            self.КОГДА_ПОЛУЧИТСЯ: Obligation.TimeKind.SOMEDAY,
        }.get(способ)
        if по_времени is None:
            по_времени = (
                Obligation.TimeKind.ANNUAL
                if данные.get("повтор_вид") == self.ГОДОВЩИНА
                else Obligation.TimeKind.INTERVAL
            )
        # «Когда получится» и счётчик друг с другом не сочетаются:
        # у дела без срока считать по пробегу нечего.
        со_счётчиком = данные.get("ещё_по_счётчику") and способ != (
            self.КОГДА_ПОЛУЧИТСЯ
        )
        вид = (
            Obligation.RuleKind.BOTH
            if со_счётчиком
            else Obligation.RuleKind.TIME
        )
        return вид, по_времени

    def clean(self):
        данные = super().clean()
        if not данные.get("способ"):
            self.add_error("способ", "Выберите, когда напоминать.")
            return данные

        вид, по_времени = self._собрать_правило(данные)
        # Модельные поля формой не показываются — заполняем сами,
        # проверит их `Obligation.clean` при сохранении.
        self.instance.rule_kind = вид
        self.instance.time_kind = по_времени
        данные["rule_kind"] = вид
        данные["time_kind"] = по_времени

        нужно_время = вид in (Obligation.RuleKind.TIME, Obligation.RuleKind.BOTH)
        нужен_счётчик = вид in (
            Obligation.RuleKind.METER,
            Obligation.RuleKind.BOTH,
        )

        if нужно_время and по_времени == Obligation.TimeKind.ANNUAL:
            if not данные.get("годовщина"):
                self.add_error("годовщина", "Укажите день и месяц, например 14.09.")
            else:
                день, месяц = (
                    int(ч) for ч in данные["годовщина"].replace(",", ".").split(".")
                )
                self.instance.annual_day, self.instance.annual_month = день, месяц

        if нужен_счётчик:
            if not данные.get("meter") and not данные.get("новый_счётчик"):
                self.add_error(
                    "meter", "Выберите счётчик или введите название нового."
                )
            if данные.get("новый_счётчик"):
                if not данные.get("единица_счётчика"):
                    self.add_error(
                        "единица_счётчика",
                        "Укажите, в чём измеряется: км, м³, литры…",
                    )
                if not (данные.get("item") or данные.get("новый_предмет")):
                    self.add_error(
                        "новый_предмет",
                        "Счётчик принадлежит предмету — выберите его "
                        "или введите новый.",
                    )

        if данные.get("показание_тогда") is not None and not (
            данные.get("meter") or данные.get("новый_счётчик")
        ):
            self.add_error(
                "показание_тогда",
                "Показание записывать некуда — у дела нет счётчика.",
            )
        return данные

    def _post_clean(self):
        """Проверка модели — но не ругаемся на то, что ещё создаётся.

        Предмет, человек и счётчик появляются только при сохранении
        (правило 3 заметки), поэтому связанные ошибки модели здесь
        снимаем: их заменяют собственные проверки формы выше.
        """
        super()._post_clean()
        отложено = {
            "meter": self.data.get("новый_счётчик"),
            "item": self.data.get("новый_предмет"),
            "person": self.data.get("новый_человек"),
            "annual_day": self.data.get("годовщина"),
        }
        for поле, создаётся in отложено.items():
            if создаётся and поле in self._errors:
                del self._errors[поле]

    # --- Сохранение --------------------------------------------------------

    @transaction.atomic
    def save(self, commit=True):
        """Сохранить дело, создав по пути новые предмет, человека, счётчик.

        Всё внутри одной транзакции: не сохранилось дело — не
        останется и созданных записей (правило 3).
        """
        дело = super().save(commit=False)
        данные = self.cleaned_data

        if данные.get("новый_предмет"):
            дело.item = Item.objects.create(
                name=данные["новый_предмет"].strip(),
                category=данные.get("category"),
            )
        if данные.get("новый_человек"):
            дело.person = Person.objects.create(
                name=данные["новый_человек"].strip()
            )
        if данные.get("новый_счётчик"):
            дело.meter = Meter.objects.create(
                item=дело.item,
                name=данные["новый_счётчик"].strip(),
                unit=данные["единица_счётчика"].strip(),
            )
        if self.user and not дело.owner_id:
            дело.owner = self.user

        дело.full_clean(exclude=["owner"])
        дело.save()

        # «Когда делали последний раз» — обычное выполнение: расчёты
        # и история у экрана, бота и формы одни (правило 4).
        if данные.get("последний_раз"):
            показание = данные.get("показание_тогда")
            Completion.objects.create(
                obligation=дело,
                date=данные["последний_раз"],
                meter_value=показание,
                done_by=self.user,
            )
            if показание is not None and дело.meter:
                MeterReading.objects.get_or_create(
                    meter=дело.meter,
                    date=данные["последний_раз"],
                    defaults={"value": показание},
                )
        return дело


class CompletionForm(forms.ModelForm):
    """Отметка выполнения задним числом: дата, показание, стоимость."""

    class Meta:
        model = Completion
        fields = ["date", "meter_value", "cost", "note"]
        labels = {
            "date": "когда сделано",
            "meter_value": "показание счётчика",
            "cost": "сколько стоило",
            "note": "заметка",
        }
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 2}),
        }


class ItemForm(forms.ModelForm):
    """Предмет: то, что обслуживают, — машина, котёл, квартира."""

    class Meta:
        model = Item
        fields = ["name", "category", "notes"]
        labels = {"name": "название", "category": "раздел", "notes": "заметки"}
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].required = False
        self.fields["category"].empty_label = "— без раздела —"


class PersonForm(forms.ModelForm):
    """Человек: родня и знакомые — дни рождения, звонки, помощь."""

    class Meta:
        model = Person
        fields = ["name", "birth_date", "notes"]
        labels = {
            "name": "имя",
            "birth_date": "дата рождения",
            "notes": "заметки",
        }
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["birth_date"].required = False


class MeterForm(forms.ModelForm):
    """Счётчик предмета: пробег, вода, моточасы."""

    class Meta:
        model = Meter
        fields = [
            "item",
            "name",
            "unit",
            "expected_yearly_usage",
            "reading_reminder_days",
        ]
        labels = {
            "item": "предмет",
            "name": "название",
            "unit": "в чём измеряется",
        }


class MeterReadingForm(forms.ModelForm):
    """Показание счётчика: сколько и на какую дату."""

    class Meta:
        model = MeterReading
        fields = ["value", "date"]
        labels = {"value": "показание", "date": "на дату"}
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class CategoryForm(forms.ModelForm):
    """Раздел: транспорт, дом, семья…"""

    class Meta:
        model = Category
        fields = ["name", "order"]
        labels = {"name": "название", "order": "порядок"}


class FactForm(forms.ModelForm):
    """Строка фактуры: где лежит, номер, цена, файл."""

    class Meta:
        model = Fact
        fields = ["name", "value", "file"]
        labels = {"name": "название", "value": "значение", "file": "файл"}
