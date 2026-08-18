from django.urls import path

from . import views



urlpatterns = [

    path(
        "",
        views.home,
        name="home",
    ),

    path(
        "login/",
        views.login_anfitrion,
        name="login_anfitrion",
    ),

    path(
        "logout/",
        views.logout_anfitrion,
        name="logout_anfitrion",
    ),

    path(
        "dashboard/",
        views.dashboard,
        name="dashboard",
    ),

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

    path(
        "fotos/<slug:slug>/album/",
        views.album_publico,
        name="album_publico",
    ),

    path(
        "fotos/<slug:slug>/foto/<int:foto_id>/eliminar/",
        views.eliminar_foto,
        name="eliminar_foto",
    ),

    path(
        "dashboard/eventos/<slug:slug>/",
        views.dashboard_evento,
        name="dashboard_evento",
    ),

    path(
        "dashboard/eventos/<slug:slug>/fotos/",
        views.fotos_dashboard,
        name="fotos_dashboard",
    ),

    path(
        "dashboard/eventos/<slug:slug>/mesas/",
        views.mesas_dashboard,
        name="mesas_dashboard",
    ),

    path(
        "dashboard/eventos/<slug:slug>/fotos/<int:foto_id>/eliminar/",
        views.eliminar_foto_dashboard,
        name="eliminar_foto_dashboard",
    ),

    path(
        "dashboard/eventos/<slug:slug>/mesas/<int:mesa_id>/actualizar/",
        views.actualizar_mesa,
        name="actualizar_mesa",
    ),

    path(
        "admin-dashboard/eventos/<slug:slug>/mesas/",
        views.configurar_mesas,
        name="configurar_mesas",
    ),

    path(
        "dashboard/eventos/<slug:slug>/mesas/<int:mesa_id>/qr/",
        views.qr_mesa,
        name="qr_mesa",
    ),

    path(
        "dashboard/eventos/<slug:slug>/mesas/<int:mesa_id>/qr/imprimir/",
        views.imprimir_qr_mesa,
        name="imprimir_qr_mesa",
    ),

    path(
        "dashboard/eventos/<slug:slug>/mesas/qr/imprimir-todos/",
        views.imprimir_qrs_mesas,
        name="imprimir_qrs_mesas",
    ),

    path(
        "dashboard/eventos/<slug:slug>/personalizacion/solicitar/",
        views.solicitar_url_personalizacion,
        name="solicitar_url_personalizacion",
    ),

    path(
        "dashboard/eventos/<slug:slug>/personalizacion/confirmar/",
        views.confirmar_personalizacion,
        name="confirmar_personalizacion",
    ),
]
