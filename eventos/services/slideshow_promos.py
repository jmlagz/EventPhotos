import io
import logging
import uuid
import warnings

from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from eventos.r2 import eliminar_objeto, generar_url_lectura, get_r2_client


logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MIN_WIDTH = 1280
MIN_HEIGHT = 720
MAX_WIDTH = 3840
MAX_HEIGHT = 2160

FORMAT_DETAILS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}


class SlideshowPromoStorageError(Exception):
    """La imagen no pudo persistirse en el almacenamiento de promos."""


def validar_imagen_promo(uploaded_file):
    """Devuelve bytes y metadatos confiables para una imagen promocional."""
    try:
        if uploaded_file.size > MAX_IMAGE_BYTES:
            raise ValidationError("La imagen no puede superar los 5 MB.")

        content = uploaded_file.read(MAX_IMAGE_BYTES + 1)
        if len(content) > MAX_IMAGE_BYTES:
            raise ValidationError("La imagen no puede superar los 5 MB.")

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    image.verify()
                with Image.open(io.BytesIO(content)) as image:
                    image.load()
                    image_format = image.format
                    width, height = image.size
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            UnidentifiedImageError,
        ) as exc:
            raise ValidationError("El archivo no es una imagen válida.") from exc

        if image_format not in FORMAT_DETAILS:
            raise ValidationError("Solo se permiten imágenes JPEG, PNG o WebP.")
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            raise ValidationError("La imagen debe medir al menos 1280 × 720 píxeles.")
        if width > MAX_WIDTH or height > MAX_HEIGHT:
            raise ValidationError("La imagen no puede superar 3840 × 2160 píxeles.")

        extension, content_type = FORMAT_DETAILS[image_format]
        return {
            "content": content,
            "extension": extension,
            "content_type": content_type,
        }
    finally:
        uploaded_file.seek(0)


def _new_object_key(tipo, extension):
    return f"slideshow-promos/{tipo}/{uuid.uuid4().hex}.{extension}"


def _delete_new_object_best_effort(object_key, *, promo_id=None):
    try:
        eliminar_objeto(object_key)
    except Exception:
        logger.error(
            "slideshow promo compensating cleanup failed",
            extra={"promo_id": promo_id},
        )


def reemplazar_imagen_promo(promo, uploaded_file):
    """Sube y hace visible una imagen sin romper la referencia anterior."""
    image_data = validar_imagen_promo(uploaded_file)
    object_key = _new_object_key(promo.tipo, image_data["extension"])

    try:
        get_r2_client().put_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=object_key,
            Body=image_data["content"],
            ContentType=image_data["content_type"],
        )
    except Exception:
        raise SlideshowPromoStorageError(
            "No fue posible subir la imagen promocional."
        ) from None

    old_key = promo.imagen_key
    if old_key == "__pending_admin_upload__":
        old_key = ""
    promo.imagen_key = object_key
    try:
        with transaction.atomic():
            promo.save()
            if old_key and old_key != object_key:
                transaction.on_commit(
                    lambda: _delete_old_object_after_commit(
                        old_key, promo.pk, promo.tipo
                    )
                )
    except Exception:
        _delete_new_object_best_effort(object_key, promo_id=promo.pk)
        raise

    return promo


def _delete_old_object_after_commit(object_key, promo_id, promo_tipo):
    try:
        eliminar_objeto(object_key)
    except Exception:
        logger.error(
            "slideshow promo previous object cleanup failed",
            extra={"promo_id": promo_id, "promo_tipo": promo_tipo},
        )


def generar_preview_promo(imagen_key):
    if not imagen_key:
        return None
    return generar_url_lectura(imagen_key)
