from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction

from eventos.models import Evento, Mesa
from eventos.services.event_configuration import (
    CONFIGURACION_VERSION_ACTUAL,
    generar_slug_unico,
)


class LimiteEventosAutoservicioAlcanzado(Exception):
    pass


def _asignar_creador_como_anfitrion(evento, usuario):
    evento.anfitriones.add(usuario)


def asegurar_mesa_inicial_autoservicio(evento):
    if evento.creation_source != Evento.CreationSource.SELF_SERVICE:
        return None

    if evento.mesas.exists():
        return None

    mesa, _created = Mesa.objects.get_or_create(
        evento=evento,
        numero=1,
    )
    return mesa


@transaction.atomic
def crear_evento_autoservicio(*, usuario, nombre, tipo, fecha):
    usuario_bloqueado = User.objects.select_for_update().get(pk=usuario.pk)

    if not usuario_bloqueado.is_active or usuario_bloqueado.is_superuser:
        raise PermissionDenied

    if Evento.objects.filter(
        self_service_created_by=usuario_bloqueado
    ).exists():
        raise LimiteEventosAutoservicioAlcanzado

    evento = Evento(
        nombre=nombre,
        slug=generar_slug_unico(nombre),
        tipo=tipo,
        fecha=fecha,
        estado=Evento.Estado.DRAFT,
        configuracion_version=CONFIGURACION_VERSION_ACTUAL,
        creation_source=Evento.CreationSource.SELF_SERVICE,
        self_service_created_by=usuario_bloqueado,
    )
    evento.full_clean()
    evento.save()
    _asignar_creador_como_anfitrion(evento, usuario_bloqueado)
    return evento
