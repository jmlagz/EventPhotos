from datetime import date

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Evento, Mesa


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class PublicFooterBrandingTests(TestCase):
    old_branding = (
        "creado por LuisArt",
        "jmlagz@gmail.com",
        "luisart@lotus-nest.com",
    )

    def setUp(self):
        self.host = User.objects.create_user(
            username="footer-host@example.com",
            password="test-password",
        )
        self.event = Evento.objects.create(
            nombre="Evento footer público",
            fecha=date(2026, 10, 31),
            estado=Evento.Estado.ACTIVE,
        )
        self.event.anfitriones.add(self.host)
        self.table = Mesa.objects.create(evento=self.event, numero=1)
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

    def assert_new_footer(self, content):
        if hasattr(content, "content"):
            content = content.content.decode()

        self.assertIn('href="https://eventphotos.com.mx/"', content)
        self.assertIn(">EventPhotos</a>", content)
        self.assertNotIn(">https://eventphotos.com.mx</a>", content)
        self.assertIn('href="https://lotus-nest.com/"', content)
        self.assertRegex(
            content,
            r'href="https://lotus-nest\.com/"\s+'
            r'target="_blank"\s+rel="noopener"',
        )
        self.assertIn('href="mailto:contacto@eventphotos.com.mx"', content)
        self.assertIn(">contacto@eventphotos.com.mx</a>", content)

        for old_text in self.old_branding:
            with self.subTest(old_text=old_text):
                self.assertNotIn(old_text, content)

    def test_required_public_pages_render_new_footer(self):
        entry_response = self.client.get(self.entry_url)
        self.assertEqual(entry_response.status_code, 200)
        self.assert_new_footer(entry_response)

        self.authorize_upload()
        urls = (
            self.upload_url,
            reverse("evento_publico", args=[self.event.slug]),
            reverse("album_publico", args=[self.event.slug]),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assert_new_footer(response)

    def test_home_and_closed_event_render_new_footer_and_contact(self):
        home_response = self.client.get(reverse("home"))
        self.assertEqual(home_response.status_code, 200)
        self.assert_new_footer(home_response)

        self.event.estado = Evento.Estado.CLOSED
        self.event.save(update_fields=["estado"])
        closed_response = self.client.get(self.entry_url)
        self.assertEqual(closed_response.status_code, 200)
        self.assertTemplateUsed(closed_response, "eventos/evento_cerrado.html")
        self.assert_new_footer(closed_response)
        self.assertContains(
            closed_response,
            'mailto:contacto@eventphotos.com.mx',
            count=2,
        )

    def test_dashboard_surfaces_render_new_footer(self):
        admin_username = "footer-admin@example.com"
        admin = User.objects.create_superuser(
            username=admin_username,
            password="test-password",
        )
        self.client.force_login(admin)

        urls = (
            reverse("dashboard"),
            reverse("dashboard_evento", args=[self.event.slug]),
            reverse("mesas_dashboard", args=[self.event.slug]),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assert_new_footer(response)
                self.assertContains(response, admin_username)

    def test_dashboard_authentication_redirect_is_unchanged(self):
        urls = (
            reverse("dashboard"),
            reverse("dashboard_evento", args=[self.event.slug]),
            reverse("mesas_dashboard", args=[self.event.slug]),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login_anfitrion"), response.url)

    def test_related_legacy_public_templates_use_new_footer(self):
        context = {"evento": self.event, "mesa": self.table}

        for template_name in (
            "eventos/instrucciones.html",
            "eventos/verificar_acceso.html",
        ):
            with self.subTest(template_name=template_name):
                content = render_to_string(template_name, context)
                self.assert_new_footer(content)

    def test_guest_facing_qr_prints_use_compact_new_footer(self):
        self.client.force_login(self.host)
        urls = (
            reverse(
                "imprimir_qr_mesa",
                args=[self.event.slug, self.table.id],
            ),
            reverse("imprimir_qrs_mesas", args=[self.event.slug]),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assert_new_footer(response)
