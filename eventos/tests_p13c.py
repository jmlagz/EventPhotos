import re
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.http import urlsafe_base64_encode

from .models import AceptacionLegal, Evento
from .services.account_activation import account_activation_token_generator


SELF_SERVICE_TEST_SETTINGS = {
    "SELF_SERVICE_ENABLED": True,
    "LEGAL_TERMS_VERSION": "terms-test-v1",
    "LEGAL_PRIVACY_VERSION": "privacy-test-v1",
    "LEGAL_TERMS_URL": "https://legal.test/terminos",
    "LEGAL_PRIVACY_URL": "https://legal.test/privacidad",
    "SELF_SERVICE_ACTIVATION_TIMEOUT_SECONDS": 86400,
    "SELF_SERVICE_ACTIVATION_RESEND_COOLDOWN_SECONDS": 60,
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
}


@override_settings(**SELF_SERVICE_TEST_SETTINGS)
class AccountActivationTests(TestCase):
    def signup_data(self, email="ana@example.com"):
        return {
            "first_name": "Ana",
            "last_name": "López",
            "email": email,
            "password": "Una-Clave-Segura-2026",
            "password_confirmacion": "Una-Clave-Segura-2026",
            "acepta_terminos": "on",
            "acepta_privacidad": "on",
        }

    def create_pending_user(self, email="ana@example.com"):
        usuario = User.objects.create_user(
            username=email,
            email=email,
            password="Una-Clave-Segura-2026",
            is_active=False,
        )
        AceptacionLegal.objects.create(
            usuario=usuario,
            version_terminos="terms-test-v1",
            version_privacidad="privacy-test-v1",
        )
        return usuario

    def activation_path(self, usuario):
        uidb64 = urlsafe_base64_encode(str(usuario.pk).encode())
        token = account_activation_token_generator.make_token(usuario)
        return reverse(
            "activar_cuenta_publica",
            kwargs={"uidb64": uidb64, "token": token},
        )

    def activation_path_from_email(self):
        match = re.search(r"https?://[^\s]+/activar/[^\s]+", mail.outbox[0].body)
        self.assertIsNotNone(match)
        return urlsplit(match.group(0)).path

    def test_valid_signup_sends_activation_email_without_admin_notice(self):
        response = self.client.post(
            reverse("registro_publico"),
            self.signup_data(),
        )

        self.assertRedirects(response, reverse("registro_publico_pendiente"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ana@example.com"])
        self.assertEqual(mail.outbox[0].subject, "Activa tu cuenta de EventPhotos")
        self.assertIn("vence en 24 horas", mail.outbox[0].body)
        self.assertNotIn("Una-Clave-Segura-2026", mail.outbox[0].body)
        self.assertContains(
            self.client.get(reverse("registro_publico_pendiente")),
            "Revisa tu correo",
        )
        usuario = User.objects.get(username="ana@example.com")
        self.assertFalse(usuario.is_active)
        self.assertEqual(Evento.objects.count(), 0)

    def test_valid_link_activates_logs_in_and_redirects_to_my_events(self):
        usuario = self.create_pending_user()

        response = self.client.get(self.activation_path(usuario))

        self.assertRedirects(response, reverse("dashboard_anfitrion"))
        usuario.refresh_from_db()
        self.assertTrue(usuario.is_active)
        self.assertEqual(int(self.client.session["_auth_user_id"]), usuario.pk)
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)
        self.assertEqual(Evento.objects.count(), 0)

    def test_invalid_uid_and_token_are_neutral(self):
        usuario = self.create_pending_user()
        cases = (
            reverse(
                "activar_cuenta_publica",
                kwargs={"uidb64": "invalid", "token": "invalid-token"},
            ),
            reverse(
                "activar_cuenta_publica",
                kwargs={
                    "uidb64": urlsafe_base64_encode(str(usuario.pk).encode()),
                    "token": "invalid-token",
                },
            ),
        )

        for path in cases:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    "Este enlace ya no es válido o ha expirado",
                )

        usuario.refresh_from_db()
        self.assertFalse(usuario.is_active)

    def test_token_is_bound_to_its_user(self):
        first = self.create_pending_user("first@example.com")
        second = self.create_pending_user("second@example.com")
        first_token = account_activation_token_generator.make_token(first)
        second_uid = urlsafe_base64_encode(str(second.pk).encode())

        response = self.client.get(
            reverse(
                "activar_cuenta_publica",
                kwargs={"uidb64": second_uid, "token": first_token},
            )
        )

        self.assertEqual(response.status_code, 200)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertFalse(second.is_active)

    @override_settings(SELF_SERVICE_ACTIVATION_TIMEOUT_SECONDS=1)
    def test_expired_link_does_not_activate(self):
        usuario = self.create_pending_user()
        old_now = datetime.now() - timedelta(seconds=2)
        with patch.object(
            account_activation_token_generator,
            "_now",
            return_value=old_now,
        ):
            path = self.activation_path(usuario)

        response = self.client.get(path)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este enlace ya no es válido o ha expirado")
        usuario.refresh_from_db()
        self.assertFalse(usuario.is_active)

    @override_settings(
        SIGNUP_ADMIN_NOTIFICATIONS=True,
        SIGNUP_NOTIFICATION_EMAIL="admin-notify@example.com",
    )
    def test_admin_notification_is_sent_once_only_after_real_activation(self):
        self.client.post(reverse("registro_publico"), self.signup_data())
        usuario = User.objects.get(username="ana@example.com")
        path = self.activation_path_from_email()
        mail.outbox.clear()

        response = self.client.get(path)

        self.assertRedirects(response, reverse("dashboard_anfitrion"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["admin-notify@example.com"])
        self.assertIn("Nuevo usuario verificado", mail.outbox[0].subject)
        self.assertIn("ana@example.com", mail.outbox[0].body)
        self.assertNotIn("Una-Clave-Segura-2026", mail.outbox[0].body)

        replay = Client().get(path)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        usuario.refresh_from_db()
        self.assertTrue(usuario.is_active)

    @override_settings(
        SIGNUP_ADMIN_NOTIFICATIONS=True,
        SIGNUP_NOTIFICATION_EMAIL="admin-notify@example.com",
    )
    @patch(
        "eventos.views.enviar_notificacion_admin_activacion",
        side_effect=RuntimeError("smtp unavailable"),
    )
    def test_admin_email_failure_does_not_rollback_activation(
        self,
        _notify_admin,
    ):
        usuario = self.create_pending_user()

        with self.assertLogs("eventos.operations", level="INFO") as logs:
            response = self.client.get(self.activation_path(usuario))

        self.assertRedirects(response, reverse("dashboard_anfitrion"))
        usuario.refresh_from_db()
        self.assertTrue(usuario.is_active)
        self.assertEqual(int(self.client.session["_auth_user_id"]), usuario.pk)
        self.assertTrue(
            any("signup_admin_notification_failed" in line for line in logs.output)
        )

    @override_settings(SELF_SERVICE_ENABLED=False)
    def test_valid_issued_link_can_finish_when_feature_flag_is_disabled(self):
        usuario = self.create_pending_user()

        response = self.client.get(self.activation_path(usuario))

        self.assertRedirects(response, reverse("dashboard_anfitrion"))
        usuario.refresh_from_db()
        self.assertTrue(usuario.is_active)

    def test_activation_email_failure_keeps_account_pending_and_allows_resend(self):
        with patch(
            "eventos.views.enviar_email_activacion",
            side_effect=RuntimeError("smtp unavailable"),
        ):
            with self.assertLogs("eventos.operations", level="INFO") as logs:
                response = self.client.post(
                    reverse("registro_publico"),
                    self.signup_data(),
                )

        self.assertRedirects(response, reverse("registro_publico_pendiente"))
        usuario = User.objects.get(username="ana@example.com")
        self.assertFalse(usuario.is_active)
        self.assertEqual(usuario.aceptaciones_legales.count(), 1)
        self.assertTrue(
            any("signup_activation_email_failed" in line for line in logs.output)
        )

        resend = self.client.post(
            reverse("reenviar_activacion"),
            {"email": "ana@example.com"},
        )
        self.assertRedirects(resend, reverse("reenviar_activacion_enviado"))
        self.assertEqual(len(mail.outbox), 1)


@override_settings(**SELF_SERVICE_TEST_SETTINGS)
class ActivationResendTests(TestCase):
    def create_user(self, email, *, is_active):
        usuario = User.objects.create_user(
            username=email,
            email=email,
            password="Una-Clave-Segura-2026",
            is_active=is_active,
        )
        AceptacionLegal.objects.create(
            usuario=usuario,
            version_terminos="terms-test-v1",
            version_privacidad="privacy-test-v1",
        )
        return usuario

    def test_pending_account_receives_email_and_session_throttle_stops_loop(self):
        self.create_user("pending@example.com", is_active=False)
        url = reverse("reenviar_activacion")

        first = self.client.post(url, {"email": " PENDING@EXAMPLE.COM "})
        second = self.client.post(url, {"email": "pending@example.com"})

        self.assertRedirects(first, reverse("reenviar_activacion_enviado"))
        self.assertRedirects(second, reverse("reenviar_activacion_enviado"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["pending@example.com"])

    def test_active_missing_and_pending_accounts_receive_same_public_response(self):
        self.create_user("active@example.com", is_active=True)
        self.create_user("pending@example.com", is_active=False)
        url = reverse("reenviar_activacion")
        responses = []

        for email in (
            "active@example.com",
            "missing@example.com",
            "pending@example.com",
        ):
            client = Client()
            responses.append(client.post(url, {"email": email}))

        self.assertEqual(
            {(response.status_code, response.url) for response in responses},
            {(302, reverse("reenviar_activacion_enviado"))},
        )
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(SELF_SERVICE_ENABLED=False)
    def test_feature_flag_blocks_resend_get_and_post(self):
        url = reverse("reenviar_activacion")

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"email": "a@example.com"}).status_code, 404)

    def test_csrf_is_required_for_resend(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            reverse("reenviar_activacion"),
            {"email": "pending@example.com"},
        )

        self.assertEqual(response.status_code, 403)


