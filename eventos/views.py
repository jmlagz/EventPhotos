import hashlib
import secrets
import io
import qrcode

from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse

from .r2 import (
    get_r2_client,
    generar_url_lectura,
    generar_url_subida,
    eliminar_objeto,
)

from .models import Evento, Mesa, Foto

def obtener_uploader_hash(request):
    uploader_token = request.session.get("uploader_token")

    if not uploader_token:
        uploader_token = secrets.token_urlsafe(32)
        request.session["uploader_token"] = uploader_token

    return hashlib.sha256(
        uploader_token.encode("utf-8")
    ).hexdigest()

def login_anfitrion(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        usuario = authenticate(
            request,
            username=username,
            password=password,
        )

        if usuario is not None:
            login(request, usuario)
            return redirect("dashboard")

        return render(
            request,
            "eventos/login.html",
            {
                "error": "Usuario o contraseña incorrectos.",
            },
        )

    return render(
        request,
        "eventos/login.html",
    )


@login_required
def logout_anfitrion(request):
    logout(request)
    return redirect("login_anfitrion")

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

    uploader_hash = obtener_uploader_hash(request)

    foto = Foto.objects.create(
        evento=evento,
        mesa=mesa,
        object_key=object_key,
        nombre_original=nombre,
        content_type=content_type,
        tamaño=int(tamaño),
        hash_sha256=hash_sha256,
        uploader_hash=uploader_hash,
        estado=Foto.Estado.APROBADA,
    )   

    return JsonResponse(
        {
            "ok": True,
            "foto_id": foto.id,
            "mensaje": "Foto registrada correctamente.",
        }
    )

@require_POST
def eliminar_foto(request, slug, foto_id):
    evento = get_object_or_404(
        Evento,
        slug=slug,
        estado__in=[
            Evento.Estado.ACTIVE,
            Evento.Estado.CLOSED,
        ],
    )

    foto = get_object_or_404(
        Foto,
        id=foto_id,
        evento=evento,
        eliminada_at__isnull=True,
    )

    uploader_hash = obtener_uploader_hash(request)

    if foto.uploader_hash != uploader_hash:
        return JsonResponse(
            {
                "error": "No tienes permiso para eliminar esta foto."
            },
            status=403,
        )

    try:
        r2 = get_r2_client()

        r2.delete_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=foto.object_key,
        )

    except Exception:
        return JsonResponse(
            {
                "error": "No fue posible eliminar la foto de almacenamiento."
            },
            status=500,
        )

    foto.eliminada_at = timezone.now()
    foto.save(
        update_fields=["eliminada_at"],
    )

    return JsonResponse(
        {
            "ok": True,
            "mensaje": "Foto eliminada correctamente.",
        }
    )

def album_publico(request, slug):
    evento = get_object_or_404(
        Evento,
        slug=slug,
        estado__in=[
            Evento.Estado.ACTIVE,
            Evento.Estado.CLOSED,
        ],
    )

    fotos = Foto.objects.filter(
        evento=evento,
        eliminada_at__isnull=True,
    ).select_related(
        "mesa",
    )

    uploader_hash = obtener_uploader_hash(request)

    fotos_album = []

    for foto in fotos:
        fotos_album.append(
            {
                "foto": foto,
                "url": generar_url_lectura(
                    foto.object_key
                ),
                        "puede_eliminar": (
                            foto.uploader_hash == uploader_hash
                        ),
            }
        )

    return render(
        request,
        "eventos/album_publico.html",
        {
            "evento": evento,
            "fotos": fotos_album,
        },
    )

@login_required
def dashboard(request):
    eventos = eventos_del_usuario(request)

    return render(
        request,
        "eventos/dashboard.html",
        {
            "eventos": eventos,
        },
    )

@login_required
def dashboard_evento(request, slug):
    evento = obtener_evento_del_usuario(request, slug)

    return render(
        request,
        "eventos/dashboard_evento.html",
        {
            "evento": evento,
        },
    )

@login_required
def fotos_dashboard(request, slug):
    evento = obtener_evento_del_usuario(request, slug)

    mesa_id = request.GET.get("mesa")

    fotos = (
        Foto.objects
        .filter(
            evento=evento,
            eliminada_at__isnull=True,
        )
        .select_related("mesa")
        .order_by("-creada_en")
    )

    if mesa_id:
        fotos = fotos.filter(mesa_id=mesa_id)

    mesas = evento.mesas.filter(
        activa=True,
    ).order_by("numero")

    fotos_con_url = []

    for foto in fotos:
        fotos_con_url.append(
            {
                "foto": foto,
                "url": generar_url_lectura(
                    foto.object_key,
                ),
            }
        )

    return render(
        request,
        "eventos/fotos_dashboard.html",
        {
            "evento": evento,
            "fotos": fotos_con_url,
            "mesas": mesas,
            "mesa_seleccionada": mesa_id,
        },
    )

