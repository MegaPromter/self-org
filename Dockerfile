# Образ веб-приложения. Один и тот же образ используют
# сайт (gunicorn), воркер и планировщик Celery.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir .

CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000"]
