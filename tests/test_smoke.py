"""Дымовые тесты каркаса: живы главная страница, вход и админка.

Содержательные проверки главного экрана — в `test_main_screen.py`.
"""
import pytest
from django.contrib.auth.models import User


@pytest.mark.django_db
def test_home_trebuet_vhoda(client):
    """Не вошёл — экран не показывается, ведём на страницу входа."""
    ответ = client.get("/")
    assert ответ.status_code == 302
    assert "/login/" in ответ["Location"]


@pytest.mark.django_db
def test_home_otvechaet_voshedshemu(client):
    пользователь = User.objects.create_user("хозяин", password="секрет")
    client.force_login(пользователь)
    ответ = client.get("/")
    assert ответ.status_code == 200
    assert "Self-org" in ответ.content.decode()


@pytest.mark.django_db
def test_stranica_vhoda_na_meste(client):
    ответ = client.get("/login/")
    assert ответ.status_code == 200
    assert "Войти" in ответ.content.decode()


@pytest.mark.django_db
def test_admin_na_meste(client):
    ответ = client.get("/admin/login/")
    assert ответ.status_code == 200
