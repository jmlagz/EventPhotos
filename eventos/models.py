import secrets
import string
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class Evento(models.Model):
    class Tipo(models.TextChoices):
        BODA = "boda", "Boda"
        XV_ANOS = "xv_anos", "XV años"
        CUMPLEANOS = "cumpleanos", "Cumpleaños"
        GRADUACION = "graduacion", "Graduación"
        CORPORATIVO = "corporativo", "Corporativo"
        OTRO = "otro", "Otro"

    class Estado(models.TextChoices):
        DRAFT = "draft", "Borrador"
        ACTIVE = "active", "Activo"
        CLOSED = "closed", "Cerrado"
        ARCHIVED = "archived", "Archivado"

    nombre = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)



    anfitriones = models.ManyToManyField(
        User,
        related_name="eventos_asignados",
        blank=True,
    )

    tipo = models.CharField(
        max_length=30,
        choices=Tipo.choices,
        default=Tipo.OTRO,
    )

    fecha = models.DateField()

    descripcion = models.TextField(blank=True)
    mensaje_bienvenida = models.TextField(blank=True)

    imagen_portada = models.ImageField(
        upload_to="eventos/portadas/",
        blank=True,
        null=True,
    )

    logo = models.ImageField(
        upload_to="eventos/logos/",
        blank=True,
        null=True,
    )

    imagen_portada_key = models.CharField(
        max_length=500,
        blank=True,
        default="",
    )

    logo_key = models.CharField(
        max_length=500,
        blank=True,
        default="",
    )

    color_principal = models.CharField(
        max_length=7,
        default="#000000",
    )

    color_secundario = models.CharField(
        max_length=7,
        default="#FFFFFF",
    )

    plantilla = models.CharField(
        max_length=50,
        default="default",
    )

    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.DRAFT,
    )

    fin_planeado = models.DateTimeField(
        blank=True,
        null=True,
    )

    timezone = models.CharField(
        max_length=64,
        blank=True,
        null=True,
    )

    upload_until = models.DateTimeField(
        blank=True,
        null=True,
    )

    available_until = models.DateTimeField(
        blank=True,
        null=True,
    )

    permitir_videos = models.BooleanField(default=False)
    moderacion_activa = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nombre)

        super().save(*args, **kwargs)

    def permite_carga(self, ahora=None):
        if self.estado != self.Estado.ACTIVE:
            return False

        if self.upload_until is None:
            return True

        if ahora is None:
            ahora = timezone.now()

        return ahora <= self.upload_until

    def permite_album_publico(self, ahora=None):
        if self.estado not in {
            self.Estado.ACTIVE,
            self.Estado.CLOSED,
        }:
            return False

        if self.available_until is None:
            return True

        if ahora is None:
            ahora = timezone.now()

        return ahora <= self.available_until

    def materializar_ciclo_temporal(
        self,
        fin_planeado,
        meses_disponibilidad,
    ):
        event_timezone = self._validar_fin_planeado(fin_planeado)

        if (
            not isinstance(meses_disponibilidad, int)
            or isinstance(meses_disponibilidad, bool)
            or meses_disponibilidad <= 0
        ):
            raise ValidationError(
                {
                    "meses_disponibilidad": (
                        "La duración de disponibilidad debe ser un número "
                        "entero positivo de meses."
                    )
                }
            )

        fin_local = fin_planeado.astimezone(event_timezone)
        self._materializar_ventana_carga(fin_planeado)
        available_until = fin_local + relativedelta(
            months=meses_disponibilidad
        )

        if available_until < fin_planeado:
            raise ValidationError(
                {
                    "available_until": (
                        "No puede ser anterior al fin planeado."
                    )
                }
            )

        self.available_until = available_until

    def materializar_ventana_carga(self, fin_planeado):
        self._validar_fin_planeado(fin_planeado)
        self._materializar_ventana_carga(fin_planeado)

    def _validar_fin_planeado(self, fin_planeado):
        if fin_planeado is None:
            raise ValidationError(
                {"fin_planeado": "El fin planeado es obligatorio."}
            )

        if not timezone.is_aware(fin_planeado):
            raise ValidationError(
                {"fin_planeado": "El fin planeado debe incluir zona horaria."}
            )

        if not self.timezone:
            raise ValidationError(
                {"timezone": "La zona horaria del evento es obligatoria."}
            )

        try:
            event_timezone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError(
                {"timezone": "La zona horaria del evento no es válida."}
            ) from exc

        return event_timezone

    def _materializar_ventana_carga(self, fin_planeado):
        upload_until = fin_planeado + timedelta(hours=48)

        if upload_until <= fin_planeado:
            raise ValidationError(
                {"upload_until": "Debe ser posterior al fin planeado."}
            )

        self.fin_planeado = fin_planeado
        self.upload_until = upload_until

    def __str__(self):
        return self.nombre

    class Meta:
        ordering = ["-fecha", "-created_at"]


