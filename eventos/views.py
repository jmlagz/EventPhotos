import hashlib
import secrets
import io
import zipfile
import qrcode



from django.db.models import Sum
from django.contrib import messages
from datetime import timedelta
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
from django.template.loader import render_to_string

from django.contrib.auth.views import (
    PasswordResetView,
    PasswordResetDoneView,
    PasswordResetConfirmView,
    PasswordResetCompleteView,
)

from .limites import (
    MAX_FOTOS_POR_EVENTO,
    MAX_STORAGE_POR_EVENTO,
    MAX_TAMANO_FOTO,
    MAX_STORAGE_PRUEBAS,
    MAX_TAMANO_PERSONALIZACION,
)

from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (
    PasswordResetView,
    PasswordResetDoneView,
    PasswordResetConfirmView,
    PasswordResetCompleteView,
)

from .models import (
    Evento,
    Mesa,
    Foto,
    InvitacionAnfitrion,
    InvitacionUsuario,
)

from .r2 import (
    get_r2_client,
    generar_url_lectura,
    generar_url_subida,
    eliminar_objeto,
    obtener_objeto,
)

from .forms import (
    EventoForm,
    UsuarioForm,
    ActivarCuentaForm,
)

def obtener_uploader_hash(request):
    uploader_token = request.session.get("uploader_token")

    if not uploader_token:
        uploader_token = secrets.token_urlsafe(32)
        request.session["uploader_token"] = uploader_token

    return hashlib.sha256(
        uploader_token.encode("utf-8")
    ).hexdigest()

password_reset_request = PasswordResetView.as_view(
    template_name="eventos/password_reset.html",
    email_template_name="eventos/password_reset_email.html",
    subject_template_name="eventos/password_reset_subject.txt",
    success_url="/password-reset/done/",
)

password_reset_done = PasswordResetDoneView.as_view(
    template_name="eventos/password_reset_done.html",
)

password_reset_confirm = PasswordResetConfirmView.as_view(
    template_name="eventos/password_reset_confirm.html",
    success_url="/reset/done/",
)

password_reset_complete = PasswordResetCompleteView.as_view(
    template_name="eventos/password_reset_complete.html",
)

