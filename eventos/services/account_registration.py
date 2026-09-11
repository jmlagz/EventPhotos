from django.contrib.auth.models import User
from django.db import IntegrityError, transaction

from eventos.models import AceptacionLegal


class EmailYaRegistrado(Exception):
    pass


@transaction.atomic
def registrar_usuario_publico(
    *,
    first_name,
    last_name,
    email,
    password,
    version_terminos,
    version_privacidad,
):
    usuario = User(
        username=email,
        email=email,
        first_name=first_name,
        last_name=last_name,
        is_active=False,
        is_staff=False,
        is_superuser=False,
    )
    usuario.set_password(password)
    try:
        usuario.save()
    except IntegrityError as exc:
        raise EmailYaRegistrado from exc

    AceptacionLegal.objects.create(
        usuario=usuario,
        version_terminos=version_terminos,
        version_privacidad=version_privacidad,
    )

    return usuario
