from django.db.models import Count, Q, Sum

from .models import UploadIntent


def reservas_upload(
    evento,
    ahora,
    excluir_intent_id=None,
    incluir_pending_cantidad=True,
    incluir_pending_almacenamiento=True,
):
    """Return the quota reserved by current UploadIntent rules."""
    intents = UploadIntent.objects.filter(evento=evento)

    if excluir_intent_id is not None:
        intents = intents.exclude(pk=excluir_intent_id)

    pending = {"cantidad": 0, "almacenamiento": 0}
    if incluir_pending_cantidad or incluir_pending_almacenamiento:
        pending = intents.filter(
            estado=UploadIntent.Estado.PENDING,
            expires_at__gt=ahora,
        ).aggregate(
            cantidad=Count("id"),
            almacenamiento=Sum("tamaño_declarado"),
        )
    materializados = intents.filter(
        Q(estado=UploadIntent.Estado.FINALIZING)
        | Q(
            estado=UploadIntent.Estado.CLEANUP_PENDING,
            cleaned_at__isnull=True,
        )
        | Q(
            estado=UploadIntent.Estado.CLEANUP_PENDING,
            finalizing_at__isnull=False,
        )
    ).aggregate(
        cantidad=Count("id"),
        almacenamiento_real=Sum("tamaño_real"),
        almacenamiento_declarado=Sum("tamaño_declarado"),
    )

    cantidad_pending = (
        (pending["cantidad"] or 0)
        if incluir_pending_cantidad
        else 0
    )
    almacenamiento_pending = (
        (pending["almacenamiento"] or 0)
        if incluir_pending_almacenamiento
        else 0
    )
    cantidad = cantidad_pending + (
        materializados["cantidad"] or 0
    )
    almacenamiento = almacenamiento_pending

    for intent in intents.filter(
        Q(estado=UploadIntent.Estado.FINALIZING)
        | Q(
            estado=UploadIntent.Estado.CLEANUP_PENDING,
            cleaned_at__isnull=True,
        )
        | Q(
            estado=UploadIntent.Estado.CLEANUP_PENDING,
            finalizing_at__isnull=False,
        )
    ).only("tamaño_real", "tamaño_declarado"):
        almacenamiento += (
            intent.tamaño_real
            if intent.tamaño_real is not None
            else intent.tamaño_declarado
        )

    return cantidad, almacenamiento
