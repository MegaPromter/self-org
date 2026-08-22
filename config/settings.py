"""Настройки Self-org.

Принцип: один код — разные окружения. Всё, что различается
между машиной пользователя и сервером, берётся из переменных
окружения; без переменных действуют безопасные для разработки
значения по умолчанию.
"""
import os
from pathlib import Path

from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent

# Локальные настройки из файла .env рядом с проектом: чтобы при
# простом запуске не вписывать токен бота в командную строку каждый
# раз. Переменные самого окружения (Docker, сервер) важнее файла.
def _прочитать_env(путь):
    if not путь.exists():
        return
    for строка in путь.read_text(encoding="utf-8").splitlines():
        строка = строка.strip()
        if not строка or строка.startswith("#") or "=" not in строка:
            continue
        имя, значение = строка.split("=", 1)
        os.environ.setdefault(имя.strip(), значение.strip().strip("\"'"))


_прочитать_env(BASE_DIR / ".env")

# На сервере ключ обязан прийти из окружения — значение ниже
# годится только для локальной разработки.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dev-only-insecure-key-не-для-сервера"
)

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = os.environ.get(
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1"
).split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# База: задан POSTGRES_HOST — работаем с PostgreSQL (Docker,
# сервер); не задан — локальная SQLite для простого запуска
# и тестов. Выбор SQLite здесь осознанный и временный: боевая
# база — PostgreSQL (см. vault «Выбор стека»).
if os.environ.get("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "selforg"),
            "USER": os.environ.get("POSTGRES_USER", "selforg"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ["POSTGRES_HOST"],
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
TIME_ZONE = os.environ.get("TIME_ZONE", "Europe/Moscow")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Файлы, загруженные пользователем (чеки, инструкции, фото
# в фактуре). Хранятся вне git.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Telegram: токен бота от @BotFather. Не задан — бот не запускается,
# сайт и планировщик работают как раньше, уведомления копятся
# в журнале (правило 9 заметки «Telegram-бот»).
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Имя бота без «@» — из него собирается ссылка привязки в админке.
# Не задано — админка покажет код и команду для ручного ввода.
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "")

# Celery: адрес Redis — «почтового ящика» между сайтом
# и планировщиком. Используется только в полном запуске.
CELERY_BROKER_URL = os.environ.get(
    "CELERY_BROKER_URL", "redis://localhost:6379/0"
)
CELERY_TIMEZONE = TIME_ZONE

# Прогоны планировщика — раз в 4 часа по фиксированным часам:
# один приходится ровно на конец тихих часов по умолчанию (09:00),
# чтобы накопившееся за ночь уходило утром, а не к обеду.
CELERY_BEAT_SCHEDULE = {
    "проверка-сроков": {
        "task": "core.tasks.check_due",
        "schedule": crontab(minute=0, hour="1,5,9,13,17,21"),
    },
}
