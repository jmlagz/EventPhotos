import io
from unittest.mock import Mock, patch

from PIL import Image
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from .models import SlideshowPromo
from .services.slideshow_promos import (
    SlideshowPromoStorageError,
    reemplazar_imagen_promo,
    validar_imagen_promo,
)


def image_upload(image_format="JPEG", size=(1280, 720), *, name="promo.bin"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color="navy").save(buffer, format=image_format)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="text/plain")


class SlideshowPromoModelTests(TestCase):
    def test_tipo_is_unique(self):
        SlideshowPromo.objects.create(
            tipo=SlideshowPromo.Tipo.EMOTIVA,
            titulo_interno="Uno",
            orden=1,
        )
        with self.assertRaises(IntegrityError):
            SlideshowPromo.objects.create(
                tipo=SlideshowPromo.Tipo.EMOTIVA,
                titulo_interno="Dos",
                orden=2,
            )

    def test_orden_must_be_between_one_and_three(self):
        for orden in (0, 4):
            with self.subTest(orden=orden):
                promo = SlideshowPromo(
                    tipo=SlideshowPromo.Tipo.EMOTIVA,
                    titulo_interno="Promo",
                    orden=orden,
                )
                with self.assertRaises(ValidationError):
                    promo.full_clean()

    def test_active_promo_requires_image(self):
        promo = SlideshowPromo(
            tipo=SlideshowPromo.Tipo.EMOTIVA,
            titulo_interno="Promo",
            orden=1,
            activa=True,
        )
        with self.assertRaises(ValidationError):
            promo.full_clean()

    def test_inactive_promo_can_have_no_image(self):
        promo = SlideshowPromo(
            tipo=SlideshowPromo.Tipo.EMOTIVA,
            titulo_interno="Promo",
            orden=1,
            activa=False,
        )
        promo.full_clean()


class SlideshowPromoValidationTests(TestCase):
    def test_valid_jpeg_png_and_webp_are_accepted_by_real_format(self):
        for image_format in ("JPEG", "PNG", "WEBP"):
            with self.subTest(image_format=image_format):
                result = validar_imagen_promo(image_upload(image_format))
                self.assertEqual(result["extension"], image_format.lower().replace("jpeg", "jpg"))

    def test_validation_restores_uploaded_file_pointer(self):
        uploaded = image_upload()

        validar_imagen_promo(uploaded)

        self.assertEqual(uploaded.tell(), 0)

    def test_rejects_corrupt_svg_and_gif(self):
        cases = (
            SimpleUploadedFile("corrupt.jpg", b"not an image"),
            SimpleUploadedFile("promo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", content_type="image/jpeg"),
            image_upload("GIF"),
        )
        for uploaded in cases:
            with self.subTest(name=uploaded.name):
                with self.assertRaises(ValidationError):
                    validar_imagen_promo(uploaded)

    def test_rejects_images_outside_dimension_limits(self):
        for size in ((1279, 720), (1280, 719), (3841, 2160), (3840, 2161)):
            with self.subTest(size=size):
                with self.assertRaises(ValidationError):
                    validar_imagen_promo(image_upload(size=size))

    def test_rejects_payload_over_five_mb(self):
        uploaded = SimpleUploadedFile("large.jpg", b"x" * (5 * 1024 * 1024 + 1))
        with self.assertRaises(ValidationError):
            validar_imagen_promo(uploaded)


