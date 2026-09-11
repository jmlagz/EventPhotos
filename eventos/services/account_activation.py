import math

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int, urlsafe_base64_encode


class AccountActivationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "eventos.services.account_activation.AccountActivationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{super()._make_hash_value(user, timestamp)}{user.is_active}"

    def check_token(self, user, token):
        if not (user and token):
            return False
        try:
            ts_b36, _ = token.split("-")
            timestamp = base36_to_int(ts_b36)
        except (TypeError, ValueError):
            return False

        for secret in (self.secret, *self.secret_fallbacks):
            expected = self._make_token_with_timestamp(
                user,
                timestamp,
                secret,
            )
            if constant_time_compare(expected, token):
                break
        else:
            return False

        age = self._num_seconds(self._now()) - timestamp
        return age <= settings.SELF_SERVICE_ACTIVATION_TIMEOUT_SECONDS


account_activation_token_generator = AccountActivationTokenGenerator()


def construir_enlace_activacion(request, usuario):
    uidb64 = urlsafe_base64_encode(str(usuario.pk).encode())
    token = account_activation_token_generator.make_token(usuario)
    return request.build_absolute_uri(
        reverse(
            "activar_cuenta_publica",
            kwargs={"uidb64": uidb64, "token": token},
        )
    )


def enviar_email_activacion(request, usuario):
    enlace_activacion = construir_enlace_activacion(request, usuario)
    expiration_hours = max(
        1,
        math.ceil(settings.SELF_SERVICE_ACTIVATION_TIMEOUT_SECONDS / 3600),
    )
    context = {
        "usuario": usuario,
        "enlace_activacion": enlace_activacion,
        "expiration_hours": expiration_hours,
    }
    subject = render_to_string(
        "eventos/activacion_cuenta_subject.txt",
        context,
    ).strip()
    body = render_to_string(
        "eventos/activacion_cuenta_email.txt",
        context,
    )
    html = render_to_string(
        "eventos/activacion_cuenta_email.html",
        context,
    )
    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[usuario.email],
    )
    message.attach_alternative(html, "text/html")
    message.send()


def enviar_notificacion_admin_activacion(usuario, activated_at):
    if not (
        settings.SIGNUP_ADMIN_NOTIFICATIONS
        and settings.SIGNUP_NOTIFICATION_EMAIL
    ):
        return False

    context = {
        "usuario": usuario,
        "activated_at": activated_at,
    }
    subject = render_to_string(
        "eventos/notificacion_admin_registro_subject.txt",
        context,
    ).strip()
    body = render_to_string(
        "eventos/notificacion_admin_registro_email.txt",
        context,
    )
    html = render_to_string(
        "eventos/notificacion_admin_registro_email.html",
        context,
    )
    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[settings.SIGNUP_NOTIFICATION_EMAIL],
    )
    message.attach_alternative(html, "text/html")
    message.send()
    return True
