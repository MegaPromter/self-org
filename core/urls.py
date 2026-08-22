"""Адреса экранов — заметки «Главный экран» и «Разделы на главной».

Действия отдельными адресами и только методом POST: обновление
страницы в браузере не должно записывать выполнение второй раз.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("all/", views.all_tasks, name="all_tasks"),
    path("section/<str:key>/", views.section, name="section"),
    path("done/<int:pk>/", views.mark_done, name="mark_done"),
    path("cost/<int:pk>/", views.set_cost, name="set_cost"),
    path("snooze/<int:pk>/", views.snooze, name="snooze"),
    path("reading/<int:pk>/", views.add_reading, name="add_reading"),
]
