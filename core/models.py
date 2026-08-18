"""Модель данных Self-org.

Пять сущностей из анализа требований: предмет, счётчик
(с показаниями), человек, обязательство, выполнение — плюс
фактура (свои поля с файлами у любой записи) и настройки
уведомлений на каждого члена семьи.

Состояния (в норме / скоро / просрочено) и расчётные значения
счётчиков здесь не хранятся: они вычисляются — это следующая
задача очереди «Расчёт сроков и состояний».
"""
import datetime

from django.conf import settings
from django.contrib.contenttypes.fields import (
    GenericForeignKey,
    GenericRelation,
)
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models


class Fact(models.Model):
    """Строка фактуры: «название — значение» и необязательный файл.

    Прикрепляется к любой сущности (предмет, человек,
    обязательство) через универсальную связь — как «свои поля»
    в Grocy: артикул, цена, где лежит, чек, инструкция, фото.
    """

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    name = models.CharField("название", max_length=200)
    value = models.CharField("значение", max_length=500, blank=True)
    file = models.FileField(
        "файл", upload_to="facts/", null=True, blank=True
    )

    class Meta:
        verbose_name = "строка фактуры"
        verbose_name_plural = "фактура"
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.name}: {self.value}" if self.value else self.name


class Item(models.Model):
    """Предмет — то, что обслуживают: автомобиль, котёл, фильтр."""

    name = models.CharField("название", max_length=200)
    notes = models.TextField("заметки", blank=True)
    facts = GenericRelation(Fact, verbose_name="фактура")

    class Meta:
        verbose_name = "предмет"
        verbose_name_plural = "предметы"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Meter(models.Model):
    """Счётчик предмета: пробег, литры, моточасы."""

    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="meters",
        verbose_name="предмет",
    )
    name = models.CharField("название", max_length=200)
    unit = models.CharField(
        "единица", max_length=50, help_text="км, литры, моточасы…"
    )
    expected_yearly_usage = models.DecimalField(
        "ожидаемый годовой расход",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Необязательно: «примерно 15 000 км в год».",
    )
    reading_reminder_days = models.PositiveIntegerField(
        "напоминать ввести показания через, дней", default=90
    )

    class Meta:
        verbose_name = "счётчик"
        verbose_name_plural = "счётчики"
        ordering = ["item", "name"]

    def __str__(self):
        return f"{self.name} — {self.item}"


class MeterReading(models.Model):
    """Показание счётчика, введённое пользователем."""

    meter = models.ForeignKey(
        Meter,
        on_delete=models.CASCADE,
        related_name="readings",
        verbose_name="счётчик",
    )
    value = models.DecimalField("значение", max_digits=12, decimal_places=2)
    date = models.DateField("дата", default=datetime.date.today)

    class Meta:
        verbose_name = "показание"
        verbose_name_plural = "показания"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.value} {self.meter.unit} на {self.date}"


