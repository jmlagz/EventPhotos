from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import AceptacionLegal, Evento, InvitacionUsuario


@override_settings(
    SELF_SERVICE_ENABLED=True,
    LEGAL_TERMS_VERSION="terms-test-v1",
    LEGAL_PRIVACY_VERSION="privacy-test-v1",
    LEGAL_TERMS_URL="https://legal.test/terminos",
    LEGAL_PRIVACY_URL="https://legal.test/privacidad",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class RegistroPublicoTests(TestCase):
    def signup_data(self, **overrides):
        data = {
            "first_name": "  Ana  ",
            "last_name": "López",
            "email": "  ANA@Example.COM  ",
            "password": "Una-Clave-Segura-2026",
            "password_confirmacion": "Una-Clave-Segura-2026",
            "acepta_terminos": "on",
            "acepta_privacidad": "on",
        }
        data.update(overrides)
        return data

    def test_valid_signup_creates_inactive_non_privileged_user_and_consent(self):
        response = self.client.post(
            reverse("registro_publico"),
            self.signup_data(),
        )

        self.assertRedirects(response, reverse("registro_publico_pendiente"))
        usuario = User.objects.get(username="ana@example.com")
        self.assertEqual(usuario.email, "ana@example.com")
        self.assertEqual(usuario.first_name, "Ana")
        self.assertEqual(usuario.last_name, "López")
        self.assertFalse(usuario.is_active)
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)
        self.assertTrue(usuario.check_password("Una-Clave-Segura-2026"))
        self.assertNotEqual(usuario.password, "Una-Clave-Segura-2026")

        aceptacion = AceptacionLegal.objects.get(usuario=usuario)
        self.assertEqual(aceptacion.version_terminos, "terms-test-v1")
        self.assertEqual(aceptacion.version_privacidad, "privacy-test-v1")
        self.assertIsNotNone(aceptacion.aceptado_en)
        self.assertFalse(Evento.objects.filter(anfitriones=usuario).exists())

    def test_both_legal_consents_are_required(self):
        for missing_field in ("acepta_terminos", "acepta_privacidad"):
            with self.subTest(missing_field=missing_field):
                data = self.signup_data()
                data.pop(missing_field)
                response = self.client.post(reverse("registro_publico"), data)

                self.assertEqual(response.status_code, 200)
                self.assertFormError(
                    response.context["form"],
                    missing_field,
                    "Este campo es obligatorio.",
                )
                self.assertEqual(User.objects.count(), 0)
                self.assertEqual(AceptacionLegal.objects.count(), 0)

    def test_password_uses_django_validators(self):
        response = self.client.post(
            reverse("registro_publico"),
            self.signup_data(
                password="password",
                password_confirmacion="password",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Esta contraseña es demasiado común.")
        self.assertEqual(User.objects.count(), 0)

    def test_duplicate_email_is_neutral_and_does_not_reveal_account_state(self):
        User.objects.create_user(
            username="ana@example.com",
            email="ana@example.com",
            password="existing-password",
            is_active=False,
        )

        response = self.client.post(
            reverse("registro_publico"),
            self.signup_data(),
        )

        self.assertRedirects(response, reverse("registro_publico_pendiente"))
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(AceptacionLegal.objects.count(), 0)

    def test_existing_and_new_email_receive_same_public_response(self):
        new_response = self.client.post(
            reverse("registro_publico"),
            self.signup_data(email="new@example.com"),
        )
        User.objects.create_user(
            username="existing@example.com",
            email="existing@example.com",
            password="existing-password",
            is_active=True,
        )

        existing_response = self.client.post(
            reverse("registro_publico"),
            self.signup_data(email="existing@example.com"),
        )

        self.assertEqual(existing_response.status_code, new_response.status_code)
        self.assertEqual(existing_response.url, new_response.url)
        self.assertEqual(User.objects.filter(email="existing@example.com").count(), 1)

    @patch(
        "eventos.services.account_registration.User.save",
        side_effect=IntegrityError("duplicate username"),
    )
    def test_concurrent_duplicate_is_handled_with_neutral_response(
        self,
        _save_user,
    ):
        response = self.client.post(
            reverse("registro_publico"),
            self.signup_data(),
        )

        self.assertRedirects(response, reverse("registro_publico_pendiente"))
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(AceptacionLegal.objects.count(), 0)

    @patch(
        "eventos.services.account_registration.AceptacionLegal.objects.create",
        side_effect=IntegrityError("consent failed"),
    )
    def test_consent_failure_rolls_back_user(self, _create_consent):
        with self.assertRaises(IntegrityError):
            self.client.post(
                reverse("registro_publico"),
                self.signup_data(),
            )

        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(AceptacionLegal.objects.count(), 0)

    def test_same_user_can_accumulate_future_legal_acceptances(self):
        self.client.post(reverse("registro_publico"), self.signup_data())
        usuario = User.objects.get(username="ana@example.com")

        AceptacionLegal.objects.create(
            usuario=usuario,
            version_terminos="terms-test-v2",
            version_privacidad="privacy-test-v2",
        )

        self.assertEqual(usuario.aceptaciones_legales.count(), 2)
        self.assertSetEqual(
            set(
                usuario.aceptaciones_legales.values_list(
                    "version_terminos",
                    flat=True,
                )
            ),
            {"terms-test-v1", "terms-test-v2"},
        )

    def test_get_renders_accessible_form_without_mutating(self):
        response = self.client.get(reverse("registro_publico"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crea tu cuenta")
        self.assertContains(response, "https://legal.test/terminos")
        self.assertContains(response, "https://legal.test/privacidad")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertNotContains(response, 'name="username"')
        self.assertEqual(User.objects.count(), 0)

    def test_csrf_is_required_for_signup_post(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(
            reverse("registro_publico"),
            self.signup_data(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(User.objects.count(), 0)


class RegistroPublicoFeatureFlagTests(TestCase):
    @override_settings(SELF_SERVICE_ENABLED=False)
    def test_disabled_flag_hides_get_and_blocks_post(self):
        url = reverse("registro_publico")

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {}).status_code, 404)
        self.assertEqual(User.objects.count(), 0)

    @override_settings(
        SELF_SERVICE_ENABLED=True,
        LEGAL_TERMS_VERSION="terms-test-v1",
        LEGAL_PRIVACY_VERSION="privacy-test-v1",
        LEGAL_TERMS_URL="",
        LEGAL_PRIVACY_URL="",
    )
    def test_missing_legal_urls_keeps_signup_unavailable(self):
        self.assertEqual(
            self.client.get(reverse("registro_publico")).status_code,
            404,
        )

    @override_settings(
        SELF_SERVICE_ENABLED=True,
        LEGAL_TERMS_VERSION="terms-test-v1",
        LEGAL_PRIVACY_VERSION="privacy-test-v1",
        LEGAL_TERMS_URL="javascript:alert(1)",
        LEGAL_PRIVACY_URL="https://legal.test/privacidad",
    )
    def test_unsafe_legal_url_scheme_keeps_signup_unavailable(self):
        self.assertEqual(
            self.client.get(reverse("registro_publico")).status_code,
            404,
        )


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class RegistroPublicoCompatibilityTests(TestCase):
    def test_existing_login_still_authenticates_active_user(self):
        usuario = User.objects.create_user(
            username="host@example.com",
            email="host@example.com",
            password="test-password",
        )

        response = self.client.post(
            reverse("login_anfitrion"),
            {"username": usuario.username, "password": "test-password"},
        )

        self.assertRedirects(response, reverse("dashboard_anfitrion"))

    def test_existing_invitation_model_is_unchanged(self):
        usuario = User.objects.create_user(
            username="invited@example.com",
            is_active=False,
        )
        invitation = InvitacionUsuario.objects.create(
            usuario=usuario,
            expira_en="2026-09-30T00:00:00Z",
        )

        self.assertTrue(invitation.token)
