from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlsafe_base64_encode

from .models import AceptacionLegal, Evento
from .services.account_activation import account_activation_token_generator


P13G_SETTINGS = {
    "SELF_SERVICE_ENABLED": True,
    "SELF_SERVICE_REGISTRATION_COOLDOWN_SECONDS": 60,
    "SELF_SERVICE_ACTIVATION_RESEND_COOLDOWN_SECONDS": 60,
    "LEGAL_TERMS_VERSION": "terms-test-v1",
    "LEGAL_PRIVACY_VERSION": "privacy-test-v1",
    "LEGAL_TERMS_URL": "https://legal.test/terminos",
    "LEGAL_PRIVACY_URL": "https://legal.test/privacidad",
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
}


@override_settings(**P13G_SETTINGS)
class LogoutHardeningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="host@example.com",
            password="safe-password",
        )
        self.url = reverse("logout_anfitrion")

    def test_get_does_not_logout_authenticated_user(self):
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_post_logs_out_normal_user(self):
        self.client.force_login(self.user)

        response = self.client.post(self.url)

        self.assertRedirects(response, reverse("login_anfitrion"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_post_logs_out_superuser(self):
        admin = User.objects.create_superuser(
            username="admin@example.com",
            password="safe-password",
        )
        self.client.force_login(admin)

        response = self.client.post(self.url)

        self.assertRedirects(response, reverse("login_anfitrion"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_post_requires_csrf_and_keeps_session_on_rejection(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = client.post(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(int(client.session["_auth_user_id"]), self.user.pk)

    def test_rendered_logout_controls_are_post_forms_with_csrf(self):
        self.client.force_login(self.user)

        dashboard = self.client.get(reverse("dashboard_anfitrion"))
        account = self.client.get(reverse("mi_cuenta"))

        for response in (dashboard, account):
            with self.subTest(template=response.templates[0].name):
                self.assertContains(response, f'action="{self.url}"')
                self.assertContains(response, 'method="post"')
                self.assertContains(response, "csrfmiddlewaretoken")
                self.assertNotContains(response, f'href="{self.url}"')


@override_settings(**P13G_SETTINGS)
class SignupHardeningTests(TestCase):
    password = "Una-Clave-Segura-2026"

    def signup_data(self, email):
        return {
            "first_name": "Ana",
            "last_name": "López",
            "email": email,
            "password": self.password,
            "password_confirmacion": self.password,
            "acepta_terminos": "on",
            "acepta_privacidad": "on",
        }

    def create_pending_user(self, email="pending@example.com"):
        user = User.objects.create_user(
            username=email,
            email=email,
            password=self.password,
            is_active=False,
        )
        AceptacionLegal.objects.create(
            usuario=user,
            version_terminos="terms-test-v1",
            version_privacidad="privacy-test-v1",
        )
        return user

    def activation_url(self, user):
        return reverse(
            "activar_cuenta_publica",
            kwargs={
                "uidb64": urlsafe_base64_encode(str(user.pk).encode()),
                "token": account_activation_token_generator.make_token(user),
            },
        )

    def test_registration_cooldown_is_per_session_and_neutral(self):
        url = reverse("registro_publico")

        first = self.client.post(url, self.signup_data("first@example.com"))
        second = self.client.post(url, self.signup_data("second@example.com"))
        other_session = Client().post(
            url,
            self.signup_data("third@example.com"),
        )

        expected = reverse("registro_publico_pendiente")
        self.assertEqual((first.status_code, first.url), (302, expected))
        self.assertEqual((second.status_code, second.url), (302, expected))
        self.assertEqual((other_session.status_code, other_session.url), (302, expected))
        self.assertTrue(User.objects.filter(email="first@example.com").exists())
        self.assertFalse(User.objects.filter(email="second@example.com").exists())
        self.assertTrue(User.objects.filter(email="third@example.com").exists())
        self.assertEqual(len(mail.outbox), 2)

    @patch("eventos.views.enviar_email_activacion", side_effect=RuntimeError("smtp"))
    def test_activation_email_failure_is_neutral_and_does_not_rollback(self, _send):
        with self.assertLogs("eventos.operations", level="INFO") as captured:
            response = self.client.post(
                reverse("registro_publico"),
                self.signup_data("failure@example.com"),
            )

        self.assertRedirects(response, reverse("registro_publico_pendiente"))
        user = User.objects.get(email="failure@example.com")
        self.assertFalse(user.is_active)
        logs = "\n".join(captured.output)
        self.assertIn("signup_activation_email_failed", logs)
        self.assertNotIn(self.password, logs)
        self.assertNotIn("failure@example.com", logs)

    def test_registration_and_activation_emit_safe_audit_events(self):
        with patch("eventos.views.log_operation") as log:
            self.client.post(
                reverse("registro_publico"),
                self.signup_data("audit@example.com"),
            )
            user = User.objects.get(email="audit@example.com")
            response = self.client.get(self.activation_url(user))

        self.assertRedirects(response, reverse("dashboard_anfitrion"))
        event_names = [call.args[0] for call in log.call_args_list]
        self.assertIn("signup_submitted", event_names)
        self.assertIn("signup_user_created", event_names)
        self.assertIn("signup_activation_email_sent", event_names)
        self.assertIn("signup_activated", event_names)
        self.assertNotIn(self.password, repr(log.call_args_list))
        self.assertNotIn("audit@example.com", repr(log.call_args_list))

    def test_invalid_or_replayed_activation_is_neutral_and_logged(self):
        user = self.create_pending_user()
        url = self.activation_url(user)
        self.client.get(url)

        with patch("eventos.views.log_operation") as log:
            replay = Client().get(url)

        self.assertEqual(replay.status_code, 200)
        self.assertContains(replay, "Este enlace ya no es válido o ha expirado")
        log.assert_called_once_with(
            "signup_activation_replayed_or_invalid",
            user_id=user.pk,
        )

    def test_resend_is_neutral_throttled_and_logged(self):
        self.create_pending_user()
        url = reverse("reenviar_activacion")

        with patch("eventos.views.log_operation") as log:
            first = self.client.post(url, {"email": "pending@example.com"})
            second = self.client.post(url, {"email": "missing@example.com"})

        expected = reverse("reenviar_activacion_enviado")
        self.assertEqual((first.status_code, first.url), (302, expected))
        self.assertEqual((second.status_code, second.url), (302, expected))
        self.assertEqual(len(mail.outbox), 1)
        calls = [
            call
            for call in log.call_args_list
            if call.args[0] == "signup_activation_resend_requested"
        ]
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].kwargs["cooldown_allowed"])
        self.assertFalse(calls[1].kwargs["cooldown_allowed"])

    @override_settings(SELF_SERVICE_ENABLED=False)
    def test_kill_switch_blocks_entry_points_but_not_existing_users_or_links(self):
        active = User.objects.create_user(
            username="active@example.com",
            password=self.password,
        )
        pending = self.create_pending_user()

        self.assertEqual(self.client.get(reverse("registro_publico")).status_code, 404)
        self.assertEqual(self.client.get(reverse("reenviar_activacion")).status_code, 404)
        login_response = self.client.post(
            reverse("login_anfitrion"),
            {"username": active.username, "password": self.password},
        )
        self.assertRedirects(login_response, reverse("dashboard_anfitrion"))
        self.client.logout()

        activation = self.client.get(self.activation_url(pending))

        self.assertRedirects(activation, reverse("dashboard_anfitrion"))
        pending.refresh_from_db()
        self.assertTrue(pending.is_active)


@override_settings(**P13G_SETTINGS)
class AccountAuditAndDeactivationTests(TestCase):
    password = "Una-Clave-Segura-2026"

    def setUp(self):
        self.user = User.objects.create_user(
            username="account@example.com",
            email="account@example.com",
            password=self.password,
            first_name="Ana",
            last_name="López",
        )

    def test_voluntarily_deactivated_login_uses_generic_error(self):
        AceptacionLegal.objects.create(
            usuario=self.user,
            version_terminos="terms-test-v1",
            version_privacidad="privacy-test-v1",
        )
        self.user.last_login = timezone.now()
        self.user.is_active = False
        self.user.save(update_fields=["last_login", "is_active"])

        response = self.client.post(
            reverse("login_anfitrion"),
            {"username": self.user.username, "password": self.password},
        )

        self.assertContains(response, "Usuario/Correo o contraseña incorrectos.")
        self.assertNotContains(response, "Tu cuenta todavía necesita verificación")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_account_mutations_emit_audit_events_without_personal_data(self):
        self.client.force_login(self.user)

        with patch("eventos.views.log_operation") as log:
            self.client.post(
                reverse("mi_cuenta"),
                {"first_name": "María", "last_name": "García"},
            )
            self.client.post(
                reverse("cambiar_password"),
                {
                    "old_password": self.password,
                    "new_password1": "Nueva-Clave-Segura-2026",
                    "new_password2": "Nueva-Clave-Segura-2026",
                },
            )
            self.client.post(reverse("desactivar_cuenta"), {"confirmar": "si"})

        event_names = [call.args[0] for call in log.call_args_list]
        self.assertEqual(
            event_names,
            [
                "account_profile_updated",
                "account_password_changed",
                "account_deactivated",
            ],
        )
        serialized_calls = repr(log.call_args_list)
        self.assertNotIn(self.password, serialized_calls)
        self.assertNotIn("Nueva-Clave-Segura-2026", serialized_calls)
        self.assertNotIn("account@example.com", serialized_calls)

    def test_self_service_event_creation_emits_audit_event(self):
        self.client.force_login(self.user)

        with patch("eventos.views.log_operation") as log:
            response = self.client.post(
                reverse("crear_evento_autoservicio"),
                {
                    "nombre": "Boda auditada",
                    "tipo": Evento.Tipo.BODA,
                    "fecha": date(2026, 12, 12),
                },
            )

        event = Evento.objects.get()
        self.assertRedirects(
            response,
            reverse("dashboard_evento", kwargs={"slug": event.slug}),
        )
        log.assert_called_once_with(
            "self_service_event_created",
            user_id=self.user.pk,
            event_id=event.pk,
        )
