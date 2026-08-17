from django.shortcuts import get_object_or_404, redirect, render

from .models import Evento, Mesa


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