@login_required
def mesas_dashboard(request, slug):
    evento = obtener_evento_del_usuario(request, slug)

    mesas = evento.mesas.filter(
        activa=True,
    )

    mesas_con_url = []

    for mesa in mesas:
        mesas_con_url.append(
            {
                "mesa": mesa,
                "url": request.build_absolute_uri(
                    f"/fotos/{evento.slug}/t/{mesa.token}/"
                ),
            }
        )

    return render(
        request,
        "eventos/mesas_dashboard.html",
        {
            "evento": evento,
            "mesas": mesas_con_url,
        },
    )

@login_required
def configurar_mesas(request, slug):

    if not request.user.is_superuser:
        return get_object_or_404(
            Evento,
            slug="__acceso_denegado__",
        )

    evento = get_object_or_404(
        Evento,
        slug=slug,
    )

    if request.method == "POST":

        try:
            numero_mesas = int(
                request.POST.get("numero_mesas", 0)
            )
        except (TypeError, ValueError):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Número de mesas inválido.",
                },
                status=400,
            )

        mesas_actuales = evento.mesas.count()

        if numero_mesas < mesas_actuales:
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "No se puede reducir el número "
                        "de mesas desde esta pantalla."
                    ),
                },
                status=400,
            )

        if numero_mesas > 100:
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "El máximo permitido es de 100 mesas."
                    ),
                },
                status=400,
            )

        for numero in range(
            mesas_actuales + 1,
            numero_mesas + 1,
        ):
            Mesa.objects.create(
                evento=evento,
                numero=numero,
            )

        return JsonResponse(
            {
                "ok": True,
                "total_mesas": evento.mesas.count(),
            }
        )

    mesas = evento.mesas.all()

    return render(
        request,
        "eventos/configurar_mesas.html",
        {
            "evento": evento,
            "mesas": mesas,
        },
    )

@login_required
def qr_mesa(request, slug, mesa_id):
    if not request.user.is_superuser:
        evento = obtener_evento_del_usuario(
            request,
            slug,
        )
    else:
        evento = get_object_or_404(
            Evento,
            slug=slug,
        )

    mesa = get_object_or_404(
        Mesa,
        id=mesa_id,
        evento=evento,
        activa=True,
    )

    url = request.build_absolute_uri(
        f"/fotos/{evento.slug}/t/{mesa.token}/"
    )

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )

    qr.add_data(url)
    qr.make(fit=True)

    imagen = qr.make_image(
        fill_color="black",
        back_color="white",
    )

    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")

    return HttpResponse(
        buffer.getvalue(),
        content_type="image/png",
    )

@login_required
def imprimir_qr_mesa(request, slug, mesa_id):
    if request.user.is_superuser:
        evento = get_object_or_404(
            Evento,
            slug=slug,
        )
    else:
        evento = obtener_evento_del_usuario(
            request,
            slug,
        )

    mesa = get_object_or_404(
        Mesa,
        id=mesa_id,
        evento=evento,
        activa=True,
    )

    url = request.build_absolute_uri(
        mesa.url_acceso
    )

    return render(
        request,
        "eventos/qr_mesa_imprimir.html",
        {
            "evento": evento,
            "mesa": mesa,
            "url": url,
        },
    )

@login_required
@require_POST
def actualizar_mesa(request, slug, mesa_id):
    evento = obtener_evento_del_usuario(request, slug)

    mesa = get_object_or_404(
        Mesa,
        id=mesa_id,
        evento=evento,
    )

    nombre = request.POST.get("nombre", "").strip()

    mesa.nombre = nombre
    mesa.save(update_fields=["nombre"])

    return JsonResponse(
        {
            "ok": True,
            "nombre": mesa.nombre,
        }
    )

@login_required
@require_POST
def eliminar_foto_dashboard(request, slug, foto_id):
    evento = obtener_evento_del_usuario(request, slug)

    foto = get_object_or_404(
        Foto,
        id=foto_id,
        evento=evento,
        eliminada_at__isnull=True,
    )

    eliminar_objeto(foto.object_key)

    foto.eliminada_at = timezone.now()
    foto.save(update_fields=["eliminada_at"])

    return JsonResponse(
        {
            "ok": True,
        }
    )

def eventos_del_usuario(request):
    if request.user.is_superuser:
        return Evento.objects.all()

    return Evento.objects.filter(
        propietario=request.user,
    )

def obtener_evento_del_usuario(request, slug):
    if request.user.is_superuser:
        return get_object_or_404(
            Evento,
            slug=slug,
        )

    return get_object_or_404(
        Evento,
        slug=slug,
        propietario=request.user,
    )

@login_required
def imprimir_qrs_mesas(request, slug):
    if request.user.is_superuser:
        evento = get_object_or_404(
            Evento,
            slug=slug,
        )
    else:
        evento = obtener_evento_del_usuario(
            request,
            slug,
        )

    mesas = evento.mesas.filter(
        activa=True,
    )

    return render(
        request,
        "eventos/qrs_mesas_imprimir.html",
        {
            "evento": evento,
            "mesas": mesas,
        },
    )