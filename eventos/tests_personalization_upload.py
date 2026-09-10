import re
from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Evento


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class PersonalizationUploadContractTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            username="personalization-host@example.com",
            password="test-password",
        )
        self.other_host = User.objects.create_user(
            username="personalization-other@example.com",
            password="test-password",
        )
        self.event = Evento.objects.create(
            nombre="Evento personalización",
            fecha=date(2026, 10, 17),
        )
        self.event.anfitriones.add(self.host)

    def presign_url(self):
        return reverse(
            "solicitar_url_personalizacion",
            args=[self.event.slug],
        )

    def test_cover_and_logo_puts_send_all_signed_headers(self):
        self.client.force_login(self.host)
        response = self.client.get(
            reverse("dashboard_evento", args=[self.event.slug])
        )
        self.assertEqual(response.status_code, 200)
        rendered = response.content.decode()
        signed_headers = re.findall(
            r'"Content-Type":\s*archivo\.type,\s*'
            r'"If-None-Match":\s*"\*"',
            rendered,
        )
        self.assertEqual(len(signed_headers), 2)

    @patch(
        "eventos.views.generar_url_subida",
        return_value="https://upload.test/presigned",
    )
    def test_cover_and_logo_presign_keep_independent_keys(self, generate_url):
        self.client.force_login(self.host)

        results = {}
        for upload_type in ("portada", "logo"):
            with self.subTest(upload_type=upload_type):
                response = self.client.post(
                    self.presign_url(),
                    {
                        "tipo": upload_type,
                        "nombre": f"{upload_type}.png",
                        "content_type": "image/png",
                        "tamaño": 1024,
                    },
                )
                self.assertEqual(response.status_code, 200)
                results[upload_type] = response.json()["object_key"]

        self.assertTrue(
            results["portada"].startswith(
                f"eventos/{self.event.slug}/personalizacion/portada/"
            )
        )
        self.assertTrue(
            results["logo"].startswith(
                f"eventos/{self.event.slug}/personalizacion/logo/"
            )
        )
        self.assertNotEqual(results["portada"], results["logo"])
        for call in generate_url.call_args_list:
            self.assertEqual(call.kwargs["content_type"], "image/png")

    @patch("eventos.views.generar_url_subida")
    def test_unassigned_host_cannot_request_personalization_presign(
        self,
        generate_url,
    ):
        self.client.force_login(self.other_host)
        response = self.client.post(
            self.presign_url(),
            {
                "tipo": "portada",
                "nombre": "portada.png",
                "content_type": "image/png",
                "tamaño": 1024,
            },
        )
        self.assertEqual(response.status_code, 404)
        generate_url.assert_not_called()
