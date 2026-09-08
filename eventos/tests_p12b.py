from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import EventoForm
from .models import Evento, Mesa


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class P12BEventCreationTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin-p12b@example.com",
            email="admin-p12b@example.com",
            password="test-password",
        )
        self.client.force_login(self.superuser)

    def test_creation_form_excludes_options_without_current_behavior(self):
        response = self.client.get(reverse("crear_evento"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["form"].fields),
            [
                "nombre",
                "tipo",
                "fecha",
                "descripcion",
                "mensaje_bienvenida",
            ],
        )

        for field_name in (
            "plantilla",
            "permitir_videos",
            "moderacion_activa",
            "color_principal",
            "color_secundario",
        ):
            with self.subTest(field_name=field_name):
                self.assertNotContains(response, f'name="{field_name}"')

    def test_creation_preserves_model_defaults_for_excluded_fields(self):
        response = self.client.post(
            reverse("crear_evento"),
            {
                "nombre": "Evento defaults P1.2B",
                "tipo": Evento.Tipo.BODA,
                "fecha": "2026-10-17",
                "descripcion": "Descripción",
                "mensaje_bienvenida": "Bienvenidos",
            },
        )

        self.assertEqual(response.status_code, 302)
        event = Evento.objects.get(nombre="Evento defaults P1.2B")
        self.assertEqual(event.plantilla, "default")
        self.assertFalse(event.permitir_videos)
        self.assertTrue(event.moderacion_activa)
        self.assertEqual(event.color_principal, "#000000")
        self.assertEqual(event.color_secundario, "#FFFFFF")

    def test_model_form_ignores_posted_values_for_excluded_fields(self):
        form = EventoForm(
            data={
                "nombre": "Evento campos excluidos",
                "tipo": Evento.Tipo.OTRO,
                "fecha": "2026-10-18",
                "descripcion": "",
                "mensaje_bienvenida": "",
                "plantilla": "inyectada",
                "permitir_videos": "on",
                "moderacion_activa": "",
                "color_principal": "#123456",
                "color_secundario": "#654321",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        event = form.save()
        self.assertEqual(event.plantilla, "default")
        self.assertFalse(event.permitir_videos)
        self.assertTrue(event.moderacion_activa)
        self.assertEqual(event.color_principal, "#000000")
        self.assertEqual(event.color_secundario, "#FFFFFF")


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class P12BPublicAndHostUXTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            username="host-p12b@example.com",
            password="test-password",
        )
        self.event = Evento.objects.create(
            nombre="Evento UX P1.2B",
            fecha=date(2026, 10, 17),
            estado=Evento.Estado.ACTIVE,
        )
        self.event.anfitriones.add(self.host)
        self.table = Mesa.objects.create(evento=self.event, numero=1)

    @patch(
        "eventos.views.generar_url_lectura",
        return_value="https://read.test/eventos/portada.webp",
    )
    def test_public_landing_renders_current_r2_cover_without_legacy_imagefield(
        self,
        _generate_url,
    ):
        self.event.imagen_portada_key = (
            "eventos/evento-ux-p12b/personalizacion/portada/current.webp"
        )
        self.event.save(update_fields=["imagen_portada_key"])

        response = self.client.get(
            reverse("evento_publico", args=[self.event.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(bool(self.event.imagen_portada))
        self.assertContains(response, "https://read.test/eventos/portada.webp")

    def test_public_landing_omits_cover_when_current_r2_key_is_missing(self):
        response = self.client.get(
            reverse("evento_publico", args=[self.event.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="portada"')

    def test_access_code_is_hidden_from_table_dashboard_and_print_views(self):
        self.client.force_login(self.host)
        urls = (
            reverse("mesas_dashboard", args=[self.event.slug]),
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
                self.assertNotContains(response, self.table.codigo_acceso)
                self.assertNotContains(response, "Código de acceso")
                self.assertNotContains(response, "si el sistema lo solicita")

    def test_dashboard_links_to_album_only_when_public_album_is_available(self):
        self.client.force_login(self.host)
        dashboard_url = reverse("dashboard_evento", args=[self.event.slug])
        album_url = reverse("album_publico", args=[self.event.slug])

        response = self.client.get(dashboard_url)
        self.assertContains(response, f'href="{album_url}"')
        self.assertContains(response, "Ver álbum del evento")

        unavailable_cases = (
            (Evento.Estado.DRAFT, None),
            (Evento.Estado.ARCHIVED, None),
            (Evento.Estado.ACTIVE, timezone.now() - timedelta(seconds=1)),
        )

        for state, available_until in unavailable_cases:
            with self.subTest(state=state, available_until=available_until):
                self.event.estado = state
                self.event.available_until = available_until
                self.event.save(update_fields=["estado", "available_until"])

                response = self.client.get(dashboard_url)
                self.assertNotContains(response, f'href="{album_url}"')
                self.assertContains(response, "Álbum no disponible actualmente")
                self.assertContains(
                    response,
                    "El estado o periodo de disponibilidad del evento no permite abrir el álbum.",
                )

    def test_lifecycle_actions_explain_and_request_confirmation(self):
        self.client.force_login(self.host)
        dashboard_url = reverse("dashboard_evento", args=[self.event.slug])
        expectations = (
            (
                Evento.Estado.DRAFT,
                "Activar el evento permitirá el acceso público y la carga de fotos",
                "Al activarlo, los invitados podrán acceder",
            ),
            (
                Evento.Estado.ACTIVE,
                "Cerrar el evento bloqueará inmediatamente nuevas cargas",
                "Cerrar bloquea inmediatamente nuevas cargas de fotos",
            ),
            (
                Evento.Estado.CLOSED,
                "Reabrir el evento no extenderá las fechas de carga ni del álbum",
                "Reabrir no modifica ni extiende las fechas configuradas",
            ),
        )

        for state, confirmation, consequence in expectations:
            with self.subTest(state=state):
                self.event.estado = state
                self.event.save(update_fields=["estado"])

                response = self.client.get(dashboard_url)
                self.assertContains(response, confirmation)
                self.assertContains(response, consequence)

    def test_reopening_does_not_recalculate_or_extend_dates(self):
        event_timezone = ZoneInfo("America/Mexico_City")
        planned_end = datetime(2026, 10, 17, 23, 0, tzinfo=event_timezone)
        upload_until = planned_end + timedelta(hours=48)
        available_until = datetime(
            2027,
            4,
            17,
            23,
            0,
            tzinfo=event_timezone,
        )
        self.event.estado = Evento.Estado.CLOSED
        self.event.timezone = "America/Mexico_City"
        self.event.fin_planeado = planned_end
        self.event.upload_until = upload_until
        self.event.available_until = available_until
        self.event.save()
        self.client.force_login(self.host)

        response = self.client.post(
            reverse("reabrir_evento", args=[self.event.slug])
        )

        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.estado, Evento.Estado.ACTIVE)
        self.assertEqual(self.event.fin_planeado, planned_end)
        self.assertEqual(self.event.upload_until, upload_until)
        self.assertEqual(self.event.available_until, available_until)
