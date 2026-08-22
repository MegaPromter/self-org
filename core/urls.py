"""Адреса экранов — заметки «Главный экран», «Разделы на главной»,
«Десктопный макет», «Заведение данных без админки».

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
    # Дела: заведение, карточка, правка, удаление, история.
    path("new/", views.obligation_new, name="obligation_new"),
    path("obligation/<int:pk>/", views.obligation_detail, name="obligation"),
    path(
        "obligation/<int:pk>/edit/",
        views.obligation_edit,
        name="obligation_edit",
    ),
    path(
        "obligation/<int:pk>/delete/",
        views.obligation_delete,
        name="obligation_delete",
    ),
    path(
        "obligation/<int:pk>/completion/",
        views.completion_add,
        name="completion_add",
    ),
    path(
        "completion/<int:pk>/delete/",
        views.completion_delete,
        name="completion_delete",
    ),
    # Справочники: предметы, люди, счётчики, разделы.
    path("catalog/", views.catalog, name="catalog"),
    path("item/new/", views.item_new, name="item_new"),
    path("item/<int:pk>/", views.item_detail, name="item"),
    path("item/<int:pk>/edit/", views.item_edit, name="item_edit"),
    path("item/<int:pk>/delete/", views.item_delete, name="item_delete"),
    path("person/new/", views.person_new, name="person_new"),
    path("person/<int:pk>/", views.person_detail, name="person"),
    path("person/<int:pk>/edit/", views.person_edit, name="person_edit"),
    path("person/<int:pk>/delete/", views.person_delete, name="person_delete"),
    path("meter/new/", views.meter_new, name="meter_new"),
    path("meter/<int:pk>/", views.meter_detail, name="meter"),
    path("meter/<int:pk>/edit/", views.meter_edit, name="meter_edit"),
    path("meter/<int:pk>/delete/", views.meter_delete, name="meter_delete"),
    path(
        "meter/<int:pk>/reading/",
        views.reading_add_dated,
        name="reading_add_dated",
    ),
    path(
        "reading/<int:pk>/delete/",
        views.reading_delete,
        name="reading_delete",
    ),
    path("category/new/", views.category_new, name="category_new"),
    path(
        "category/<int:pk>/edit/", views.category_edit, name="category_edit"
    ),
    path(
        "category/<int:pk>/delete/",
        views.category_delete,
        name="category_delete",
    ),
    # Фактура: строки «название — значение» у предмета, человека, дела.
    path("fact/<str:вид>/<int:pk>/add/", views.fact_add, name="fact_add"),
    path("fact/<int:pk>/delete/", views.fact_delete, name="fact_delete"),
]
