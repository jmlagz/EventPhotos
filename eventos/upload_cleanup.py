from datetime import timedelta

from botocore.exceptions import ClientError
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Evento, Foto, UploadIntent
from .r2 import get_r2_client


DEFAULT_UPLOAD_INTENT_CLEANUP_GRACE_SECONDS = 15 * 60
CLEANABLE_STATES = {
    UploadIntent.Estado.PENDING,
    UploadIntent.Estado.EXPIRED,
    UploadIntent.Estado.CANCELLED,
    UploadIntent.Estado.CLEANUP_PENDING,
    UploadIntent.Estado.CONFIRMED,
}


def cleanup_grace():
    seconds = getattr(
        settings,
        "UPLOAD_INTENT_CLEANUP_GRACE_SECONDS",
        DEFAULT_UPLOAD_INTENT_CLEANUP_GRACE_SECONDS,
    )
    return timedelta(seconds=int(seconds))


def cleanup_cutoff(ahora):
    return ahora - cleanup_grace()


def temporary_key_is_safe(upload_intent):
    object_key = upload_intent.object_key
    prefix = (
        f"eventos/{upload_intent.evento.slug}/"
        f"mesas/{upload_intent.mesa.token}/upload-intents/"
    )

    if not object_key.startswith(prefix):
        return False

    filename = object_key[len(prefix):]
    stem, separator, extension = filename.partition(".")
    if (
        not separator
        or not extension
        or "/" in filename
        or "\\" in filename
        or stem != str(upload_intent.id)
        or not extension.isalnum()
    ):
        return False

    if object_key == upload_intent.final_object_key:
        return False

    if (
        upload_intent.foto_id is not None
        and object_key == upload_intent.foto.object_key
    ):
        return False

    # Una Foto legacy puede usar el temporal sin estar asociada al intent.
    if Foto.objects.filter(
        object_key=object_key,
        eliminada_at__isnull=True,
    ).exists():
        return False

    return True


def intent_is_cleanable(upload_intent, ahora):
    if upload_intent.cleaned_at is not None:
        return False, "already_cleaned"

    if upload_intent.estado == UploadIntent.Estado.FINALIZING:
        return False, "finalizing"

    if upload_intent.estado not in CLEANABLE_STATES:
        return False, "state"

    if upload_intent.expires_at > cleanup_cutoff(ahora):
        return False, "grace"

    if upload_intent.estado == UploadIntent.Estado.CONFIRMED:
        if (
            upload_intent.final_object_key is None
            or upload_intent.foto_id is None
            or upload_intent.foto.object_key
            != upload_intent.final_object_key
        ):
            return False, "legacy_confirmed"

    if not temporary_key_is_safe(upload_intent):
        return False, "unsafe_key"

    return True, "eligible"


def _is_missing_object(error):
    if not isinstance(error, ClientError):
        return False

    response = error.response or {}
    code = str(response.get("Error", {}).get("Code", ""))
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status == 404 or code in {"404", "NoSuchKey", "NotFound"}


def _lock_and_evaluate(intent_id):
    with transaction.atomic():
        try:
            evento_id = UploadIntent.objects.only("evento_id").get(
                pk=intent_id
            ).evento_id
            evento = Evento.objects.select_for_update().get(pk=evento_id)
            upload_intent = (
                UploadIntent.objects
                .select_for_update()
                .get(pk=intent_id, evento=evento)
            )
        except (Evento.DoesNotExist, UploadIntent.DoesNotExist):
            return None, "missing"

        cleanable, reason = intent_is_cleanable(
            upload_intent,
            timezone.now(),
        )
        if not cleanable:
            return None, reason

        return upload_intent.object_key, "eligible"


def cleanup_upload_intent(intent_id, r2=None):
    object_key, reason = _lock_and_evaluate(intent_id)
    if object_key is None:
        return reason

    r2 = r2 or get_r2_client()
    try:
        r2.delete_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=object_key,
        )
    except Exception as error:
        if not _is_missing_object(error):
            return "retry"

    with transaction.atomic():
        try:
            evento_id = UploadIntent.objects.only("evento_id").get(
                pk=intent_id
            ).evento_id
            evento = Evento.objects.select_for_update().get(pk=evento_id)
            upload_intent = (
                UploadIntent.objects
                .select_for_update()
                .get(pk=intent_id, evento=evento)
            )
        except (Evento.DoesNotExist, UploadIntent.DoesNotExist):
            return "missing"

        if upload_intent.cleaned_at is not None:
            return "already_cleaned"

        cleanable, reason = intent_is_cleanable(
            upload_intent,
            timezone.now(),
        )
        if not cleanable or upload_intent.object_key != object_key:
            return reason

        upload_intent.cleaned_at = timezone.now()
        update_fields = ["cleaned_at"]
        if upload_intent.estado == UploadIntent.Estado.PENDING:
            upload_intent.estado = UploadIntent.Estado.EXPIRED
            update_fields.append("estado")
        upload_intent.save(update_fields=update_fields)

    return "cleaned"
