import re
from datetime import date
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .limites import MAX_TAMANO_PERSONALIZACION
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

    def confirmation_url(self):
        return reverse(
            "confirmar_personalizacion",
            args=[self.event.slug],
        )

    def object_key(self, upload_type="portada"):
        return (
            f"eventos/{self.event.slug}/personalizacion/"
            f"{upload_type}/test.png"
        )

    def request_presign(self, upload_type="portada", size=1024):
        return self.client.post(
            self.presign_url(),
            {
                "tipo": upload_type,
                "nombre": f"{upload_type}.png",
                "content_type": "image/png",
                "tamaño": size,
            },
        )

    def confirm(self, upload_type="portada", object_key=None):
        return self.client.post(
            self.confirmation_url(),
            {
                "tipo": upload_type,
                "object_key": object_key or self.object_key(upload_type),
            },
        )

    def test_browser_puts_leave_content_length_to_user_agent(self):
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
        self.assertNotIn('"Content-Length"', rendered)

    @patch(
        "eventos.views.generar_url_subida",
        return_value="https://upload.test/presigned",
    )
    def test_size_within_limit_generates_presigned_url_with_exact_length(
        self,
        generate_url,
    ):
        self.client.force_login(self.host)
        response = self.request_presign(size=MAX_TAMANO_PERSONALIZACION)
        self.assertEqual(response.status_code, 200)
        generate_url.assert_called_once()
        self.assertEqual(
            generate_url.call_args.kwargs["content_length"],
            MAX_TAMANO_PERSONALIZACION,
        )

    @patch("eventos.views.generar_url_subida")
    def test_size_over_limit_is_rejected_before_presign(self, generate_url):
        self.client.force_login(self.host)
        response = self.request_presign(
            size=MAX_TAMANO_PERSONALIZACION + 1,
        )
        self.assertEqual(response.status_code, 400)
        generate_url.assert_not_called()

    @patch(
        "eventos.views.generar_url_subida",
        return_value="https://upload.test/presigned",
    )
    def test_cover_and_logo_presign_keep_independent_keys(self, generate_url):
        self.client.force_login(self.host)
        results = {}
        declared_sizes = {"portada": 1024, "logo": 2048}
        for upload_type, size in declared_sizes.items():
            with self.subTest(upload_type=upload_type):
                response = self.request_presign(upload_type, size)
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
        for upload_type, call in zip(declared_sizes, generate_url.call_args_list):
            self.assertEqual(call.kwargs["content_type"], "image/png")
            self.assertEqual(
                call.kwargs["content_length"],
                declared_sizes[upload_type],
            )

    @patch("eventos.views.generar_url_subida")
    def test_unassigned_host_cannot_request_personalization_presign(
        self,
        generate_url,
    ):
        self.client.force_login(self.other_host)
        response = self.request_presign()
        self.assertEqual(response.status_code, 404)
        generate_url.assert_not_called()

    @patch("eventos.views.get_r2_client")
    def test_valid_head_confirms_personalization(self, get_r2_client):
        self.client.force_login(self.host)
        r2 = get_r2_client.return_value
        r2.head_object.return_value = {
            "ContentLength": 1024,
            "ContentType": "image/png",
        }
        object_key = self.object_key()
        response = self.confirm(object_key=object_key)
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.imagen_portada_key, object_key)
        r2.delete_object.assert_not_called()

    @patch("eventos.views.get_r2_client")
    def test_content_length_equal_to_limit_is_accepted(self, get_r2_client):
        self.client.force_login(self.host)
        r2 = get_r2_client.return_value
        r2.head_object.return_value = {
            "ContentLength": MAX_TAMANO_PERSONALIZACION,
            "ContentType": "image/webp",
        }
        object_key = self.object_key("logo")
        response = self.confirm("logo", object_key)
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.logo_key, object_key)

    @patch("eventos.views.get_r2_client")
    def test_oversized_object_is_rejected_and_deleted(self, get_r2_client):
        self.client.force_login(self.host)
        self.event.imagen_portada_key = "eventos/existing-cover.png"
        self.event.save(update_fields=["imagen_portada_key"])
        r2 = get_r2_client.return_value
        r2.head_object.return_value = {
            "ContentLength": MAX_TAMANO_PERSONALIZACION + 1,
            "ContentType": "image/png",
        }
        object_key = self.object_key()
        response = self.confirm(object_key=object_key)
        self.assertEqual(response.status_code, 400)
        r2.delete_object.assert_called_once_with(
            Bucket=settings.R2_BUCKET_NAME,
            Key=object_key,
        )
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.imagen_portada_key,
            "eventos/existing-cover.png",
        )

    @patch("eventos.views.get_r2_client")
    def test_delete_failure_does_not_hide_oversize_rejection(
        self,
        get_r2_client,
    ):
        self.client.force_login(self.host)
        self.event.logo_key = "eventos/existing-logo.png"
        self.event.save(update_fields=["logo_key"])
        r2 = get_r2_client.return_value
        r2.head_object.return_value = {
            "ContentLength": MAX_TAMANO_PERSONALIZACION + 1,
            "ContentType": "image/png",
        }
        r2.delete_object.side_effect = RuntimeError("storage unavailable")
        response = self.confirm("logo")
        self.assertEqual(response.status_code, 400)
        self.event.refresh_from_db()
        self.assertEqual(self.event.logo_key, "eventos/existing-logo.png")

    @patch("eventos.views.get_r2_client")
    def test_missing_invalid_or_non_positive_content_length_is_rejected(
        self,
        get_r2_client,
    ):
        self.client.force_login(self.host)
        r2 = get_r2_client.return_value
        cases = (
            {},
            {"ContentLength": "1024"},
            {"ContentLength": 0},
            {"ContentLength": -1},
            {"ContentLength": True},
        )
        for head_fields in cases:
            with self.subTest(head_fields=head_fields):
                r2.reset_mock()
                r2.head_object.return_value = {
                    **head_fields,
                    "ContentType": "image/png",
                }
                response = self.confirm()
                self.assertEqual(response.status_code, 400)
                r2.delete_object.assert_called_once_with(
                    Bucket=settings.R2_BUCKET_NAME,
                    Key=self.object_key(),
                )

    @patch("eventos.views.get_r2_client")
    def test_invalid_content_type_is_rejected_and_deleted(self, get_r2_client):
        self.client.force_login(self.host)
        r2 = get_r2_client.return_value
        r2.head_object.return_value = {
            "ContentLength": 1024,
            "ContentType": "text/plain",
        }
        object_key = self.object_key()
        response = self.confirm(object_key=object_key)
        self.assertEqual(response.status_code, 400)
        r2.delete_object.assert_called_once_with(
            Bucket=settings.R2_BUCKET_NAME,
            Key=object_key,
        )

    @patch("eventos.views.get_r2_client")
    def test_object_key_outside_expected_prefix_is_rejected_before_head(
        self,
        get_r2_client,
    ):
        self.client.force_login(self.host)
        response = self.confirm(
            object_key="eventos/otro-evento/personalizacion/portada/test.png"
        )
        self.assertEqual(response.status_code, 400)
        get_r2_client.assert_not_called()

    @patch("eventos.views.get_r2_client")
    def test_rejected_cover_does_not_replace_previous_key(self, get_r2_client):
        self.client.force_login(self.host)
        self.event.imagen_portada_key = "eventos/previous-cover.png"
        self.event.save(update_fields=["imagen_portada_key"])
        get_r2_client.return_value.head_object.return_value = {
            "ContentLength": 0,
            "ContentType": "image/png",
        }
        response = self.confirm("portada")
        self.assertEqual(response.status_code, 400)
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.imagen_portada_key,
            "eventos/previous-cover.png",
        )

    @patch("eventos.views.get_r2_client")
    def test_rejected_logo_does_not_replace_previous_key(self, get_r2_client):
        self.client.force_login(self.host)
        self.event.logo_key = "eventos/previous-logo.png"
        self.event.save(update_fields=["logo_key"])
        get_r2_client.return_value.head_object.return_value = {
            "ContentLength": MAX_TAMANO_PERSONALIZACION + 1,
            "ContentType": "image/png",
        }
        response = self.confirm("logo")
        self.assertEqual(response.status_code, 400)
        self.event.refresh_from_db()
        self.assertEqual(self.event.logo_key, "eventos/previous-logo.png")

    @patch("eventos.views.get_r2_client")
    def test_unassigned_host_cannot_confirm_personalization(
        self,
        get_r2_client,
    ):
        self.client.force_login(self.other_host)
        response = self.confirm()
        self.assertEqual(response.status_code, 404)
        get_r2_client.assert_not_called()
