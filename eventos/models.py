import secrets
import string

from django.contrib.auth.models import User
from django.db import models
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

    propietario = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="eventos",
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

    permitir_videos = models.BooleanField(default=False)
    moderacion_activa = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nombre)

        super().save(*args, **kwargs)

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