@override_settings(**SELF_SERVICE_TEST_SETTINGS)
class InactiveLoginTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username="pending@example.com",
            email="pending@example.com",
            password="Una-Clave-Segura-2026",
            is_active=False,
        )
        AceptacionLegal.objects.create(
            usuario=self.usuario,
            version_terminos="terms-test-v1",
            version_privacidad="privacy-test-v1",
        )

    def test_correct_password_explains_pending_verification(self):
        response = self.client.post(
            reverse("login_anfitrion"),
            {
                "username": "pending@example.com",
                "password": "Una-Clave-Segura-2026",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu cuenta todavía necesita verificación")
        self.assertContains(response, reverse("reenviar_activacion"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_password_keeps_generic_login_error(self):
        response = self.client.post(
            reverse("login_anfitrion"),
            {
                "username": "pending@example.com",
                "password": "wrong-password",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Usuario/Correo o contraseña incorrectos.",
        )
        self.assertNotContains(response, "Tu cuenta todavía necesita verificación")

    @override_settings(SELF_SERVICE_ENABLED=False)
    def test_disabled_feature_flag_does_not_offer_blocked_resend(self):
        response = self.client.post(
            reverse("login_anfitrion"),
            {
                "username": "pending@example.com",
                "password": "Una-Clave-Segura-2026",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tu cuenta todavía necesita verificación")
        self.assertNotContains(response, reverse("reenviar_activacion"))
