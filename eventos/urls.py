from django.urls import path

from . import views



urlpatterns = [
    path(
        "fotos/<slug:slug>/",
        views.evento_publico,
        name="evento_publico",
    ),
    path(
        "fotos/<slug:slug>/t/<str:token>/",
        views.mesa_publica,
        name="mesa_publica",
    ),
    path(
        "fotos/<slug:slug>/t/<str:token>/acceso/",
        views.verificar_acceso,
        name="verificar_acceso",
    ),
    path(
        "fotos/<slug:slug>/t/<str:token>/instrucciones/",
        views.instrucciones,
        name="instrucciones",
    ),

    path(
        "fotos/<slug:slug>/t/<str:token>/subir/",
        views.subir_fotos,
        name="subir_fotos",
    ),

    path(
        "fotos/<slug:slug>/t/<str:token>/solicitar-url-subida/",
        views.solicitar_url_subida,
        name="solicitar_url_subida",
    ),

    path(
        "fotos/<slug:slug>/t/<str:token>/confirmar-subida/",
        views.confirmar_subida,
        name="confirmar_subida",
    ),
]