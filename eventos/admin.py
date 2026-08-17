from django.contrib import admin

from .models import Evento, Mesa, Foto


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