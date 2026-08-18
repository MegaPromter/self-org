from django.http import HttpResponse


def home(request):
    """Страница-заглушка: подтверждает, что каркас жив.

    Настоящий интерфейс появится в задачах про модель данных
    и экраны — эта страница будет заменена.
    """
    return HttpResponse(
        "<!doctype html>"
        '<html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Self-org</title></head>"
        '<body style="font-family: system-ui, sans-serif; max-width: 40rem; '
        'margin: 4rem auto; padding: 0 1rem;">'
        "<h1>Self-org</h1>"
        "<p>Система персональной организации. Каркас работает.</p>"
        '<p><a href="/admin/">Войти в админку</a></p>'
        "</body></html>"
    )
