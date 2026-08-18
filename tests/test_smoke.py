"""Дымовые тесты каркаса: живы главная страница и админка.

Это проверка «Как проверим» заметки «Скелет проекта» —
содержательные тесты появятся вместе с моделью данных.
"""
import pytest


@pytest.mark.django_db
def test_home_otvechaet(client):
    ответ = client.get("/")
    assert ответ.status_code == 200
    assert "Self-org" in ответ.content.decode()


@pytest.mark.django_db
def test_admin_na_meste(client):
    ответ = client.get("/admin/login/")
    assert ответ.status_code == 200