class Person(models.Model):
    """Человек — родня, знакомые: дни рождения, звонки, помощь."""

    name = models.CharField("имя", max_length=200)
    birth_date = models.DateField("дата рождения", null=True, blank=True)
    notes = models.TextField("заметки", blank=True)
    # Если человек — член семьи с доступом в систему, здесь его
    # учётка. У родни без доступа поле пустое.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="person",
        verbose_name="учётка члена семьи",
    )
    facts = GenericRelation(Fact, verbose_name="фактура")

    class Meta:
        verbose_name = "человек"
        verbose_name_plural = "люди"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Obligation(models.Model):
    """Обязательство — то, что нужно делать: разово или регулярно."""

    class RuleKind(models.TextChoices):
        TIME = "time", "по времени"
        METER = "meter", "по счётчику"
        BOTH = "both", "оба сразу — что раньше"

    class TimeKind(models.TextChoices):
        ONCE = "once", "разово к дате"
        INTERVAL = "interval", "каждые N от последнего выполнения"
        ANNUAL = "annual", "ежегодно в фиксированную дату"

    class IntervalUnit(models.TextChoices):
        DAYS = "days", "дней"
        WEEKS = "weeks", "недель"
        MONTHS = "months", "месяцев"
        YEARS = "years", "лет"

    name = models.CharField("название", max_length=200)
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="obligations",
        null=True,
        blank=True,
        verbose_name="предмет",
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="obligations",
        null=True,
        blank=True,
        verbose_name="человек",
    )
    notes = models.TextField("заметки", blank=True)

    # --- Правило срока: вид и его части --------------------------------
    rule_kind = models.CharField(
        "вид правила срока", max_length=10, choices=RuleKind.choices
    )
    time_kind = models.CharField(
        "правило по времени",
        max_length=10,
        choices=TimeKind.choices,
        blank=True,
    )
    due_date = models.DateField("дата (для разового)", null=True, blank=True)
    interval_value = models.PositiveIntegerField(
        "интервал: сколько", null=True, blank=True
    )
    interval_unit = models.CharField(
        "интервал: чего",
        max_length=10,
        choices=IntervalUnit.choices,
        blank=True,
    )
    annual_month = models.PositiveSmallIntegerField(
        "ежегодно: месяц", null=True, blank=True
    )
    annual_day = models.PositiveSmallIntegerField(
        "ежегодно: день", null=True, blank=True
    )
    # Счётчик защищён от удаления, пока на него смотрит правило:
    # иначе обязательство молча потеряет смысл.
    meter = models.ForeignKey(
        Meter,
        on_delete=models.PROTECT,
        related_name="obligations",
        null=True,
        blank=True,
        verbose_name="счётчик",
    )
    meter_interval = models.DecimalField(
        "интервал по счётчику",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Например, 8000 — каждые 8000 км.",
    )

    # --- Напоминания ---------------------------------------------------
    soon_threshold_percent = models.PositiveSmallIntegerField(
        "порог «скоро», % интервала", default=70
    )
    overdue_repeat_days = models.PositiveIntegerField(
        "повтор о просроченном, дней", default=7
    )

    # --- Семья ---------------------------------------------------------
    # Владельца нельзя удалить, не решив судьбу его обязательств.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_obligations",
        verbose_name="владелец",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="assigned_obligations",
        null=True,
        blank=True,
        verbose_name="исполнитель",
    )
    is_private = models.BooleanField(
        "личное",
        default=False,
        help_text="Личное видит только владелец, общее — вся семья.",
    )

    facts = GenericRelation(Fact, verbose_name="фактура")

    class Meta:
        verbose_name = "обязательство"
        verbose_name_plural = "обязательства"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        """Проверка, что у выбранного вида правила заполнены его части."""
        ошибки = {}

        нужно_время = self.rule_kind in (self.RuleKind.TIME, self.RuleKind.BOTH)
        нужен_счётчик = self.rule_kind in (
            self.RuleKind.METER,
            self.RuleKind.BOTH,
        )

        if нужно_время:
            if not self.time_kind:
                ошибки["time_kind"] = "Выберите правило по времени."
            elif self.time_kind == self.TimeKind.ONCE and not self.due_date:
                ошибки["due_date"] = "Укажите дату для разового обязательства."
            elif self.time_kind == self.TimeKind.INTERVAL:
                if not self.interval_value:
                    ошибки["interval_value"] = "Укажите длину интервала."
                if not self.interval_unit:
                    ошибки["interval_unit"] = "Выберите единицу интервала."
            elif self.time_kind == self.TimeKind.ANNUAL:
                try:
                    # 2000 год високосный — 29 февраля тоже допустимо.
                    datetime.date(2000, self.annual_month, self.annual_day)
                except (TypeError, ValueError):
                    ошибки["annual_day"] = (
                        "Укажите существующие месяц и день."
                    )

        if нужен_счётчик:
            if not self.meter:
                ошибки["meter"] = "Выберите счётчик."
            if not self.meter_interval or self.meter_interval <= 0:
                ошибки["meter_interval"] = (
                    "Укажите интервал по счётчику больше нуля."
                )

        if (
            self.meter
            and self.item
            and self.meter.item_id != self.item_id
        ):
            ошибки["meter"] = "Счётчик принадлежит другому предмету."

        if ошибки:
            raise ValidationError(ошибки)


class Completion(models.Model):
    """Выполнение — запись факта: когда сделано и почём.

    От последнего выполнения отсчитывается следующий срок.
    """

    obligation = models.ForeignKey(
        Obligation,
        on_delete=models.CASCADE,
        related_name="completions",
        verbose_name="обязательство",
    )
    date = models.DateField("дата", default=datetime.date.today)
    meter_value = models.DecimalField(
        "показание счётчика",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    cost = models.DecimalField(
        "стоимость", max_digits=12, decimal_places=2, null=True, blank=True
    )
    note = models.TextField("заметка", blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="completions",
        null=True,
        blank=True,
        verbose_name="кто сделал",
    )

    class Meta:
        verbose_name = "выполнение"
        verbose_name_plural = "выполнения"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.obligation} — {self.date}"


class NotificationProfile(models.Model):
    """Настройки уведомлений члена семьи: Telegram и тихие часы."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_profile",
        verbose_name="учётка",
    )
    telegram_chat_id = models.CharField(
        "Telegram chat id",
        max_length=64,
        blank=True,
        help_text="Заполнится при подключении бота.",
    )
    quiet_hours_start = models.TimeField(
        "тихие часы с", default=datetime.time(22, 0)
    )
    quiet_hours_end = models.TimeField(
        "тихие часы до", default=datetime.time(9, 0)
    )

    class Meta:
        verbose_name = "настройки уведомлений"
        verbose_name_plural = "настройки уведомлений"

    def __str__(self):
        return f"Уведомления: {self.user}"
