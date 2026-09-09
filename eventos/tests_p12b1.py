from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .models import Evento, Mesa


class P12B1TableEntryVisualIdentityTests(TestCase):
    def setUp(self):
        self.event = Evento.objects.create(
            nombre="Evento QR P1.2B.1",
            fecha=date(2026, 10, 24),
            estado=Evento.Estado.ACTIVE,
        )
        self.table = Mesa.objects.create(
            evento=self.event,
            numero=1,
            nombre="Familia",
        )
        self.entry_url = reverse(
            "mesa_publica",
            args=[self.event.slug, self.table.token],
        )
        self.upload_url = reverse(
            "subir_fotos",
            args=[self.event.slug, self.table.token],
        )

    def authorize_upload(self):
        session = self.client.session
        session["mesa_id"] = self.table.id
        session["evento_id"] = self.event.id
        session["instrucciones_aceptadas"] = True
        session.save()

    @patch("eventos.views.generar_url_lectura")
    def test_table_entry_renders_current_r2_cover_and_logo(self, generate_url):
        cover_key = "eventos/evento-qr/personalizacion/portada/private.webp"
        logo_key = "eventos/evento-qr/personalizacion/logo/private.webp"
        self.event.imagen_portada_key = cover_key
        self.event.logo_key = logo_key
        self.event.save(update_fields=["imagen_portada_key", "logo_key"])
        generate_url.side_effect = (
            lambda key: {
                cover_key: "https://read.test/signed-cover",
                logo_key: "https://read.test/signed-logo",
            }[key]
        )

        response = self.client.get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "eventos/mesa_publica.html")
        self.assertContains(response, "https://read.test/signed-cover")
        self.assertContains(response, "https://read.test/signed-logo")
        self.assertContains(response, "hero hero-con-portada")
        self.assertContains(response, 'class="hero-portada"')
        self.assertContains(response, 'class="hero-logo"')
        self.assertNotContains(response, cover_key)
        self.assertNotContains(response, logo_key)
        self.assertEqual(generate_url.call_count, 2)

    @patch("eventos.views.generar_url_lectura")
    def test_table_entry_without_current_cover_uses_clean_fallback(
        self,
        generate_url,
    ):
        response = self.client.get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="hero"')
        self.assertNotContains(response, "hero hero-con-portada")
        self.assertNotContains(response, 'class="hero-portada"')
        self.assertContains(response, 'class="hero-icon"')
        generate_url.assert_not_called()

    @patch("eventos.views.generar_url_lectura")
    def test_table_entry_does_not_use_legacy_cover_imagefield(self, generate_url):
        self.event.imagen_portada = "eventos/portadas/legacy-private.jpg"
        self.event.save(update_fields=["imagen_portada"])

        response = self.client.get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "legacy-private.jpg")
        self.assertNotContains(response, "hero hero-con-portada")
        self.assertNotContains(response, 'class="hero-portada"')
        self.assertContains(response, 'class="hero-icon"')
        generate_url.assert_not_called()

    @patch("eventos.views.generar_url_lectura")
    def test_existing_consent_flow_is_unchanged(self, generate_url):
        response = self.client.post(self.entry_url, {"acepto": "on"})

        self.assertRedirects(
            response,
            reverse(
                "subir_fotos",
                args=[self.event.slug, self.table.token],
            ),
            fetch_redirect_response=False,
        )
        session = self.client.session
        self.assertEqual(session["mesa_id"], self.table.id)
        self.assertEqual(session["evento_id"], self.event.id)
        self.assertTrue(session["instrucciones_aceptadas"])
        generate_url.assert_not_called()

    @patch("eventos.views.generar_url_lectura")
    def test_closed_event_still_uses_existing_closed_screen(self, generate_url):
        self.event.estado = Evento.Estado.CLOSED
        self.event.imagen_portada_key = "eventos/evento-qr/cover.webp"
        self.event.save(update_fields=["estado", "imagen_portada_key"])

        response = self.client.get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "eventos/evento_cerrado.html")
        self.assertNotContains(response, 'class="hero-portada"')
        generate_url.assert_not_called()

    def test_invalid_or_inactive_table_access_still_returns_404(self):
        invalid_url = reverse(
            "mesa_publica",
            args=[self.event.slug, "invalid-token"],
        )
        self.assertEqual(self.client.get(invalid_url).status_code, 404)

        self.table.activa = False
        self.table.save(update_fields=["activa"])
        self.assertEqual(self.client.get(self.entry_url).status_code, 404)

    @patch("eventos.views.generar_url_lectura")
    def test_upload_screen_renders_same_r2_cover_and_logo(self, generate_url):
        cover_key = "eventos/evento-qr/personalizacion/portada/upload.webp"
        logo_key = "eventos/evento-qr/personalizacion/logo/upload.webp"
        self.event.imagen_portada_key = cover_key
        self.event.logo_key = logo_key
        self.event.save(update_fields=["imagen_portada_key", "logo_key"])
        generate_url.side_effect = (
            lambda key: {
                cover_key: "https://read.test/upload-cover",
                logo_key: "https://read.test/upload-logo",
            }[key]
        )
        self.authorize_upload()

        response = self.client.get(self.upload_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "eventos/subir_fotos.html")
        self.assertTemplateUsed(response, "eventos/_mesa_identidad_hero.html")
        self.assertContains(response, "https://read.test/upload-cover")
        self.assertContains(response, "https://read.test/upload-logo")
        self.assertContains(response, "hero hero-con-portada")
        self.assertContains(response, 'class="hero-portada"')
        self.assertContains(response, 'class="hero-logo"')
        self.assertNotContains(response, cover_key)
        self.assertNotContains(response, logo_key)

    @patch("eventos.views.generar_url_lectura")
    def test_upload_screen_without_current_identity_uses_fallback(
        self,
        generate_url,
    ):
        self.authorize_upload()

        response = self.client.get(self.upload_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="hero"')
        self.assertNotContains(response, "hero hero-con-portada")
        self.assertNotContains(response, 'class="hero-portada"')
        self.assertContains(response, 'class="hero-icon"')
        generate_url.assert_not_called()

    @patch("eventos.views.generar_url_lectura")
    def test_upload_screen_does_not_use_legacy_imagefields(self, generate_url):
        self.event.imagen_portada = "eventos/portadas/legacy-upload.jpg"
        self.event.logo = "eventos/logos/legacy-upload.jpg"
        self.event.save(update_fields=["imagen_portada", "logo"])
        self.authorize_upload()

        response = self.client.get(self.upload_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "legacy-upload.jpg")
        self.assertNotContains(response, "hero hero-con-portada")
        self.assertContains(response, 'class="hero-icon"')
        generate_url.assert_not_called()

    @patch("eventos.views.generar_url_lectura")
    def test_upload_route_without_consent_still_redirects_to_entry(
        self,
        generate_url,
    ):
        response = self.client.get(self.upload_url)

        self.assertRedirects(
            response,
            self.entry_url,
            fetch_redirect_response=False,
        )
        generate_url.assert_not_called()
