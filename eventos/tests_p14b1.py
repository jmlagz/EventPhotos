from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from .models import Evento, Mesa


class DraftTableEntryTests(TestCase):
    def setUp(self):
        self.event = Evento.objects.create(
            nombre="Evento borrador privado",
            fecha=date(2026, 10, 24),
            estado=Evento.Estado.DRAFT,
        )
        self.table = Mesa.objects.create(
            evento=self.event,
            numero=1,
            nombre="Mesa privada",
        )
        self.entry_url = reverse(
            "mesa_publica",
            args=[self.event.slug, self.table.token],
        )
        self.upload_url = reverse(
            "subir_fotos",
            args=[self.event.slug, self.table.token],
        )
        self.dashboard_url = reverse(
            "dashboard_evento",
            args=[self.event.slug],
        )

    def assert_session_not_authorized(self, client=None):
        session = (client or self.client).session
        for key in (
            "mesa_id",
            "evento_id",
            "instrucciones_aceptadas",
            "uploader_token",
        ):
            self.assertNotIn(key, session)

    def test_draft_with_valid_token_returns_friendly_page(self):
        response = self.client.get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "eventos/evento_no_abierto.html")
        self.assertContains(response, "Este evento todavía no está abierto")
        self.assertContains(
            response,
            "Podrás compartir tus fotos cuando el anfitrión active el evento.",
        )
        self.assertNotContains(response, 'name="acepto"')
        self.assertNotContains(response, self.table.token)
        self.assertNotContains(response, self.table.nombre)
        self.assertNotContains(response, self.event.nombre)

    def test_draft_get_does_not_authorize_table_session(self):
        self.client.get(self.entry_url)

        self.assert_session_not_authorized()

    def test_draft_post_cannot_accept_terms_or_enable_upload(self):
        response = self.client.post(self.entry_url, {"acepto": "on"})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "eventos/evento_no_abierto.html")
        self.assert_session_not_authorized()
        self.assertEqual(self.client.get(self.upload_url).status_code, 404)

    def test_active_event_keeps_existing_consent_flow(self):
        self.event.estado = Evento.Estado.ACTIVE
        self.event.save(update_fields=["estado"])

        entry = self.client.get(self.entry_url)

        self.assertEqual(entry.status_code, 200)
        self.assertTemplateUsed(entry, "eventos/mesa_publica.html")
        session = self.client.session
        self.assertEqual(session["mesa_id"], self.table.id)
        self.assertEqual(session["evento_id"], self.event.id)
        self.assertNotIn("instrucciones_aceptadas", session)

        consent = self.client.post(self.entry_url, {"acepto": "on"})

        self.assertRedirects(
            consent,
            self.upload_url,
            fetch_redirect_response=False,
        )
        self.assertTrue(self.client.session["instrucciones_aceptadas"])

    def test_closed_event_keeps_existing_closed_screen(self):
        self.event.estado = Evento.Estado.CLOSED
        self.event.save(update_fields=["estado"])

        response = self.client.get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "eventos/evento_cerrado.html")
        self.assert_session_not_authorized()

    def test_invalid_inactive_or_other_event_table_token_returns_404(self):
        other_event = Evento.objects.create(
            nombre="Otro evento",
            fecha=date(2026, 10, 25),
            estado=Evento.Estado.DRAFT,
        )
        other_table = Mesa.objects.create(
            evento=other_event,
            numero=1,
        )
        wrong_event_url = reverse(
            "mesa_publica",
            args=[self.event.slug, other_table.token],
        )
        invalid_url = reverse(
            "mesa_publica",
            args=[self.event.slug, "invalid-token"],
        )

        self.assertEqual(self.client.get(invalid_url).status_code, 404)
        self.assertEqual(self.client.get(wrong_event_url).status_code, 404)

        self.table.activa = False
        self.table.save(update_fields=["activa"])
        self.assertEqual(self.client.get(self.entry_url).status_code, 404)

    def test_event_host_sees_dashboard_cta(self):
        host = User.objects.create_user("draft-host@example.com")
        self.event.anfitriones.add(host)
        self.client.force_login(host)

        response = self.client.get(self.entry_url)

        self.assertContains(response, "Volver al dashboard del evento")
        self.assertContains(response, f'href="{self.dashboard_url}"')
        self.assert_session_not_authorized()

    def test_authenticated_user_from_another_event_does_not_see_cta(self):
        other_user = User.objects.create_user("other-draft-host@example.com")
        other_event = Evento.objects.create(
            nombre="Evento del usuario ajeno",
            fecha=date(2026, 10, 26),
            estado=Evento.Estado.DRAFT,
        )
        other_event.anfitriones.add(other_user)
        self.client.force_login(other_user)

        response = self.client.get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Volver al dashboard del evento")
        self.assertNotContains(response, self.dashboard_url)
        self.assert_session_not_authorized()

    def test_superuser_sees_dashboard_cta(self):
        superuser = User.objects.create_superuser(
            "draft-admin@example.com",
            "draft-admin@example.com",
            "password",
        )
        self.client.force_login(superuser)

        response = self.client.get(self.entry_url)

        self.assertContains(response, "Volver al dashboard del evento")
        self.assertContains(response, f'href="{self.dashboard_url}"')
        self.assert_session_not_authorized()

    def test_anonymous_guest_does_not_see_dashboard_cta(self):
        response = Client().get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Volver al dashboard del evento")
        self.assertNotContains(response, self.dashboard_url)
