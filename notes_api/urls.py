from django.urls import path
from . import views

urlpatterns = [
    path("register", views.register),
    path("login", views.login),
    path("notes", views.notes_list),
    path("notes/<uuid:note_id>", views.note_detail),
    path("notes/<uuid:note_id>/share", views.share_note),
    path("notes/<uuid:note_id>/activity", views.note_activity),
    path("notes/<uuid:note_id>/restore", views.restore_note),
    path("trash", views.trash),
    path("search", views.search_notes),
    path("about", views.about),
    path("openapi.json", views.openapi_json),
]
