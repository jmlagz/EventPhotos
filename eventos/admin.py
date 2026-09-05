from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.html import format_html

from .models import Evento, Foto, Mesa, SlideshowPromo
from .services.slideshow_promos import (
    SlideshowPromoStorageError,
    generar_preview_promo,
    reemplazar_imagen_promo,
    validar_imagen_promo,
)


@admin.register(Evento)
class EventoAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "tipo",
        "fecha",
        "estado",
        "mesas_count",
        "created_at",
    )

    list_filter = (
        "tipo",
        "estado",
    )

    search_fields = (
        "nombre",
        "slug",
    )

    prepopulated_fields = {
        "slug": ("nombre",),
    }

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    def mesas_count(self, obj):
        return obj.mesas.count()

    mesas_count.short_description = "Mesas"


@admin.register(Mesa)
class MesaAdmin(admin.ModelAdmin):
    list_display = (
        "numero",
        "nombre",
        "evento",
        "codigo_acceso",
        "activa",
        "created_at",
    )

    list_filter = (
        "activa",
        "evento",
    )

    search_fields = (
        "nombre",
        "evento__nombre",
        "token",
        "codigo_acceso",
    )

    readonly_fields = (
        "token",
        "codigo_acceso",
        "created_at",
    )

@admin.register(Foto)
class FotoAdmin(admin.ModelAdmin):
    list_display = (
        "nombre_original",
        "evento",
        "mesa",
        "content_type",
        "tamaño",
        "creada_en",
    )

    list_filter = (
        "evento",
        "mesa",
        "content_type",
    )

    search_fields = (
        "nombre_original",
        "object_key",
        "hash_sha256",
    )

    readonly_fields = (
        "evento",
        "mesa",
        "object_key",
        "nombre_original",
        "content_type",
        "tamaño",
        "hash_sha256",
        "creada_en",
    )

    ordering = (
        "-creada_en",
    )


class SlideshowPromoAdminForm(forms.ModelForm):
    imagen = forms.FileField(label="Subir o reemplazar imagen", required=False)

    class Meta:
        model = SlideshowPromo
        fields = ("tipo", "titulo_interno", "activa", "orden")

    def clean(self):
        cleaned_data = super().clean()
        imagen = cleaned_data.get("imagen")
        activa = cleaned_data.get("activa")

        if imagen:
            # ModelForm asigna los valores al modelo después de clean(); el
            # servicio necesita el tipo nuevo para construir el prefijo.
            self.instance.tipo = cleaned_data["tipo"]
            validar_imagen_promo(imagen)
            imagen.seek(0)
            if not self.instance.imagen_key:
                self.instance.imagen_key = "__pending_admin_upload__"
        elif activa and not self.instance.imagen_key:
            self.add_error(
                "imagen",
                "Una promo activa requiere una imagen.",
            )

        return cleaned_data


@admin.register(SlideshowPromo)
class SlideshowPromoAdmin(admin.ModelAdmin):
    form = SlideshowPromoAdminForm
    list_display = ("tipo", "titulo_interno", "activa", "orden", "preview")
    list_filter = ("activa", "tipo")
    ordering = ("orden", "tipo")
    readonly_fields = ("preview", "creada_en", "actualizada_en")
    fields = (
        "tipo",
        "titulo_interno",
        "activa",
        "orden",
        "imagen",
        "preview",
        "creada_en",
        "actualizada_en",
    )

    def has_module_permission(self, request):
        return request.user.is_authenticated and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_authenticated and request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_authenticated and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_authenticated and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Imagen actual")
    def preview(self, obj):
        if not obj or not obj.imagen_key:
            return "Sin imagen"
        try:
            url = generar_preview_promo(obj.imagen_key)
        except Exception:
            return "Vista previa no disponible"
        return format_html(
            '<img src="{}" alt="Vista previa de promo" style="max-width: 320px; max-height: 180px;" />',
            url,
        )

    def save_model(self, request, obj, form, change):
        imagen = form.cleaned_data.get("imagen")
        if not imagen:
            obj.save()
            return
        try:
            reemplazar_imagen_promo(obj, imagen)
        except SlideshowPromoStorageError as exc:
            raise ValidationError(str(exc)) from exc
