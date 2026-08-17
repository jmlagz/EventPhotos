from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .r2 import generar_url_subida

from .models import Evento, Mesa, Foto


def evento_publico(request, slug):
    evento = get_object_or_404(
        Evento,
        slug=slug,
        estado__in=[
            Evento.Estado.ACTIVE,
            Evento.Estado.CLOSED,
        ],
    )

    return render(
        request,
        "eventos/evento_publico.html",
        {
            "evento": evento,
        },
    )


def mesa_publica(request, slug, token):
    evento = get_object_or_404(
        Evento,
        slug=slug,
        estado__in=[
            Evento.Estado.ACTIVE,
            Evento.Estado.CLOSED,
        ],
    )

    mesa = get_object_or_404(
        Mesa,
        evento=evento,
        token=token,
        activa=True,
    )

    # Si ya tenemos una sesión autorizada para esta mesa,
    # no necesitamos pedir nuevamente el código.
    if request.session.get("mesa_id") == mesa.id:
        return render(
            request,
            "eventos/mesa_autorizada.html",
            {
                "evento": evento,
                "mesa": mesa,
                "instrucciones_aceptadas": request.session.get(
                    "instrucciones_aceptadas",
                    False,
                ),
            },
        )

    return render(
        request,
        "eventos/mesa_publica.html",
        {
            "evento": evento,
            "mesa": mesa,
        },
    )


def verificar_acceso(request, slug, token):
    evento = get_object_or_404(
        Evento,
        slug=slug,
        estado__in=[
            Evento.Estado.ACTIVE,
            Evento.Estado.CLOSED,
        ],
    )

    mesa = get_object_or_404(
        Mesa,
        evento=evento,
        token=token,
        activa=True,
    )

    if request.method == "POST":
        codigo = request.POST.get("codigo", "").strip()

        if codigo == mesa.codigo_acceso:
            # Rotamos la clave de sesión para evitar session fixation.
            request.session.cycle_key()

            request.session["mesa_id"] = mesa.id
            request.session["evento_id"] = evento.id

            # La sesión durará 4 horas.
            request.session.set_expiry(60 * 60 * 4)

            return redirect(
                "mesa_publica",
                slug=evento.slug,
                token=mesa.token,
            )

        return render(
            request,
            "eventos/verificar_acceso.html",
            {
                "evento": evento,
                "mesa": mesa,
                "error": "El código no es correcto.",
            },
        )

    return render(
        request,
        "eventos/verificar_acceso.html",
        {
            "evento": evento,
            "mesa": mesa,
        },
    )

def instrucciones(request, slug, token):
    evento = get_object_or_404(
        Evento,
        slug=slug,
        estado__in=[
            Evento.Estado.ACTIVE,
            Evento.Estado.CLOSED,
        ],
    )

    mesa = get_object_or_404(
        Mesa,
        evento=evento,
        token=token,
        activa=True,
    )

    # Debe existir una sesión autorizada para esta mesa.
    if request.session.get("mesa_id") != mesa.id:
        return redirect(
            "verificar_acceso",
            slug=evento.slug,
            token=mesa.token,
        )

    # Si ya aceptó las instrucciones, no tiene sentido
    # mostrárselas nuevamente.
    if request.session.get("instrucciones_aceptadas"):
        return redirect(
            "mesa_publica",
            slug=evento.slug,
            token=mesa.token,
        )

    if request.method == "POST":
        acepto = request.POST.get("acepto")

        if acepto == "on":
            request.session["instrucciones_aceptadas"] = True

            return redirect(
                "mesa_publica",
                slug=evento.slug,
                token=mesa.token,
            )

    return render(
        request,
        "eventos/instrucciones.html",
        {
            "evento": evento,
            "mesa": mesa,
        },
    )


def subir_fotos(request, slug, token):
    evento = get_object_or_404(
        Evento,
        slug=slug,
        estado__in=[
            Evento.Estado.ACTIVE,
            Evento.Estado.CLOSED,
        ],
    )

    mesa = get_object_or_404(
        Mesa,
        evento=evento,
        token=token,
        activa=True,
    )

    # Debe existir una sesión autorizada.
    if request.session.get("mesa_id") != mesa.id:
        return redirect(
            "verificar_acceso",
            slug=evento.slug,
            token=mesa.token,
        )

    # Debe haber aceptado las instrucciones.
    if not request.session.get("instrucciones_aceptadas"):
        return redirect(
            "instrucciones",
            slug=evento.slug,
            token=mesa.token,
        )

    return render(
        request,
        "eventos/subir_fotos.html",
        {
            "evento": evento,
            "mesa": mesa,
        },
    )