class SlideshowPromoServiceTests(TestCase):
    def setUp(self):
        self.promo = SlideshowPromo.objects.create(
            tipo=SlideshowPromo.Tipo.EMOTIVA,
            titulo_interno="Promo",
            imagen_key="slideshow-promos/emotiva/old.jpg",
            activa=True,
            orden=1,
        )

    @patch("eventos.services.slideshow_promos.eliminar_objeto")
    @patch("eventos.services.slideshow_promos.get_r2_client")
    def test_upload_replaces_with_versioned_key_and_cleans_old_after_commit(self, get_client, delete):
        client = Mock()
        get_client.return_value = client

        with self.captureOnCommitCallbacks(execute=True):
            reemplazar_imagen_promo(self.promo, image_upload("PNG"))

        self.promo.refresh_from_db()
        self.assertRegex(self.promo.imagen_key, r"^slideshow-promos/emotiva/[0-9a-f]{32}\.png$")
        client.put_object.assert_called_once()
        self.assertEqual(client.put_object.call_args.kwargs["ContentType"], "image/png")
        self.assertEqual(delete.call_args.args[0], "slideshow-promos/emotiva/old.jpg")

    @patch("eventos.services.slideshow_promos.get_r2_client")
    def test_upload_failure_keeps_old_key(self, get_client):
        get_client.return_value.put_object.side_effect = RuntimeError("unavailable")

        with self.assertRaises(SlideshowPromoStorageError):
            reemplazar_imagen_promo(self.promo, image_upload())

        self.promo.refresh_from_db()
        self.assertEqual(self.promo.imagen_key, "slideshow-promos/emotiva/old.jpg")

    @patch("eventos.services.slideshow_promos.eliminar_objeto")
    @patch("eventos.services.slideshow_promos.get_r2_client")
    def test_database_failure_cleans_new_object_best_effort(self, get_client, delete):
        get_client.return_value = Mock()
        with patch.object(SlideshowPromo, "save", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                reemplazar_imagen_promo(self.promo, image_upload())

        self.assertEqual(delete.call_count, 1)
        self.assertRegex(delete.call_args.args[0], r"^slideshow-promos/emotiva/[0-9a-f]{32}\.jpg$")

    @patch("eventos.services.slideshow_promos.eliminar_objeto", side_effect=RuntimeError("delete failed"))
    @patch("eventos.services.slideshow_promos.get_r2_client")
    def test_old_cleanup_failure_does_not_break_new_promo(self, get_client, _delete):
        get_client.return_value = Mock()

        reemplazar_imagen_promo(self.promo, image_upload())

        self.promo.refresh_from_db()
        self.assertTrue(self.promo.imagen_key.startswith("slideshow-promos/emotiva/"))


class SlideshowPromoAdminTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser("root", "root@example.com", "password")
        self.staff = User.objects.create_user("staff", is_staff=True, password="password")
        self.host = User.objects.create_user("host", password="password")
        self.promo = SlideshowPromo.objects.create(
            tipo=SlideshowPromo.Tipo.EMOTIVA,
            titulo_interno="Privada",
            imagen_key="slideshow-promos/emotiva/private.jpg",
            activa=True,
            orden=1,
        )
        self.changelist_url = reverse("admin:eventos_slideshowpromo_changelist")
        self.change_url = reverse("admin:eventos_slideshowpromo_change", args=[self.promo.pk])

    def test_only_superuser_can_access_admin_model(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(self.changelist_url).status_code, 200)

        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.changelist_url).status_code, 403)

        self.client.force_login(self.host)
        response = self.client.get(self.changelist_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])

    @patch("eventos.admin.generar_preview_promo", return_value="https://signed.test/promo")
    def test_admin_uses_signed_preview_without_exposing_key_or_delete(self, _preview):
        self.client.force_login(self.superuser)
        response = self.client.get(self.change_url)

        self.assertContains(response, "https://signed.test/promo")
        self.assertNotContains(response, self.promo.imagen_key)
        self.assertNotContains(response, "delete/")

    @patch("eventos.services.slideshow_promos.get_r2_client")
    def test_superuser_can_create_active_promo_with_admin_upload(self, get_client):
        self.client.force_login(self.superuser)
        add_url = reverse("admin:eventos_slideshowpromo_add")

        response = self.client.post(
            add_url,
            {
                "tipo": SlideshowPromo.Tipo.FOTOGRAFO,
                "titulo_interno": "Nueva promo",
                "activa": "on",
                "orden": 2,
                "imagen": image_upload("WEBP"),
                "_save": "Guardar",
            },
        )

        self.assertEqual(response.status_code, 302)
        promo = SlideshowPromo.objects.get(tipo=SlideshowPromo.Tipo.FOTOGRAFO)
        self.assertTrue(promo.activa)
        self.assertRegex(promo.imagen_key, r"^slideshow-promos/fotografo/[0-9a-f]{32}\.webp$")
        get_client.return_value.put_object.assert_called_once()