class Mesa(models.Model):
    evento = models.ForeignKey(
        Evento,
        on_delete=models.CASCADE,
        related_name="mesas",
    )

    numero = models.PositiveIntegerField()
    nombre = models.CharField(
        max_length=100,
        blank=True,
    )

    token = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
    )

    codigo_acceso = models.CharField(
        max_length=6,
    )

    activa = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def generar_token(self):
        return secrets.token_urlsafe(32)

    def generar_codigo(self):
        caracteres = string.digits
        return "".join(secrets.choice(caracteres) for _ in range(6))

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = self.generar_token()

        if not self.codigo_acceso:
            self.codigo_acceso = self.generar_codigo()

        super().save(*args, **kwargs)

    @property
    def etiqueta(self):
        if self.nombre:
            return f"Mesa {self.numero} - {self.nombre}"
        return f"Mesa {self.numero}"

    @property
    def url_acceso(self):
        return f"/fotos/{self.evento.slug}/t/{self.token}/"

    def __str__(self):
        return self.etiqueta

    class Meta:
        ordering = ["numero"]
        constraints = [
            models.UniqueConstraint(
                fields=["evento", "numero"],
                name="unique_mesa_por_evento",
            )
        ]

class Foto(models.Model):

    class Estado(models.TextChoices):
        PENDIENTE = "pending", "Pendiente"
        APROBADA = "approved", "Aprobada"
        RECHAZADA = "rejected", "Rechazada"

    evento = models.ForeignKey(
        Evento,
        on_delete=models.CASCADE,
        related_name="fotos",
    )

    mesa = models.ForeignKey(
        Mesa,
        on_delete=models.CASCADE,
        related_name="fotos",
    )

    object_key = models.CharField(
        max_length=500,
    )

    nombre_original = models.CharField(
        max_length=255,
    )

    content_type = models.CharField(
        max_length=100,
    )

    tamaño = models.PositiveBigIntegerField()

    hash_sha256 = models.CharField(
        max_length=64,
    )

    # Identifica de forma anónima al navegador que subió la foto.
    # No guardamos el token original, solamente su hash.
    uploader_hash = models.CharField(
        max_length=64,
        blank=True,
    )

    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.APROBADA,
    )

    eliminada_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    creada_en = models.DateTimeField(
        auto_now_add=True,
    )

    def __str__(self):
        return self.nombre_original

    class Meta:
        ordering = ["-creada_en"]

        constraints = [
            models.UniqueConstraint(
                fields=["evento", "hash_sha256"],
                name="unique_foto_por_evento",
            )
        ]


class UploadIntent(models.Model):

    class Estado(models.TextChoices):
        PENDING = "pending", "Pendiente"
        FINALIZING = "finalizing", "Materializando"
        CONFIRMED = "confirmed", "Confirmada"
        CANCELLED = "cancelled", "Cancelada"
        EXPIRED = "expired", "Expirada"
        CLEANUP_PENDING = "cleanup_pending", "Limpieza pendiente"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    evento = models.ForeignKey(
        Evento,
        on_delete=models.CASCADE,
        related_name="upload_intents",
    )

    mesa = models.ForeignKey(
        Mesa,
        on_delete=models.CASCADE,
        related_name="upload_intents",
    )

    object_key = models.CharField(
        max_length=500,
        unique=True,
    )

    final_object_key = models.CharField(
        max_length=500,
        unique=True,
        null=True,
        blank=True,
    )

    nombre_original = models.CharField(max_length=255)
    content_type_declarado = models.CharField(max_length=100)
    tamaño_declarado = models.PositiveBigIntegerField()
    hash_declarado = models.CharField(max_length=64)

    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    tamaño_real = models.PositiveBigIntegerField(null=True, blank=True)
    source_etag = models.CharField(max_length=128, null=True, blank=True)
    finalizing_at = models.DateTimeField(null=True, blank=True)
    cleaned_at = models.DateTimeField(null=True, blank=True)

    foto = models.OneToOneField(
        Foto,
        on_delete=models.SET_NULL,
        related_name="upload_intent",
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"Intento de subida {self.id}"


class InvitacionAnfitrion(models.Model):
    usuario = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="invitaciones_anfitrion",
    )

    evento = models.ForeignKey(
        Evento,
        on_delete=models.CASCADE,
        related_name="invitaciones_anfitrion",
    )

    token = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
    )

    creada_en = models.DateTimeField(
        auto_now_add=True,
    )

    expira_en = models.DateTimeField()

    usada_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    def generar_token(self):
        return secrets.token_urlsafe(32)

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = self.generar_token()

        super().save(*args, **kwargs)

    @property
    def esta_usada(self):
        return self.usada_en is not None

    @property
    def esta_expirada(self):
        from django.utils import timezone
        return timezone.now() >= self.expira_en

    def __str__(self):
        return f"Invitación de {self.usuario.username} - {self.evento.nombre}"

class InvitacionUsuario(models.Model):

    usuario = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="invitaciones_usuario",
    )

    token = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
    )

    creada_en = models.DateTimeField(
        auto_now_add=True,
    )

    expira_en = models.DateTimeField()

    usada_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    def generar_token(self):
        return secrets.token_urlsafe(32)

    def save(self, *args, **kwargs):

        if not self.token:
            self.token = self.generar_token()

        super().save(*args, **kwargs)

    @property
    def esta_usada(self):
        return self.usada_en is not None

    @property
    def esta_expirada(self):
        from django.utils import timezone

        return timezone.now() >= self.expira_en

    def __str__(self):
        return (
            f"Invitación de {self.usuario.username}"
        )