@require_POST
def solicitar_url_subida(request, slug, token):
    evento = get_object_or_404(
        Evento,
        slug=slug,
        estado__in=[
            Evento.Estado.ACTIVE,
            Evento.Estado.CLOSED,
        ],
    )

    mesa = get_object_or_404(
        Mesa,
        evento=evento,
        token=token,
        activa=True,
    )

    # Debe existir una sesión autorizada para esta mesa.
    if request.session.get("mesa_id") != mesa.id:
        return JsonResponse(
            {"error": "No autorizado."},
            status=403,
        )

    # Debe haber aceptado las instrucciones.
    if not request.session.get("instrucciones_aceptadas"):
        return JsonResponse(
            {"error": "Debes aceptar las instrucciones."},
            status=403,
        )

    nombre = request.POST.get("nombre", "").strip()
    content_type = request.POST.get("content_type", "").strip()
    hash_sha256 = request.POST.get("hash_sha256", "").strip()

    if not nombre or not content_type or not hash_sha256:
        return JsonResponse(
            {"error": "Faltan datos de la foto."},
            status=400,
        )

    if len(hash_sha256) != 64:
        return JsonResponse(
            {"error": "Hash SHA-256 inválido."},
            status=400,
        )

    # Por ahora aceptamos solamente imágenes.
    tipos_permitidos = {
        "image/jpeg",
        "image/png",
        "image/heic",
        "image/heif",
    }

    if content_type not in tipos_permitidos:
        return JsonResponse(
            {"error": "Tipo de imagen no permitido."},
            status=400,
        )

    # Generamos una clave única para evitar colisiones.
    import uuid

    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else "jpg"

    if Foto.objects.filter(
        evento=evento,
        hash_sha256=hash_sha256,
    ).exists():
        return JsonResponse(
            {
                "duplicada": True,
                "mensaje": "Esta foto ya fue compartida en este evento.",
            }
        )

    object_key = (
        f"eventos/{evento.slug}/"
        f"mesas/{mesa.token}/"
        f"{uuid.uuid4().hex}.{extension}"
    )

    try:
        url = generar_url_subida(
            object_key=object_key,
            content_type=content_type,
        )
    except Exception:
        return JsonResponse(
            {"error": "No fue posible generar la URL de subida."},
            status=500,
        )

    return JsonResponse(
        {
            "url": url,
            "object_key": object_key,
        }
    )

@require_POST
def confirmar_subida(request, slug, token):
    evento = get_object_or_404(
        Evento,
        slug=slug,
        estado__in=[
            Evento.Estado.ACTIVE,
            Evento.Estado.CLOSED,
        ],
    )

    mesa = get_object_or_404(
        Mesa,
        evento=evento,
        token=token,
        activa=True,
    )

    if request.session.get("mesa_id") != mesa.id:
        return JsonResponse(
            {"error": "No autorizado."},
            status=403,
        )

    if not request.session.get("instrucciones_aceptadas"):
        return JsonResponse(
            {"error": "Debes aceptar las instrucciones."},
            status=403,
        )

    object_key = request.POST.get("object_key", "").strip()
    nombre = request.POST.get("nombre", "").strip()
    content_type = request.POST.get("content_type", "").strip()
    hash_sha256 = request.POST.get("hash_sha256", "").strip()
    tamaño = request.POST.get("tamaño", "").strip()

    if not all([
        object_key,
        nombre,
        content_type,
        hash_sha256,
        tamaño,
    ]):
        return JsonResponse(
            {"error": "Faltan datos para registrar la foto."},
            status=400,
        )

    # Segunda comprobación contra duplicados.
    foto_existente = Foto.objects.filter(
        evento=evento,
        hash_sha256=hash_sha256,
    ).first()

    if foto_existente:
        return JsonResponse(
            {
                "duplicada": True,
                "mensaje": "Esta foto ya fue compartida en este evento.",
            }
        )

    foto = Foto.objects.create(
        evento=evento,
        mesa=mesa,
        object_key=object_key,
        nombre_original=nombre,
        content_type=content_type,
        tamaño=int(tamaño),
        hash_sha256=hash_sha256,
    )

    return JsonResponse(
        {
            "ok": True,
            "foto_id": foto.id,
            "mensaje": "Foto registrada correctamente.",
        }
    )