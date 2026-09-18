import os
import runpy
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse


class DebugFailClosedTests(SimpleTestCase):
    settings_path = Path(settings.BASE_DIR) / "config" / "settings.py"

    def read_debug_setting(self, value):
        environment = {"DATABASE_URL": "sqlite:///:memory:"}
        if value is not None:
            environment["DEBUG"] = value
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("dotenv.load_dotenv"),
        ):
            loaded_settings = runpy.run_path(str(self.settings_path))
        return loaded_settings["DEBUG"]

    def test_debug_is_false_when_environment_variable_is_absent(self):
        self.assertIs(self.read_debug_setting(None), False)

    def test_debug_is_true_when_explicitly_enabled(self):
        self.assertIs(self.read_debug_setting("True"), True)

    def test_debug_is_false_when_explicitly_disabled(self):
        self.assertIs(self.read_debug_setting("False"), False)


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
class ProductionErrorResponseTests(SimpleTestCase):
    def test_not_found_response_does_not_expose_diagnostics(self):
        response = self.client.get("/__p13h1_missing_route__/")
        body = response.content.decode(errors="replace")

        self.assertEqual(response.status_code, 404)
        for sensitive_text in (
            "Traceback",
            "URLconf",
            "ROOT_URLCONF",
            "DJANGO_SETTINGS_MODULE",
            str(settings.BASE_DIR),
            "D:\\EventPhotos",
        ):
            with self.subTest(sensitive_text=sensitive_text):
                self.assertNotIn(sensitive_text, body)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class SecurityRegressionTests(TestCase):
    password = "Clave-Segura-2026"

    def setUp(self):
        self.user = User.objects.create_user(
            username="p13h1@example.com",
            email="p13h1@example.com",
            password=self.password,
        )

    def test_authenticated_get_logout_is_405_and_preserves_session(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("logout_anfitrion"))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "POST")
        self.assertEqual(
            int(self.client.session["_auth_user_id"]),
            self.user.pk,
        )

    def test_logout_post_with_valid_csrf_still_works(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.get(reverse("dashboard_anfitrion"))
        csrf_token = client.cookies[settings.CSRF_COOKIE_NAME].value

        response = client.post(
            reverse("logout_anfitrion"),
            {"csrfmiddlewaretoken": csrf_token},
        )

        self.assertRedirects(response, reverse("login_anfitrion"))
        self.assertNotIn("_auth_user_id", client.session)

    def test_sensitive_post_without_csrf_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = client.post(reverse("logout_anfitrion"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            int(client.session["_auth_user_id"]),
            self.user.pk,
        )

    def test_login_keeps_csrf_token_and_cookie_names(self):
        response = self.client.get(reverse("login_anfitrion"))

        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertEqual(settings.SESSION_COOKIE_NAME, "sessionid")
        self.assertEqual(settings.CSRF_COOKIE_NAME, "csrftoken")
        self.assertIn("csrftoken", response.cookies)

        self.client.force_login(self.user)
        self.assertIn("sessionid", self.client.cookies)

    def test_admin_remains_protected_and_operational(self):
        admin_url = reverse("admin:index")

        anonymous_response = self.client.get(admin_url)

        self.assertRedirects(
            anonymous_response,
            f"{reverse('admin:login')}?next={admin_url}",
        )

        superuser = User.objects.create_superuser(
            username="admin-p13h1@example.com",
            email="admin-p13h1@example.com",
            password=self.password,
        )
        self.client.force_login(superuser)

        self.assertEqual(self.client.get(admin_url).status_code, 200)
