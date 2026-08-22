"""Адреса главного экрана — заметка «Главный экран».

Действия отдельными адресами и только методом POST: обновление
страницы в браузере не должно записывать выполнение второй раз
(правило 10).
"""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("done/<int:pk>/", views.mark_done, name="mark_done"),
    path("cost/<int:pk>/", views.set_cost, name="set_cost"),
    path("snooze/<int:pk>/", views.snooze, name="snooze"),
    path("reading/<int:pk>/", views.add_reading, name="add_reading"),
]
