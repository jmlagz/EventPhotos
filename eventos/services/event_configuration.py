from datetime import datetime, time, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from eventos.models import Evento


CONFIGURACION_VERSION_ACTUAL = 1
TIMEZONE_DEFAULT = "America/Mexico_City"
VIGENCIAS_ADMINISTRATIVAS = (6, 12)


def validar_timezone(timezone_name):
    try:
        return ZoneInfo(timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ValidationError(
            {"timezone": "Selecciona una zona horaria válida."}
        ) from exc


def _datetime_local_valido(local_naive, event_timezone, fold):
    candidate = local_naive.replace(tzinfo=event_timezone, fold=fold)
    roundtrip = candidate.astimezone(datetime_timezone.utc).astimezone(
        event_timezone
    )
    if roundtrip.replace(tzinfo=None) != local_naive:
        return None
    return candidate


def construir_fin_planeado(fecha_evento, timezone_name):
    event_timezone = validar_timezone(timezone_name)
    local_naive = datetime.combine(
        fecha_evento + timedelta(days=1),
        time(hour=4),
    )
    candidates = {
        candidate.utcoffset(): candidate
        for fold in (0, 1)
        if (candidate := _datetime_local_valido(
            local_naive,
            event_timezone,
            fold,
        ))
        is not None
    }

    if not candidates:
        raise ValidationError(
            {"fecha": "La hora derivada no existe en la zona seleccionada."}
        )

    if len(candidates) > 1:
        raise ValidationError(
            {"fecha": "La hora derivada es ambigua en la zona seleccionada."}
        )

    return next(iter(candidates.values()))


def generar_slug_unico(nombre):
    base = slugify(nombre) or "evento"
    candidate = base
    suffix = 2

    while Evento.objects.filter(slug=candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1

    return candidate


def materializar_temporalidad(
    evento,
    *,
    fecha_evento,
    timezone_name,
    duracion_efectiva_meses,
):
    if duracion_efectiva_meses not in VIGENCIAS_ADMINISTRATIVAS:
        raise ValidationError(
            {"vigencia_meses": "Selecciona una vigencia administrativa válida."}
        )

    evento.fecha = fecha_evento
    evento.timezone = timezone_name
    fin_planeado = construir_fin_planeado(fecha_evento, timezone_name)
    evento.materializar_ciclo_temporal(
        fin_planeado,
        meses_disponibilidad=duracion_efectiva_meses,
    )


@transaction.atomic
def crear_evento_configurable(
    *,
    nombre,
    tipo,
    fecha,
    descripcion,
    mensaje_bienvenida,
    timezone_name,
    duracion_efectiva_meses,
):
    evento = Evento(
        nombre=nombre,
        slug=generar_slug_unico(nombre),
        tipo=tipo,
        fecha=fecha,
        descripcion=descripcion,
        mensaje_bienvenida=mensaje_bienvenida,
        estado=Evento.Estado.DRAFT,
        configuracion_version=CONFIGURACION_VERSION_ACTUAL,
    )
    materializar_temporalidad(
        evento,
        fecha_evento=fecha,
        timezone_name=timezone_name,
        duracion_efectiva_meses=duracion_efectiva_meses,
    )
    evento.full_clean()
    evento.save()
    return evento


def asignar_anfitrion(evento, usuario):
    evento.anfitriones.add(usuario)


def _temporalidad_coherente(evento):
    if not all(
        (
            evento.timezone,
            evento.fin_planeado,
            evento.upload_until,
            evento.available_until,
        )
    ):
        return False

    try:
        validar_timezone(evento.timezone)
        expected_end = construir_fin_planeado(evento.fecha, evento.timezone)
    except ValidationError:
        return False

    if not all(
        timezone.is_aware(value)
        for value in (
            evento.fin_planeado,
            evento.upload_until,
            evento.available_until,
        )
    ):
        return False

    expected_upload_until = evento.fin_planeado.astimezone(
        datetime_timezone.utc
    ) + timedelta(hours=48)
    return (
        evento.fin_planeado == expected_end
        and evento.upload_until.astimezone(datetime_timezone.utc)
        == expected_upload_until
        and evento.available_until > evento.fin_planeado
    )


def evaluar_checklist(evento):
    tipos_validos = {value for value, _label in Evento.Tipo.choices}
    informacion_ok = bool(
        evento.nombre and evento.tipo in tipos_validos and evento.fecha
    )
    temporalidad_ok = _temporalidad_coherente(evento)
    anfitriones_ok = evento.anfitriones.exists()
    mesas_ok = evento.mesas.filter(activa=True).exists()
    listo = all(
        (informacion_ok, temporalidad_ok, anfitriones_ok, mesas_ok)
    )

    def item(clave, titulo, completo, detalle_pendiente):
        return {
            "clave": clave,
            "titulo": titulo,
            "estado": "Completo" if completo else "Pendiente",
            "completo": completo,
            "detalle": "Configuración completa." if completo else detalle_pendiente,
        }

    items = [
        item(
            "informacion",
            "Información del evento",
            informacion_ok,
            "Completa nombre, tipo y fecha.",
        ),
        item(
            "temporalidad",
            "Fecha y horario",
            temporalidad_ok,
            "Revisa zona horaria y fechas de disponibilidad.",
        ),
        item(
            "anfitriones",
            "Anfitriones",
            anfitriones_ok,
            "Asigna al menos un anfitrión.",
        ),
        item(
            "mesas",
            "Mesas",
            mesas_ok,
            "Configura al menos una mesa activa.",
        ),
        {
            "clave": "identidad",
            "titulo": "Portada y logo",
            "estado": "Opcional",
            "completo": True,
            "detalle": (
                "Identidad visual configurada."
                if evento.imagen_portada_key or evento.logo_key
                else "Puedes añadirlos sin bloquear la activación."
            ),
        },
        {
            "clave": "activacion",
            "titulo": "Listo para activar",
            "estado": "Completo" if listo else "Revisar",
            "completo": listo,
            "detalle": (
                "El evento cumple los requisitos de activación."
                if listo
                else "Completa los elementos pendientes antes de activar."
            ),
        },
    ]
    return {"items": items, "listo": listo}


def validar_activacion(evento):
    if evento.configuracion_version is None:
        return

    checklist = evaluar_checklist(evento)
    if checklist["listo"]:
        return

    pendientes = [
        item["titulo"]
        for item in checklist["items"]
        if item["estado"] in {"Pendiente", "Revisar"}
        and item["clave"] != "activacion"
    ]
    raise ValidationError(
        "Completa la configuración antes de activar: " + ", ".join(pendientes)
    )


@transaction.atomic
def actualizar_evento_configurable(
    evento,
    *,
    nombre,
    tipo,
    fecha,
    descripcion,
    mensaje_bienvenida,
    timezone_name,
    duracion_efectiva_meses,
):
    if evento.estado == Evento.Estado.ARCHIVED:
        raise ValidationError("Los eventos archivados son de sólo lectura.")

    if evento.estado != Evento.Estado.DRAFT and (
        fecha != evento.fecha or timezone_name != evento.timezone
    ):
        raise ValidationError(
            "La fecha y la zona horaria sólo pueden cambiarse en borrador."
        )

    evento.nombre = nombre
    evento.tipo = tipo
    evento.descripcion = descripcion
    evento.mensaje_bienvenida = mensaje_bienvenida

    if evento.estado == Evento.Estado.DRAFT:
        materializar_temporalidad(
            evento,
            fecha_evento=fecha,
            timezone_name=timezone_name,
            duracion_efectiva_meses=duracion_efectiva_meses,
        )

    evento.full_clean()
    evento.save()
    return evento


def inferir_vigencia_meses(evento):
    if not evento.fin_planeado or not evento.available_until:
        return VIGENCIAS_ADMINISTRATIVAS[0]

    for months in VIGENCIAS_ADMINISTRATIVAS:
        if evento.fin_planeado + relativedelta(months=months) == evento.available_until:
            return months

    return VIGENCIAS_ADMINISTRATIVAS[0]
