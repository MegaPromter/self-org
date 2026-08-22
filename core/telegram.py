"""Telegram — заметка «Telegram-бот».

Две роли в одном модуле:

1. **Канал планировщика** — `send()`: уведомление из журнала
   уходит сообщением в привязанный чат, с фактурой и кнопками.
2. **Сам бот** — `run_bot()`: слушает Telegram, обрабатывает
   нажатия кнопок и ответы сообщением.

Что именно кнопки делают с данными — в `core/bot_actions.py`.
Здесь только связь с Telegram.
"""
import asyncio

from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from . import bot_actions
from .models import Notification

# Варианты отложки, предлагаемые кнопкой (правило 5).
ВАРИАНТЫ_ОТЛОЖКИ = [(1, "на день"), (3, "на 3 дня"), (7, "на неделю")]


# --- Сообщение: текст и кнопки (правило 1) ---------------------------------


def build_text(notification: Notification) -> str:
    """Текст напоминания плюс фактура обязательства."""
    строки = [notification.text]
    if notification.obligation_id:
        for факт in notification.obligation.facts.all():
            строки.append(
                f"{факт.name}: {факт.value}" if факт.value else факт.name
            )
    return "\n".join(строки)


def build_keyboard(notification: Notification) -> InlineKeyboardMarkup | None:
    """Кнопки под сообщением — свои для каждого вида напоминания."""
    if notification.kind == Notification.Kind.READING:
        if not notification.meter_id:
            return None
        return InlineKeyboardMarkup(
            [[_кнопка_показания(notification.meter_id)]]
        )

    if not notification.obligation_id:
        return None
    номер = notification.obligation_id
    ряды = [
        [
            InlineKeyboardButton("Сделано", callback_data=f"done:{номер}"),
            InlineKeyboardButton("Отложить", callback_data=f"snooze:{номер}"),
        ]
    ]
    # Правило по счётчику — заодно даём ввести показание.
    if notification.obligation.meter_id:
        ряды.append(
            [_кнопка_показания(notification.obligation.meter_id)]
        )
    return InlineKeyboardMarkup(ряды)


def _кнопка_показания(meter_id):
    return InlineKeyboardButton(
        "Ввести показание", callback_data=f"reading:{meter_id}"
    )


def _клавиатура_отложки(obligation_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    подпись, callback_data=f"snooze:{obligation_id}:{дней}"
                )
                for дней, подпись in ВАРИАНТЫ_ОТЛОЖКИ
            ]
        ]
    )


# --- Канал планировщика ----------------------------------------------------


def send(notification: Notification) -> bool:
    """Отправить уведомление в Telegram: принято — True.

    Токена нет или чат не привязан — False: уведомление остаётся
    «ожидает» (правило 2). Сбой сети превращается в исключение,
    планировщик его гасит и тоже считает «не принято».
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        return False
    чат = _чат(notification.user)
    if not чат:
        return False
    asyncio.run(
        _отправить(
            чат, build_text(notification), build_keyboard(notification)
        )
    )
    return True


def _чат(user):
    профиль = getattr(user, "notification_profile", None)
    return профиль.telegram_chat_id if профиль else ""


async def _отправить(чат, текст, клавиатура):
    async with Bot(settings.TELEGRAM_BOT_TOKEN) as бот:
        await бот.send_message(
            chat_id=чат, text=текст, reply_markup=клавиатура
        )


# --- Бот: разбор нажатий и сообщений ---------------------------------------


async def _на_старт(update, context):
    """/start — привязка чата кодом из ссылки (правило 8)."""
    код = context.args[0] if context.args else ""
    ответ = await sync_to_async(bot_actions.link_chat)(
        код, update.effective_chat.id
    )
    await update.message.reply_text(ответ)


async def _на_кнопку(update, context):
    """Нажатие кнопки под напоминанием."""
    запрос = update.callback_query
    await запрос.answer()  # убрать «часики» в интерфейсе Telegram
    чат = update.effective_chat.id
    части = (запрос.data or "").split(":")
    действие, номер = части[0], части[1] if len(части) > 1 else ""

    if действие == "done":
        ответ = await sync_to_async(bot_actions.mark_done)(чат, номер)
    elif действие == "reading":
        ответ = await sync_to_async(bot_actions.ask_reading)(чат, номер)
    elif действие == "snooze" and len(части) == 2:
        # Первое нажатие — показать, на сколько откладываем.
        await запрос.edit_message_reply_markup(_клавиатура_отложки(номер))
        return
    elif действие == "snooze":
        ответ = await sync_to_async(bot_actions.snooze)(
            чат, номер, int(части[2])
        )
    else:
        ответ = "Не понял кнопку."
    await запрос.message.reply_text(ответ)


async def _на_текст(update, context):
    """Ответ сообщением: стоимость или показание счётчика."""
    ответ = await sync_to_async(bot_actions.handle_text)(
        update.effective_chat.id, update.message.text
    )
    await update.message.reply_text(ответ)


def run_bot():
    """Запустить бота: опрос Telegram, пока процесс живёт (правило 10)."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "Не задан TELEGRAM_BOT_TOKEN — бот не может запуститься. "
            "Токен даёт @BotFather в Telegram."
        )
    приложение = Application.builder().token(
        settings.TELEGRAM_BOT_TOKEN
    ).build()
    приложение.add_handler(CommandHandler("start", _на_старт))
    приложение.add_handler(CallbackQueryHandler(_на_кнопку))
    приложение.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _на_текст)
    )
    приложение.run_polling()