def login_anfitrion(request):

    if request.user.is_authenticated:

        if request.user.is_superuser:
            return redirect("dashboard")

        eventos = Evento.objects.filter(
            anfitriones=request.user,
        )

        if eventos.count() == 1:

            evento = eventos.first()

            return redirect(
                "dashboard_evento",
                slug=evento.slug,
            )

        return redirect("dashboard_anfitrion")


    if request.method == "POST":

        username = request.POST.get(
            "username",
            "",
        ).strip()

        password = request.POST.get(
            "password",
            "",
        )


        usuario = authenticate(
            request,
            username=username,
            password=password,
        )


        if usuario is not None:

            login(
                request,
                usuario,
            )


            if usuario.is_superuser:
                return redirect("dashboard")


            eventos = Evento.objects.filter(
                anfitriones=usuario,
            )


            if eventos.count() == 1:

                evento = eventos.first()

                return redirect(
                    "dashboard_evento",
                    slug=evento.slug,
                )


            return redirect(
                "dashboard_anfitrion",
            )


        return render(
            request,
            "eventos/login.html",
            {
                "error":
                    "Usuario/Correo o contraseña incorrectos.",
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

    imagen_portada_url = None

    if evento.imagen_portada_key:
        imagen_portada_url = generar_url_lectura(
            evento.imagen_portada_key
        )

    logo_url = None

    if evento.logo_key:
        logo_url = generar_url_lectura(
            evento.logo_key
        )

    return render(
        request,
        "eventos/evento_publico.html",
        {
            "evento": evento,
            "imagen_portada_url": imagen_portada_url,
            "logo_url": logo_url,
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

    # Evento cerrado:
    # no permite iniciar ni continuar el flujo de subida.
    if evento.estado == Evento.Estado.CLOSED:
        return render(
            request,
            "eventos/evento_cerrado.html",
            {
                "evento": evento,
                "mesa": mesa,
            },
        )

    # Si la sesión ya está autorizada y ya aceptó
    # las instrucciones, puede ir directamente a subir fotos.
    if (
        request.session.get("mesa_id") == mesa.id
        and request.session.get("instrucciones_aceptadas")
    ):
        return redirect(
            "subir_fotos",
            slug=evento.slug,
            token=mesa.token,
        )

    if request.method == "POST":

        codigo = request.POST.get(
            "codigo",
            "",
        ).strip()

        acepto = request.POST.get("acepto")

        errores = []

        # Si todavía no existe una sesión autorizada,
        # debemos comprobar el código.
        if request.session.get("mesa_id") != mesa.id:

            if codigo != mesa.codigo_acceso:
                errores.append(
                    "El código de acceso no es correcto."
                )

        # El consentimiento siempre debe existir.
        if acepto != "on":
            errores.append(
                "Debes confirmar que entiendes y aceptas "
                "compartir tus fotos."
            )

        if not errores:

            # Solo rotamos la sesión cuando estamos
            # autorizando una mesa por primera vez.
            if request.session.get("mesa_id") != mesa.id:

                request.session.cycle_key()

                request.session["mesa_id"] = mesa.id
                request.session["evento_id"] = evento.id

                # La sesión durará 4 horas.
                request.session.set_expiry(
                    60 * 60 * 4
                )

            request.session["instrucciones_aceptadas"] = True

            return redirect(
                "subir_fotos",
                slug=evento.slug,
                token=mesa.token,
            )

        return render(
            request,
            "eventos/mesa_publica.html",
            {
                "evento": evento,
                "mesa": mesa,
                "errores": errores,
                "codigo_ingresado": codigo,
            },
        )

    return render(
        request,
        "eventos/mesa_publica.html",
        {
            "evento": evento,
            "mesa": mesa,
            "errores": [],
            "codigo_ingresado": "",
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

    if evento.estado == Evento.Estado.CLOSED:
        return render(
            request,
            "eventos/evento_cerrado.html",
            {
                "evento": evento,
            },
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
            "mesa_publica",
            slug=evento.slug,
            token=mesa.token,
        )

    # Debe haber aceptado el consentimiento.
    if not request.session.get("instrucciones_aceptadas"):
        return redirect(
            "mesa_publica",
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

    # Los eventos cerrados ya no aceptan nuevas fotos.
    if evento.estado == Evento.Estado.CLOSED:
        return JsonResponse(
            {"error": "Este evento ya está cerrado y no acepta nuevas fotos."},
            status=403,
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
            {"error": "Debes aceptar el consentimiento para compartir fotos."},
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

        # Verificamos el tamaño informado por el navegador
        # como primera barrera. La validación definitiva
        # se hará contra R2 al confirmar la subida.
        tamaño = request.POST.get("tamaño", "").strip()

        try:
            tamaño = int(tamaño)
        except (TypeError, ValueError):
            return JsonResponse(
                {"error": "Tamaño de archivo inválido."},
                status=400,
            )

        if tamaño <= 0:
            return JsonResponse(
                {"error": "El tamaño de la foto no es válido."},
                status=400,
            )

        if tamaño > MAX_TAMANO_FOTO:
            return JsonResponse(
                {
                    "error": (
                        "La foto supera el tamaño máximo permitido "
                        "de 15 MB."
                    )
                },
                status=400,
            )

        # Contamos únicamente las fotos que siguen activas.
        fotos_actuales = Foto.objects.filter(
            evento=evento,
            eliminada_at__isnull=True,
        ).count()

        if fotos_actuales >= MAX_FOTOS_POR_EVENTO:
            return JsonResponse(
                {
                    "error": (
                        "Este evento ha alcanzado el límite "
                        "de 450 fotos."
                    )
                },
                status=400,
            )

        # Calculamos el almacenamiento actualmente utilizado.
        almacenamiento_actual = (
            Foto.objects
            .filter(
                evento=evento,
                eliminada_at__isnull=True,
            )
            .aggregate(total=Sum("tamaño"))
            .get("total")
            or 0
        )

        if almacenamiento_actual + tamaño > MAX_STORAGE_POR_EVENTO:
            return JsonResponse(
                {
                    "error": (
                        "Este evento ha alcanzado su límite "
                        "de almacenamiento."
                    )
                },
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

    # Los eventos cerrados ya no aceptan nuevas fotos.
    if evento.estado == Evento.Estado.CLOSED:
        return JsonResponse(
            {"error": "Este evento ya está cerrado y no acepta nuevas fotos."},
            status=403,
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

    if not all([
        object_key,
        nombre,
        content_type,
        hash_sha256,
    ]):
        return JsonResponse(
            {"error": "Faltan datos para registrar la foto."},
            status=400,
        )

    # El object_key debe pertenecer a este evento y esta mesa.
    prefijo_esperado = (
        f"eventos/{evento.slug}/"
        f"mesas/{mesa.token}/"
    )

    if not object_key.startswith(prefijo_esperado):
        return JsonResponse(
            {"error": "Objeto de almacenamiento no válido."},
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

    # Consultamos R2 para obtener el tamaño REAL del archivo.
    try:
        r2 = get_r2_client()

        objeto = r2.head_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=object_key,
        )

        tamaño_real = objeto["ContentLength"]

    except Exception:
        return JsonResponse(
            {
                "error": (
                    "No fue posible verificar la foto "
                    "en el almacenamiento."
                )
            },
            status=400,
        )

    # Verificamos nuevamente el tamaño máximo individual.
    if tamaño_real > MAX_TAMANO_FOTO:
        try:
            r2.delete_object(
                Bucket=settings.R2_BUCKET_NAME,
                Key=object_key,
            )
        except Exception:
            pass

        return JsonResponse(
            {
                "error": (
                    "La foto supera el tamaño máximo "
                    "permitido de 15 MB."
                )
            },
            status=400,
        )

    # Contamos las fotos activas actuales.
    fotos_actuales = Foto.objects.filter(
        evento=evento,
        eliminada_at__isnull=True,
    ).count()

    if fotos_actuales >= MAX_FOTOS_POR_EVENTO:
        try:
            r2.delete_object(
                Bucket=settings.R2_BUCKET_NAME,
                Key=object_key,
            )
        except Exception:
            pass

        return JsonResponse(
            {
                "error": (
                    "Este evento ha alcanzado el límite "
                    "de 450 fotos."
                )
            },
            status=400,
        )

    # Calculamos el almacenamiento real actualmente utilizado.
    almacenamiento_actual = (
        Foto.objects
        .filter(
            evento=evento,
            eliminada_at__isnull=True,
        )
        .aggregate(total=Sum("tamaño"))
        .get("total")
        or 0
    )

    if (
        almacenamiento_actual + tamaño_real
        > MAX_STORAGE_POR_EVENTO
    ):
        try:
            r2.delete_object(
                Bucket=settings.R2_BUCKET_NAME,
                Key=object_key,
            )
        except Exception:
            pass

        return JsonResponse(
            {
                "error": (
                    "Este evento ha alcanzado su límite "
                    "de almacenamiento."
                )
            },
            status=400,
        )

    uploader_hash = obtener_uploader_hash(request)

    foto = Foto.objects.create(
        evento=evento,
        mesa=mesa,
        object_key=object_key,
        nombre_original=nombre,
        content_type=content_type,
        tamaño=tamaño_real,
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

def home(request):
    return render(
        request,
        "eventos/home.html",
    )

@login_required
def dashboard(request):

    if not request.user.is_superuser:
        return redirect("dashboard_anfitrion")

    eventos = Evento.objects.all()

    total_eventos = eventos.count()

    total_fotos = Foto.objects.filter(
        evento__in=eventos,
        eliminada_at__isnull=True,
    ).count()

    almacenamiento_usado = (
        Foto.objects.filter(
            evento__in=eventos,
            eliminada_at__isnull=True,
        )
        .aggregate(total=Sum("tamaño"))
        .get("total")
        or 0
    )

    porcentaje_almacenamiento = (
        almacenamiento_usado
        / MAX_STORAGE_PRUEBAS
        * 100
    )

    for evento in eventos:
        fotos_evento = Foto.objects.filter(
            evento=evento,
            eliminada_at__isnull=True,
        )

        evento.total_fotos = fotos_evento.count()

        evento.almacenamiento_usado = (
            fotos_evento
            .aggregate(total=Sum("tamaño"))
            .get("total")
            or 0
        )

        evento.porcentaje_fotos = (
            evento.total_fotos
            / MAX_FOTOS_POR_EVENTO
            * 100
        )

        evento.porcentaje_almacenamiento = (
            evento.almacenamiento_usado
            / MAX_STORAGE_POR_EVENTO
            * 100
        )

    return render(
        request,
        "eventos/dashboard.html",
        {
            "eventos": eventos,
            "total_eventos": total_eventos,
            "total_fotos": total_fotos,
            "almacenamiento_usado": almacenamiento_usado,
            "porcentaje_almacenamiento": porcentaje_almacenamiento,
            "max_storage_pruebas": MAX_STORAGE_PRUEBAS,
        },
    )

@login_required
def dashboard_anfitrion(request):

    if request.user.is_superuser:
        return redirect("dashboard")

    eventos = Evento.objects.filter(
        anfitriones=request.user,
    )

    for evento in eventos:

        fotos_evento = Foto.objects.filter(
            evento=evento,
            eliminada_at__isnull=True,
        )

        evento.total_fotos = fotos_evento.count()

        evento.almacenamiento_usado = (
            fotos_evento
            .aggregate(total=Sum("tamaño"))
            .get("total")
            or 0
        )

        evento.porcentaje_fotos = (
            evento.total_fotos
            / MAX_FOTOS_POR_EVENTO
            * 100
        )

        evento.porcentaje_almacenamiento = (
            evento.almacenamiento_usado
            / MAX_STORAGE_POR_EVENTO
            * 100
        )

    return render(
        request,
        "eventos/dashboard_anfitrion.html",
        {
            "eventos": eventos,
        },
    )

@login_required
def crear_evento(request):

    if not request.user.is_superuser:
        return HttpResponse(
            "No tienes permiso para crear eventos.",
            status=403,
        )

    if request.method == "POST":

        form = EventoForm(request.POST)

        if form.is_valid():

            evento = form.save(commit=False)
            evento.estado = Evento.Estado.DRAFT

            evento.save()

            # El usuario que crea el evento queda como anfitrión.
            evento.anfitriones.add(request.user)

            messages.success(
                request,
                "Evento creado correctamente."
            )

            return redirect(
                "dashboard_evento",
                slug=evento.slug,
            )

    else:
        form = EventoForm()

    return render(
        request,
        "eventos/crear_evento.html",
        {
            "form": form,
        },
    )

@login_required
def confirmar_personalizacion(request, slug):
    evento = obtener_evento_del_usuario(request, slug)

    if request.method != "POST":
        return JsonResponse(
            {"error": "Método no permitido."},
            status=405,
        )

    tipo = request.POST.get("tipo", "").strip()
    object_key = request.POST.get("object_key", "").strip()

    if tipo not in {"portada", "logo"}:
        return JsonResponse(
            {"error": "Tipo de personalización no válido."},
            status=400,
        )

    if not object_key:
        return JsonResponse(
            {"error": "Falta la referencia del archivo."},
            status=400,
        )

    prefijo_esperado = (
        f"eventos/{evento.slug}/"
        f"personalizacion/{tipo}/"
    )

    if not object_key.startswith(prefijo_esperado):
        return JsonResponse(
            {"error": "Objeto de almacenamiento no válido."},
            status=400,
        )

    try:
        r2 = get_r2_client()

        objeto = r2.head_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=object_key,
        )

    except Exception:
        return JsonResponse(
            {
                "error": (
                    "No fue posible verificar la imagen "
                    "en el almacenamiento."
                )
            },
            status=400,
        )

    content_type = objeto.get("ContentType", "")

    tipos_permitidos = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    if content_type not in tipos_permitidos:
        try:
            r2.delete_object(
                Bucket=settings.R2_BUCKET_NAME,
                Key=object_key,
            )
        except Exception:
            pass

        return JsonResponse(
            {"error": "El archivo no es una imagen válida."},
            status=400,
        )

    if tipo == "portada":
        evento.imagen_portada_key = object_key

    else:
        evento.logo_key = object_key

    evento.save(
        update_fields=[
            "imagen_portada_key"
            if tipo == "portada"
            else "logo_key",
            "updated_at",
        ]
    )

    return JsonResponse(
        {
            "ok": True,
            "tipo": tipo,
            "object_key": object_key,
        }
    )


@login_required
def solicitar_url_personalizacion(request, slug):
    evento = obtener_evento_del_usuario(request, slug)

    if request.method != "POST":
        return JsonResponse(
            {"error": "Método no permitido."},
            status=405,
        )

    tipo = request.POST.get("tipo", "").strip()
    nombre = request.POST.get("nombre", "").strip()
    content_type = request.POST.get("content_type", "").strip()

    tamaño = request.POST.get("tamaño", "").strip()

    try:
        tamaño = int(tamaño)
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "Tamaño de archivo inválido."},
            status=400,
        )

    if tamaño <= 0:
        return JsonResponse(
            {"error": "El archivo está vacío."},
            status=400,
        )

    if tamaño > MAX_TAMANO_PERSONALIZACION:
        return JsonResponse(
            {
                "error": (
                    "La imagen es demasiado grande. "
                    "El límite es de 5 MB."
                )
            },
            status=400,
        )

    if tipo not in {"portada", "logo"}:
        return JsonResponse(
            {"error": "Tipo de personalización no válido."},
            status=400,
        )

    if not nombre or not content_type:
        return JsonResponse(
            {"error": "Faltan datos de la imagen."},
            status=400,
        )

    tipos_permitidos = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    if content_type not in tipos_permitidos:
        return JsonResponse(
            {"error": "Tipo de imagen no permitido."},
            status=400,
        )

    import uuid

    extension = (
        nombre.rsplit(".", 1)[-1].lower()
        if "." in nombre
        else "jpg"
    )

    object_key = (
        f"eventos/{evento.slug}/"
        f"personalizacion/{tipo}/"
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

def activar_cuenta(request, token):

    # Primero buscamos en el nuevo sistema de invitaciones.
    invitacion_usuario = (
        InvitacionUsuario.objects
        .filter(token=token)
        .select_related("usuario")
        .first()
    )

    # Si no existe, buscamos en el sistema anterior.
    invitacion_anfitrion = None

    if invitacion_usuario is None:

        invitacion_anfitrion = (
            InvitacionAnfitrion.objects
            .filter(token=token)
            .select_related(
                "usuario",
                "evento",
            )
            .first()
        )

        if invitacion_anfitrion is None:
            return HttpResponse(
                "La invitación no es válida.",
                status=404,
            )

        usuario = invitacion_anfitrion.usuario
        invitacion = invitacion_anfitrion

    else:

        usuario = invitacion_usuario.usuario
        invitacion = invitacion_usuario


    # -----------------------------------------
    # VALIDAR INVITACIÓN
    # -----------------------------------------

    if invitacion.esta_usada:

        return render(
            request,
            "eventos/invitacion_usada.html",
            {
                "invitacion": invitacion,
            },
        )


    if invitacion.esta_expirada:

        return render(
            request,
            "eventos/invitacion_expirada.html",
            {
                "invitacion": invitacion,
            },
        )


    # -----------------------------------------
    # ACTIVAR CUENTA
    # -----------------------------------------

    if request.method == "POST":

        form = ActivarCuentaForm(request.POST)

        if form.is_valid():

            usuario.set_password(
                form.cleaned_data["password"]
            )

            usuario.is_active = True

            usuario.save(
                update_fields=[
                    "password",
                    "is_active",
                ]
            )

            invitacion.usada_en = timezone.now()

            invitacion.save(
                update_fields=[
                    "usada_en",
                ]
            )

            login(
                request,
                usuario,
            )

            messages.success(
                request,
                "Tu cuenta fue activada correctamente.",
            )

            # -----------------------------------------
            # REDIRECCIÓN SEGÚN ROL
            # -----------------------------------------

            if usuario.is_superuser:

                return redirect(
                    "dashboard",
                )


            eventos = Evento.objects.filter(
                anfitriones=usuario,
            )


            if eventos.count() == 1:

                evento = eventos.first()

                return redirect(
                    "dashboard_evento",
                    slug=evento.slug,
                )


            return redirect(
                "dashboard_anfitrion",
            )


    else:

        form = ActivarCuentaForm()


    return render(
        request,
        "eventos/activar_cuenta.html",
        {
            "form": form,
            "invitacion": invitacion,
            "usuario": usuario,
        },
    )

@login_required
def crear_usuario(request):

    if not request.user.is_superuser:
        return HttpResponse(
            "No tienes permiso para crear usuarios.",
            status=403,
        )

    evento_preseleccionado = None

    evento_slug = request.GET.get("evento")

    if evento_slug:
        evento_preseleccionado = get_object_or_404(
            Evento,
            slug=evento_slug,
        )

    if request.method == "POST":

        form = UsuarioForm(request.POST)

        if form.is_valid():

            usuario = form.save()

            rol = form.cleaned_data["rol"]
            eventos = form.cleaned_data["eventos"]

            # -----------------------------------------
            # ANFITRIÓN
            # -----------------------------------------

            if rol == UsuarioForm.ROL_ANFITRION:

                for evento in eventos:
                    evento.anfitriones.add(usuario)

            # -----------------------------------------
            # INVITACIÓN DE ACTIVACIÓN
            # -----------------------------------------

            invitacion = InvitacionUsuario.objects.create(
                usuario=usuario,
                expira_en=timezone.now() + timedelta(days=3),
            )

            enlace_activacion = request.build_absolute_uri(
                reverse(
                    "activar_cuenta",
                    kwargs={
                        "token": invitacion.token,
                    },
                )
            )

            # -----------------------------------------
            # CORREO
            # -----------------------------------------

            if rol == UsuarioForm.ROL_ADMIN:

                mensaje_texto = (
                    f"Hola {usuario.first_name},\n\n"
                    "Se ha creado una cuenta para ti en "
                    "EventPhotos como administrador.\n\n"
                    f"Usuario: {usuario.email}\n\n"
                    "Tu correo electrónico también será tu "
                    "nombre de usuario para iniciar sesión.\n\n"
                    "Puedes activar tu cuenta y crear tu "
                    "contraseña utilizando el siguiente enlace:\n\n"
                    f"{enlace_activacion}\n\n"
                    "Este enlace tiene una vigencia de 3 días.\n\n"
                    "EventPhotos"
                )

                mensaje_html = render_to_string(
                    "eventos/invitacion_admin.html",
                    {
                        "usuario": usuario,
                        "enlace_activacion": enlace_activacion,
                    },
                )

                asunto = (
                    "Activa tu cuenta de administrador de EventPhotos"
                )

                contexto_mensaje = {
                    "usuario": usuario,
                    "enlace_activacion": enlace_activacion,
                }

            else:

                nombres_eventos = "\n".join(
                    f"- {evento.nombre}"
                    for evento in eventos
                )

                mensaje_texto = (
                    f"Hola {usuario.first_name},\n\n"
                    "Se ha creado una cuenta para ti en "
                    "EventPhotos como anfitrión.\n\n"
                    "Eventos asignados:\n"
                    f"{nombres_eventos}\n\n"
                    f"Usuario: {usuario.email}\n\n"
                    "Tu correo electrónico también será tu "
                    "nombre de usuario para iniciar sesión.\n\n"
                    "Puedes activar tu cuenta y crear tu "
                    "contraseña utilizando el siguiente enlace:\n\n"
                    f"{enlace_activacion}\n\n"
                    "Este enlace tiene una vigencia de 3 días.\n\n"
                    "EventPhotos"
                )

                mensaje_html = render_to_string(
                    "eventos/invitacion_anfitrion.html",
                    {
                        "usuario": usuario,
                        "eventos": eventos,
                        "enlace_activacion": enlace_activacion,
                    },
                )

                asunto = "Activa tu cuenta de EventPhotos"

                contexto_mensaje = {
                    "usuario": usuario,
                    "eventos": eventos,
                    "enlace_activacion": enlace_activacion,
                }

            correo = EmailMultiAlternatives(
                subject=asunto,
                body=mensaje_texto,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[usuario.email],
            )

            correo.attach_alternative(
                mensaje_html,
                "text/html",
            )

            correo.send()

            messages.success(
                request,
                f"Usuario {usuario.email} creado correctamente.",
            )

            return redirect("dashboard")

    else:

        if evento_preseleccionado:

            form = UsuarioForm(
                initial={
                    "eventos": [
                        evento_preseleccionado.id,
                    ],
                }
            )

        else:
            form = UsuarioForm()

    return render(
        request,
        "eventos/crear_usuario.html",
        {
            "form": form,
        },
    )

@login_required
def dashboard_evento(request, slug):
    evento = obtener_evento_del_usuario(request, slug)

    fotos_actuales = Foto.objects.filter(
        evento=evento,
        eliminada_at__isnull=True,
    )

    total_fotos = fotos_actuales.count()

    almacenamiento_usado = (
        fotos_actuales
        .aggregate(total=Sum("tamaño"))
        .get("total")
        or 0
    )

    porcentaje_fotos = (
        total_fotos / MAX_FOTOS_POR_EVENTO * 100
    )

    porcentaje_almacenamiento = (
        almacenamiento_usado
        / MAX_STORAGE_POR_EVENTO
        * 100
    )

    imagen_portada_url = None

    if evento.imagen_portada_key:
        imagen_portada_url = generar_url_lectura(
            evento.imagen_portada_key
        )

    logo_url = None

    if evento.logo_key:
        logo_url = generar_url_lectura(
            evento.logo_key
        )

    return render(
        request,
        "eventos/dashboard_evento.html",
        {
            "evento": evento,
            "total_fotos": total_fotos,
            "almacenamiento_usado": almacenamiento_usado,
            "porcentaje_fotos": porcentaje_fotos,
            "porcentaje_almacenamiento": porcentaje_almacenamiento,
            "max_fotos": MAX_FOTOS_POR_EVENTO,
            "max_almacenamiento": MAX_STORAGE_POR_EVENTO,
            "imagen_portada_url": imagen_portada_url,
            "logo_url": logo_url,
        },
    )

@login_required
def reabrir_evento(request, slug):
    if request.method != "POST":
        return redirect(
            "dashboard_evento",
            slug=slug,
        )

    evento = obtener_evento_del_usuario(
        request,
        slug,
    )

    if evento.estado != Evento.Estado.CLOSED:
        messages.warning(
            request,
            "Solo se pueden reabrir eventos cerrados.",
        )

        return redirect(
            "dashboard_evento",
            slug=evento.slug,
        )

    evento.estado = Evento.Estado.ACTIVE
    evento.save(update_fields=["estado", "updated_at"])

    messages.success(
        request,
        "El evento fue reabierto correctamente.",
    )

    return redirect(
        "dashboard_evento",
        slug=evento.slug,
    )

@login_required
def cerrar_evento(request, slug):
    if request.method != "POST":
        return redirect(
            "dashboard_evento",
            slug=slug,
        )

    evento = obtener_evento_del_usuario(
        request,
        slug,
    )

    if evento.estado != Evento.Estado.ACTIVE:
        messages.warning(
            request,
            "Solo se pueden cerrar eventos activos.",
        )

        return redirect(
            "dashboard_evento",
            slug=evento.slug,
        )

    evento.estado = Evento.Estado.CLOSED
    evento.save(update_fields=["estado", "updated_at"])

    messages.success(
        request,
        "El evento fue cerrado correctamente.",
    )

    return redirect(
        "dashboard_evento",
        slug=evento.slug,
    )

@login_required
def descargar_fotos_evento(request, slug):
    evento = obtener_evento_del_usuario(request, slug)

    fotos = (
        Foto.objects
        .filter(
            evento=evento,
            eliminada_at__isnull=True,
        )
        .select_related("mesa")
        .order_by("mesa__numero", "creada_en")
    )

    if not fotos.exists():
        return HttpResponse(
            "Este evento no tiene fotos para descargar.",
            status=404,
        )

    archivo_zip = io.BytesIO()

    with zipfile.ZipFile(
        archivo_zip,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as zip_file:

        nombres_usados = set()

        for foto in fotos:
            objeto = obtener_objeto(foto.object_key)

            contenido = objeto["Body"].read()

            nombre_mesa = (
                foto.mesa.nombre.strip()
                if foto.mesa and foto.mesa.nombre
                else f"Mesa {foto.mesa.numero}"
            )

            nombre_archivo = foto.nombre_original

            ruta_zip = f"{nombre_mesa}/{nombre_archivo}"

            if ruta_zip in nombres_usados:
                base, extension = nombre_archivo.rsplit(
                    ".",
                    1,
                ) if "." in nombre_archivo else (
                    nombre_archivo,
                    "",
                )

                contador = 2

                while True:
                    nuevo_nombre = (
                        f"{base}_{contador}"
                        f".{extension}"
                        if extension
                        else f"{base}_{contador}"
                    )

                    nueva_ruta = (
                        f"{nombre_mesa}/{nuevo_nombre}"
                    )

                    if nueva_ruta not in nombres_usados:
                        ruta_zip = nueva_ruta
                        break

                    contador += 1

            nombres_usados.add(ruta_zip)

            zip_file.writestr(
                ruta_zip,
                contenido,
            )

    archivo_zip.seek(0)

    nombre_zip = (
        f"{evento.nombre} - Fotos.zip"
    )

    respuesta = HttpResponse(
        archivo_zip.getvalue(),
        content_type="application/zip",
    )

    respuesta["Content-Disposition"] = (
        f'attachment; filename="{nombre_zip}"'
    )

    return respuesta

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

    evento = obtener_evento_del_usuario(
        request,
        slug,
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

def qr_mesa(request, slug, mesa_id):

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

    imagen.save(
        buffer,
        format="PNG",
    )

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
        anfitriones=request.user,
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
        anfitriones=request.user,
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